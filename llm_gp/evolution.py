from __future__ import annotations

import csv
import random
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from .island import Island
from .config import AppConfig
from .database import ExperimentDatabase
from .evaluator import Evaluator, MockEvaluator, RosGazeboEvaluator
from .fitness import calculate_fitness, compute_generation_stats
from .models import EvaluationResult, GenerationSummary, Individual, MutationContext
from .operators import (
    CrossoverOperator,
    IdFactory,
    MutationOperator,
    build_crossover_operator,
    build_mutation_operator,
    initial_cpp_source,
    initial_source,
)
from .selection import elite_sort_key, select_parent_pair, select_survivors
from .validation import validate_individual


class EvolutionEngine:
    def __init__(
        self,
        config: AppConfig,# 実験設定を受け取ります。
        database: ExperimentDatabase | None = None,# データベースを外部から渡せるようにしています。
        evaluator: Evaluator | None = None,# 評価器を外部から渡せるようにしています。
        crossover: CrossoverOperator | None = None,#交叉処理を外部から指定できます。
        mutation: MutationOperator | None = None,# 突然変異処理を外部から指定できます。
    ) -> None:
        self.config = config# 受け取った設定を、このクラス内部で使えるように保存します。
        self.rng = random.Random(config.random_seed)# 設定された乱数シードを使って、専用の乱数生成器を作ります。
        self.ids = IdFactory()# 個体IDを作る IdFactory を生成します。
        self.database = database or ExperimentDatabase(config.database_path, reset=True)# 外部から database が渡されていれば、それを使用します。渡されていなければ、新しいデータベースを作ります。
        self._owns_database = database is None# データベースをこのクラス自身が作ったかどうかを記録します。
        self.cpp_mode = config.evaluation.evaluator_type == "ros_gazebo"# 評価方法が ros_gazebo か確認します。
        self.evaluator = evaluator or self._build_evaluator()# 外部から評価器が渡されていれば、それを使います。
        self.islands = [
            Island(name, config.random_seed + index * 100_003)# 島の名前と、その島専用の乱数シードを使って Island を作ります。
            for index, name in enumerate(config.islands)# 設定に登録されている島を順番に処理します。
        ]
        self.crossover = crossover or build_crossover_operator(# 外部から交叉オペレータが渡されていれば、それを使います。渡されていなければLLMベースの交叉オペレータを自動作成します(APIキーがなければ決定論的な重み平均にフォールバック)。
            config.llm.provider,
            config.llm.model,
            self.ids,
            config.source_directory,
            cpp_mode=self.cpp_mode,
            max_retries=config.validation.max_repair_attempts,
            fallback_to_mock=config.llm.fallback_to_mock,
            fitness_settings=config.fitness,
            validation_settings=config.validation,
            connection_max_retries=config.llm.connection_max_retries,
            connection_retry_base_seconds=config.llm.connection_retry_base_seconds,
            connection_retry_max_seconds=config.llm.connection_retry_max_seconds,
        )
        if mutation is not None:# 外部から突然変異オペレータが渡されたか確認します。
            self.mutations = {island.name: mutation for island in self.islands}# 渡された同じ突然変異オペレータを、すべての島に割り当てます。
        else:
            self.mutations = {
                island.name: build_mutation_operator(# 島の名前をキーにして、その島専用の突然変異オペレータを作ります。
                    config.llm.provider,
                    config.llm.model,
                    self.ids,
                    island.rng,
                    cpp_mode=self.cpp_mode,
                    max_retries=config.validation.max_repair_attempts,
                    fallback_to_mock=config.llm.fallback_to_mock,
                    fitness_settings=config.fitness,
                    validation_settings=config.validation,
                    connection_max_retries=config.llm.connection_max_retries,
                    connection_retry_base_seconds=config.llm.connection_retry_base_seconds,
                    connection_retry_max_seconds=config.llm.connection_retry_max_seconds,
                )
                for island in self.islands
            }# 島ごとの突然変異オペレータを辞書として作成します。
        self.mutation = self.mutations[self.islands[0].name]# 最初の島の突然変異オペレータを、self.mutation にも保存します。
        self.total_generated = 0# これまで生成した子個体の総数を0で初期化します。
        self.total_migrations = 0# これまで実行した移住の総数を0で初期化します。
        self.summaries: list[GenerationSummary] = []# 各世代の集計結果を保存する空のリストを作ります。
        # ros_gazebo.additional_workspacesが設定されていれば、島の数だけGazebo評価を
        # 同時実行できるようワーカースレッドプールを用意する(未設定なら1=完全に逐次、
        # 従来と全く同じ動作)。実際に並列化されるのは評価(RosGazeboEvaluator.evaluate)
        # だけで、交叉/変異のコード生成は常にメインスレッドで逐次のまま。
        ros_gazebo_settings = config.evaluation.ros_gazebo
        self._workspace_pool_size = (
            1 + len(ros_gazebo_settings.additional_workspaces)
            if ros_gazebo_settings is not None
            else 1
        )
        self._executor: ThreadPoolExecutor | None = (
            ThreadPoolExecutor(max_workers=self._workspace_pool_size)
            if self._workspace_pool_size > 1
            else None
        )

    def _build_evaluator(self) -> Evaluator:# Evaluator型の評価器を作って返すメソッド
        if self.config.evaluation.evaluator_type == "mock":# 評価方式が mock か確認します。(プログラムが正しく動くか高速に確認するためのダミー)
            return MockEvaluator(
                self.rng,
                self.config.evaluation.fixed_goal,
                self.config.evaluation.repetitions_per_goal,
            )# Mock評価器を作成し、そのまま返します。
        if self.config.evaluation.evaluator_type == "ros_gazebo":# 評価方式が ros_gazebo か確認します。
            settings = self.config.evaluation.ros_gazebo
            if settings is None:
                raise ValueError("ROS/Gazebo settings are required")
            return RosGazeboEvaluator(
                settings=settings,# ROS/Gazeboの詳細設定を渡します。
                fixed_goal=self.config.evaluation.fixed_goal,# ロボットが向かう固定ゴールを渡します。
                timeout_seconds=self.config.evaluation.timeout_seconds,# 1回の評価を何秒で打ち切るか指定します。
                repetitions=self.config.evaluation.repetitions_per_goal,# 同じ個体を何回評価するか指定します。
                build_timeout_seconds=self.config.validation.build_timeout_seconds,# C++コードのビルドに対する制限時間を指定します。
                log_directory=self.config.database_path.parent / "ros_logs",# ROS/Gazeboのログ保存先を指定します。
                project_root=Path(__file__).parents[1],
                rng=self.rng,# ゴールのランダムサンプリングに使う乱数生成器(実験全体のシードに紐づく)。
                goal_x_range=self.config.evaluation.goal_x_range,# 設定されていればゴールxをこの範囲からランダムに選びます。
                goal_y_range=self.config.evaluation.goal_y_range,# 設定されていればゴールyをこの範囲からランダムに選びます。
            )# ROS/Gazebo用の評価器を作成して返します。
        raise ValueError(f"Unsupported evaluator_type: {self.config.evaluation.evaluator_type}")# mock でも ros_gazebo でもない値が指定された場合、エラーにします。

    def initialize(self) -> None:# 各島の初期集団を生成し、評価するメソッドです。
        population_size = self.config.evolution.population_size_per_island# 1つの島に配置する個体数を設定から取得します。
        populations: dict[str, list[Individual]] = {}# 島名ごとの初期個体リスト(評価前に全島分作ってからまとめて評価するため)。
        for island_index, island in enumerate(self.islands):# すべての島を順番に処理します。
            population: list[Individual] = []# 設定された個体数だけ繰り返します。
            for member_index in range(population_size):
                individual_id = self.ids.next()# 新しい個体IDを1つ作ります。
                if self.cpp_mode:#ROS/Gazeboを使うC++モードか確認します。
                    settings = self.config.evaluation.ros_gazebo# ROS/Gazeboの設定を取得します。
                    assert settings is not None# settings が必ず存在することを確認します。
                    baseline = Path(__file__).parents[1] / settings.candidate_target# 初期個体の基になるRelaxed A*コードのパスを作ります。
                    path = initial_cpp_source(
                        self.config.source_directory, individual_id, baseline
                    )# 初期C++ソースファイルを作成し、その保存先を path に入れます。生成コードの保存先,個体ID,コピー元となる基準コード
                else:
                    path = initial_source(
                        self.config.source_directory, individual_id, island.name
                    )# 保存先、個体ID、島名を渡します。
                individual = Individual(# 初期個体を表す Individual オブジェクトを作成します。
                    individual_id=individual_id,# 先ほど生成した個体IDを設定します。
                    generation=0,# 初期個体なので、世代番号を0にします。
                    current_island=island.name,# 現在所属している島を設定します。
                    origin_island=island.name,# 最初に生まれた島も同じ島として記録します。
                    source_path=str(path),# 個体が持つソースコードのパスを文字列として保存します。
                    parent_ids=[],# 初期個体には親がいないため、空のリストにします。
                    operator_type="initial",# この個体が初期生成された個体であることを記録します。
                    random_seed=(# 実験全体の基本乱数シードです。
                        self.config.random_seed
                        + island_index * 100_003
                        + member_index
                    ),
                    selected_for_next_generation=True,# 初期個体は最初の集団に所属するため、次世代選択済みとして扱います。
                    change_history=["Initialized from the current Relaxed A* baseline"],# 変更履歴に、「現在のRelaxed A*を基準として初期化した」と記録します。
                )
                population.append(individual)# まだ未評価の個体を、現在の島の集団リストへ追加します(評価は全島分集めた後にまとめて行う)。
            populations[island.name] = population# この島の初期集団を後で参照できるよう記録します。

        all_individuals = [
            individual for population in populations.values() for individual in population
        ]# 4島分の初期個体を1つのフラットなリストにまとめ、_process_individualsへ1回で渡します。
        results = self._process_individuals(all_individuals)# 全個体をまとめて検証・評価します(ワーカープール設定時はここで並列実行される。適応度はまだ計算・保存しない)。

        for island in self.islands:# すべての島を順番に処理します。
            island.population = populations[island.name]# 評価済みの初期個体を、島の集団として設定します。
            self._score_individuals(island.population)# その島の初期集団だけを母集団としてmin/maxを求め、適応度を計算します。
            for individual in island.population:# 島に所属する個体を1体ずつ処理します。
                self.database.save_population_membership(island.name, 0, individual)# その個体が、世代0でどの島の集団に所属しているか保存します。
        self._persist_freshly_evaluated(all_individuals, results)# 適応度計算後に、評価結果(適応度込み)を1回だけデータベースへ書き込みます。
        self.database.commit()# ここまでのデータベース変更を確定します。

    def generate_children(self, island: Island, generation: int) -> list[Individual]:# 指定された島で、1組の親から子個体を生成するメソッドです。
        parent1, parent2 = select_parent_pair(# 島の集団から親を2個体選択します。
            island.population, island.rng, self.config.selection.epsilon# 親選択関数へ次を渡します。島の個体集団,島専用の乱数生成器,選択時にゼロ除算などを防ぐ小さな値
        )
        child_c = self.crossover.crossover(parent1, parent2)# 親1と親2を交叉させ、子個体を生成します。
        self._set_child_metadata(
            child_c, island.name, generation, "crossover_only", parent1, parent2# 生成された子に、世代や親などの情報を設定します。
        )

        mutation = self.mutations[island.name]# 現在の島に対応した突然変異オペレータを取得します。
        mutation_parent = island.rng.choice([parent1, parent2])# 親1または親2のどちらかをランダムに選びます。
        child_m = mutation.mutate(
            mutation_parent,
            MutationContext(
                generation,
                island.name,
                self.config.source_directory,# 生成されたソースコードの保存先を渡します。
                "mutation_only",
                ".cpp" if self.cpp_mode else ".py",
            ),
        )
        self._set_child_metadata(
            child_m, island.name, generation, "mutation_only", mutation_parent
        )

        intermediate = self.crossover.crossover(parent1, parent2)# まず親1と親2を交叉させます。
        child_cm = mutation.mutate(# 交叉でできた中間個体に突然変異を適用します。
            intermediate,
            MutationContext(
                generation,
                island.name,
                self.config.source_directory,
                "crossover_and_mutation",
                ".cpp" if self.cpp_mode else ".py",
                # intermediate自体は未評価なので、既知の評価結果を持つ両親を
                # フィードバック用の参照個体としてmutateへ渡します。
                reference_individuals=(parent1, parent2),
            ),
        )
        # mutate()は新しいIndividualを返すため、intermediate(crossoverステップ)の
        # llm_callsを引き継がないと消えてしまう。両ステップ分を合算して保持する。
        child_cm.llm_calls = list(intermediate.llm_calls) + list(child_cm.llm_calls)
        self._set_child_metadata(
            child_cm,
            island.name,
            generation,
            "crossover_and_mutation",
            parent1,
            parent2,
        )
        child_cm.change_history.insert(0, "Relaxed A* crossover intermediate created")
        return [child_c, child_m, child_cm]

    @staticmethod#このメソッドが、self を必要としない静的メソッドであることを示します。
    def _set_child_metadata(
        child: Individual,
        island_name: str,
        generation: int,
        operator_type: str,
        *parents: Individual,
    ) -> None:
        child.generation = generation
        child.current_island = island_name
        child.origin_island = island_name
        child.operator_type = operator_type
        child.parent_ids = [parent.individual_id for parent in parents]
        child.is_elite = False
        child.selected_for_next_generation = False

    def run_generation(self, generation: int) -> GenerationSummary:
        generated = 0
        island_parents: dict[str, list[Individual]] = {}
        island_children: dict[str, list[Individual]] = {}
        all_children: list[Individual] = []
        for island in self.islands:
            parents = list(island.population)
            children: list[Individual] = []
            for _ in range(self.config.evolution.parent_pairs_per_island):
                for child in self.generate_children(island, generation):
                    generated += 1
                    self.total_generated += 1
                    children.append(child)
            island_parents[island.name] = parents
            island_children[island.name] = children
            all_children.extend(children)

        # LLMによる子生成(逐次)がすべて終わってから、評価(重い部分)だけを
        # まとめて1回で処理する。ワーカープール設定時はここで並列実行される。
        # 適応度の計算・保存はまだ行わない(下の島ごとのループでmin/maxを
        # 求めてから行う)。
        results = self._process_individuals(all_children)
        results_by_id = {
            child.individual_id: result for child, result in zip(all_children, results)
        }

        for island in self.islands:
            parents = island_parents[island.name]
            children = island_children[island.name]
            # Rescore parents alongside this generation's children so both
            # are ranked against the same min/max before selection.
            self._score_individuals(parents + children)
            island.population = select_survivors(
                parents + children,
                self.config.evolution.population_size_per_island,
                self.config.evolution.elite_count,
                island.rng,
                self.config.selection.epsilon,
            )
            self._persist_rescored(parents)
            self._persist_freshly_evaluated(
                children, [results_by_id[child.individual_id] for child in children]
            )

        migrations = 0
        if generation % self.config.migration.interval == 0:
            migrations = self.perform_ring_migration(generation)

        for island in self.islands:
            for individual in island.population:
                self.database.update_individual_status(individual)
                self.database.save_population_membership(
                    island.name, generation, individual
                )
        summary = self._make_summary(generation, generated, migrations)
        self.summaries.append(summary)
        self._append_summary(summary)
        self.database.commit()
        print(self._format_summary(summary))
        return summary

    def run(self) -> list[GenerationSummary]:
        self._prepare_summary_csv()
        self.initialize()
        goal = self.config.evaluation.fixed_goal
        x_range = self.config.evaluation.goal_x_range
        y_range = self.config.evaluation.goal_y_range
        goal_desc = (
            f"goal_x in {list(x_range)}, goal_y in {list(y_range)}, yaw={goal.yaw}"
            if x_range is not None and y_range is not None
            else f"fixed_goal=[{goal.x}, {goal.y}, {goal.yaw}]"
        )
        initial_count = len(self.islands) * self.config.evolution.population_size_per_island
        print(
            f"Initialized {len(self.islands)} islands with {initial_count} evaluated individuals; "
            f"{goal_desc}"
        )
        print(
            f"evaluator={type(self.evaluator).__name__}; "
            f"crossover={type(self.crossover).__name__}; "
            f"mutation={type(self.mutation).__name__}"
        )
        for generation in range(1, self.config.evolution.max_generations + 1):
            self.run_generation(generation)
        self.shutdown()
        if self._owns_database:
            self.database.close()
        return self.summaries

    def _validate_and_evaluate(self, individual: Individual) -> EvaluationResult:
        """Validate + evaluate one individual, mutating only its own fields.

        Touches no database state and no other individual's data, so this is
        the one piece of _process_individual/_process_individuals that is
        safe to run inside a worker thread (see _process_individuals): the
        only shared object it reaches into is self.evaluator, and
        RosGazeboEvaluator.evaluate() is itself safe under concurrent calls
        (per-call workspace leased from a pool, its own rng access locked).
        """
        valid, error = validate_individual(individual)
        individual.valid = valid
        individual.error_message = error
        if not valid:
            result = EvaluationResult(False, None, None, None, error_message=error)
            individual.fitness = self.config.fitness.failure_fitness
            return result
        result = self.evaluator.evaluate(individual)
        individual.evaluation_success = result.success
        individual.error_message = result.error_message
        individual.planning_time = result.planning_time
        individual.path_length = result.path_length
        individual.arrival_time = result.arrival_time
        individual.node_expansions = result.node_expansions
        # Fitness is intentionally not set here: it is normalized against
        # min/max metrics observed across a whole pool of individuals (see
        # _score_individuals), which isn't known until every member of that
        # pool has been evaluated.
        return result

    @staticmethod
    def _result_from_individual(individual: Individual) -> EvaluationResult:
        """Reconstruct the EvaluationResult an already-evaluated individual
        came from, from the metrics _validate_and_evaluate copied onto it.
        Used to rescore parents (evaluated in a prior generation) alongside
        freshly evaluated children without re-running the evaluator."""
        return EvaluationResult(
            individual.evaluation_success,
            individual.planning_time,
            individual.path_length,
            individual.arrival_time,
            individual.error_message,
            node_expansions=individual.node_expansions,
        )

    def _score_individuals(self, individuals: list[Individual]) -> None:
        """Assign fitness to every valid individual in `individuals` by
        normalizing its metrics against the min/max observed across this
        same pool's successful evaluations (see fitness.calculate_fitness).
        Invalid individuals already carry config.fitness.failure_fitness
        from _validate_and_evaluate and are left untouched.

        Call this once per island per generation on that island's full
        parents+children pool (not just the new children), so parents are
        rescored against the same generation's min/max as their children
        before select_survivors compares them.
        """
        scoreable = [individual for individual in individuals if individual.valid]
        results = [self._result_from_individual(individual) for individual in scoreable]
        stats = compute_generation_stats(results)
        for individual, result in zip(scoreable, results):
            individual.fitness = calculate_fitness(result, self.config.fitness, stats)

    def _process_individual(self, individual: Individual) -> None:
        self.database.save_individual(individual)
        result = self._validate_and_evaluate(individual)
        self._score_individuals([individual])
        self.database.update_individual_status(individual)
        self.database.save_evaluation(individual, result)

    def _process_individuals(self, individuals: list[Individual]) -> list[EvaluationResult]:
        """Batch form of _process_individual's validate+evaluate step:
        records every individual's creation up front (same order/timing as
        calling _process_individual in a loop would, for the
        save_individual() half), then runs validate+evaluate for the whole
        batch -- concurrently across self._workspace_pool_size worker
        threads if a pool was configured, otherwise sequentially in the same
        order.

        Unlike _process_individual, this does NOT score or persist the
        evaluation: fitness normalization needs every result in the pool
        first (see _score_individuals), so the caller must score the batch
        and then persist it (see _persist_freshly_evaluated /
        _persist_rescored) once fitness has been assigned.
        """
        if not individuals:
            return []
        for individual in individuals:
            self.database.save_individual(individual)
        if self._executor is None:
            return [self._validate_and_evaluate(individual) for individual in individuals]
        futures = [
            self._executor.submit(self._validate_and_evaluate, individual)
            for individual in individuals
        ]
        return [future.result() for future in futures]

    def _persist_freshly_evaluated(
        self, individuals: list[Individual], results: list[EvaluationResult]
    ) -> None:
        """Persist status and a first evaluations row for individuals just
        evaluated by _process_individuals, after _score_individuals has set
        their final .fitness."""
        for individual, result in zip(individuals, results):
            self.database.update_individual_status(individual)
            self.database.save_evaluation(individual, result)

    def _persist_rescored(self, individuals: list[Individual]) -> None:
        """Update status and the existing evaluations row for individuals
        rescored by _score_individuals without being re-evaluated this
        generation -- i.e. parents carried over from a prior generation,
        whose raw metrics are unchanged but whose fitness may have shifted
        under this generation's min/max."""
        for individual in individuals:
            self.database.update_individual_status(individual)
            self.database.update_evaluation_fitness(individual.individual_id, individual.fitness)

    def shutdown(self) -> None:
        """Release the worker thread pool, if one was created. Safe to call
        even when no pool exists (self._executor is None). run() calls this
        automatically; code that builds an EvolutionEngine directly (as
        several tests do) and doesn't call run() should call this in a
        finally block, the same way it already closes the database."""
        if self._executor is not None:
            self._executor.shutdown(wait=True)
            self._executor = None

    def perform_ring_migration(self, generation: int) -> int:
        selections: list[tuple[Island, Island, Individual]] = []
        for index, source_island in enumerate(self.islands):
            target_island = self.islands[(index + 1) % len(self.islands)]
            if source_island.population:
                source = min(source_island.population, key=elite_sort_key)
                selections.append((source_island, target_island, source))

        copied_by_target: dict[str, list[Individual]] = {
            island.name: [] for island in self.islands
        }
        for source_island, target_island, source in selections:
            copied_id = self.ids.next("mig")
            destination = self.config.source_directory / (
                f"{copied_id}{Path(source.source_path).suffix}"
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source.source_path, destination)
            copied = replace(
                source,
                individual_id=copied_id,
                generation=generation,
                current_island=target_island.name,
                source_path=str(destination),
                parent_ids=[source.individual_id],
                operator_type="migration_copy",
                is_elite=False,
                selected_for_next_generation=False,
                change_history=[
                    f"Copied from {source_island.name} to {target_island.name}"
                ],
                llm_calls=[],
            )
            self.database.save_individual(copied)
            if self.config.migration.reevaluate_migrants:
                result = self.evaluator.evaluate(copied)
                copied.evaluation_success = result.success
                copied.planning_time = result.planning_time
                copied.path_length = result.path_length
                copied.arrival_time = result.arrival_time
                copied.node_expansions = result.node_expansions
                # Normalize against the destination island's current
                # population plus this migrant, rather than mutating that
                # population's already-selected fitness values here.
                pool_results = [
                    self._result_from_individual(member)
                    for member in target_island.population
                ] + [result]
                stats = compute_generation_stats(pool_results)
                copied.fitness = calculate_fitness(result, self.config.fitness, stats)
            else:
                result = EvaluationResult(
                    copied.evaluation_success,
                    copied.planning_time,
                    copied.path_length,
                    copied.arrival_time,
                    copied.error_message,
                    node_expansions=copied.node_expansions,
                )
            self.database.update_individual_status(copied)
            self.database.save_evaluation(copied, result)
            self.database.save_migration(
                source, copied, source_island.name, target_island.name
            )
            copied_by_target[target_island.name].append(copied)

        for target_island in self.islands:
            candidates = target_island.population + copied_by_target[target_island.name]
            target_island.population = select_survivors(
                candidates,
                self.config.evolution.population_size_per_island,
                self.config.evolution.elite_count,
                target_island.rng,
                self.config.selection.epsilon,
            )
            for candidate in candidates:
                self.database.update_individual_status(candidate)
        self.total_migrations += len(selections)
        return len(selections)

    def _make_summary(
        self, generation: int, generated: int, migrations: int
    ) -> GenerationSummary:
        return GenerationSummary(
            generation=generation,
            generated_individuals=generated,
            migrations=migrations,
            population_sizes={
                island.name: len(island.population) for island in self.islands
            },
            best_fitness={
                island.name: max(
                    (item.fitness or 0.0 for item in island.population), default=0.0
                )
                for island in self.islands
            },
        )

    def _prepare_summary_csv(self) -> None:
        self.config.generation_csv.parent.mkdir(parents=True, exist_ok=True)
        with self.config.generation_csv.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(
                ["generation", "generated_individuals", "migrations"]
                + [f"{name}_population_size" for name in self.config.islands]
                + [f"{name}_best_fitness" for name in self.config.islands]
            )

    def _append_summary(self, summary: GenerationSummary) -> None:
        with self.config.generation_csv.open("a", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(
                [summary.generation, summary.generated_individuals, summary.migrations]
                + [summary.population_sizes[name] for name in self.config.islands]
                + [f"{summary.best_fitness[name]:.8f}" for name in self.config.islands]
            )

    @staticmethod
    def _format_summary(summary: GenerationSummary) -> str:
        sizes = ", ".join(
            f"{name}={size}" for name, size in summary.population_sizes.items()
        )
        return (
            f"generation={summary.generation} generated={summary.generated_individuals} "
            f"migrations={summary.migrations} populations[{sizes}]"
        )
