# Codex実装仕様書：LLM変異GP + MAP-Elites + 島モデル

## 1. 目的

ROS/Gazeboで評価する経路計画アルゴリズムを個体として扱い、以下を組み合わせた進化計算フレームワークをPythonで実装する。

- GPによる親選択・交叉
- LLMを用いた変異
- MAP-Elites風アーカイブ
- 島ベースの母集団モデル
- SQLiteによる全個体・評価結果・変更履歴の保存

アルゴリズムの具体的な改変方法は後から決めるため、交叉、LLM変異、ROS/Gazebo評価は交換可能なインターフェースとして実装する。最初はモックでも全体ループが動作するようにする。

---

## 2. 島の構成

4つの島を用意する。

1. `astar`
2. `relaxed_astar`
3. `dwa`
4. `llm_large_mutation`

各島は独立したMAP-Elitesアーカイブを持つ。

---

## 3. MAP-Elitesアーカイブ

評価指標は次の3つ。

- 経路生成時間
- 経路長
- 到達時間

各指標を以下の3段階に離散化する。

- `good`
- `medium`
- `poor`

よって、各島のセル数は次のとおり。

```text
3 × 3 × 3 = 27セル
```

各セルには最大1個体を保存する。

```text
1島あたり最大27個体
4島全体で最大108個体
```

セルキー例：

```python
("good", "medium", "good")
```

対象セルが空なら新個体を保存する。既存個体がいる場合は適応度を比較し、新個体の方が高い場合のみ置き換える。アーカイブに残らなかった個体もSQLiteには履歴として保存する。

---

## 4. 初期個体

各島に3個体ずつ配置する。

```text
astar: A1, A2, A3
relaxed_astar: R1, R2, R3
dwa: D1, D2, D3
llm_large_mutation: L1, L2, L3
```

初期個体は合計12個体。すべて評価し、各島のアーカイブへ登録する。

---

## 5. 各世代で生成する個体

各島で1世代につき親ペアを5組選択する。

1組の親から次の3個体を生成する。

1. `crossover_only`
2. `mutation_only`
3. `crossover_and_mutation`

したがって、生成数は次のとおり。

```text
1島：5組 × 3個体 = 15個体
4島：15個体 × 4島 = 60個体/世代
```

---

## 6. 親選択

各島のアーカイブに保存されている個体からトーナメント選択で親を決める。

初期設定：

```python
TOURNAMENT_SIZE = 3
PARENT_PAIRS_PER_ISLAND = 5
```

処理：

1. 島内からランダムに3個体を選択
2. 最も適応度が高い個体を親とする
3. これを2回行い、親1と親2を決定
4. 可能なら同一個体同士を避ける
5. 島内に1個体しかいない場合は同じ個体を2回使ってよい

---

## 7. 子個体生成

親1と親2から以下を生成する。

### 7.1 交叉のみ

```text
Parent1 + Parent2
        ↓
     crossover
        ↓
     Child_C
```

`operator_type = "crossover_only"`

### 7.2 LLM変異のみ

親1または親2をランダムに1個体選び、LLM変異する。

```text
Parent1 or Parent2
        ↓
   LLM mutation
        ↓
     Child_M
```

`operator_type = "mutation_only"`

### 7.3 交叉 + LLM変異

```text
Parent1 + Parent2
        ↓
     crossover
        ↓
 intermediate child
        ↓
   LLM mutation
        ↓
     Child_CM
```

`operator_type = "crossover_and_mutation"`

3個体はすべて別個体として検証・評価・保存判定する。

---

## 8. コード検証

ROS/Gazebo評価前に次を確認する。

1. ファイルが生成されている
2. 構文エラーがない
3. ビルド可能
4. 必須関数・クラスが存在する
5. 禁止されたファイルを変更していない
6. タイムアウトしない
7. NaN/Infを出す危険な処理がないか簡易確認

無効な個体はROS/Gazeboで実行しない。失敗理由をSQLiteへ保存する。LLM修正の再試行は初期設定で1回まで。

---

## 9. ROS/Gazebo評価

最初はモック評価器を実装し、後から実評価器へ差し替え可能にする。

```python
@dataclass
class EvaluationResult:
    success: bool
    planning_time: float | None
    path_length: float | None
    arrival_time: float | None
    error_message: str | None = None
    raw_log_path: str | None = None
```

将来的には複数ゴール・複数試行を行い、平均値を使用できるようにする。

---

## 10. 適応度

3指標は小さいほど良い。基準値に対して正規化し、重み付き和を計算する。

```python
planning_score = max(0.0, 1.0 - planning_time / planning_reference)
path_score = max(0.0, 1.0 - path_length / path_reference)
arrival_score = max(0.0, 1.0 - arrival_time / arrival_reference)

fitness = (
    planning_score * planning_weight
    + path_score * path_weight
    + arrival_score * arrival_weight
)
```

初期重み：

```yaml
planning_weight: 0.33
path_weight: 0.34
arrival_weight: 0.33
failure_fitness: 0.0
```

---

## 11. セル分類

設定ファイルで閾値を変更可能にする。

```yaml
bins:
  planning_time:
    good_max: 0.15
    medium_max: 0.30
  path_length:
    good_max: 10.0
    medium_max: 15.0
  arrival_time:
    good_max: 20.0
    medium_max: 35.0
```

分類規則：

```text
value <= good_max   → good
value <= medium_max → medium
otherwise           → poor
```

---

## 12. 島間コピー

5世代ごとにリング型でコピーする。

```text
astar
  ↓
relaxed_astar
  ↓
dwa
  ↓
llm_large_mutation
  ↓
astar
```

各島から2個体をコピーする。

1. 島内で最も適応度が高い個体
2. 最良個体とは異なるセルからランダムに選んだ個体

```text
2個体 × 4島 = 最大8個体/移住処理
```

コピー元の個体は削除しない。コピー個体には新しいIDを付け、元個体ID、コピー元島、コピー先島、世代を記録する。

コピー先でも通常のMAP-Elites規則を適用する。

重要：全コピー元の選択を先に完了してから、コピー先へ一括登録する。同じ移住処理中に多段コピーされることを防ぐ。

---

## 13. データ構造

```python
@dataclass
class Individual:
    individual_id: str
    generation: int
    current_island: str
    origin_island: str
    source_path: str
    parent_ids: list[str]
    operator_type: str

    planning_time: float | None = None
    path_length: float | None = None
    arrival_time: float | None = None
    fitness: float | None = None
    cell_key: tuple[str, str, str] | None = None

    valid: bool = True
    evaluation_success: bool = False
    error_message: str | None = None
    change_history: list[str] = field(default_factory=list)
```

---

## 14. SQLite

最低限、次のテーブルを作成する。

### individuals

- individual_id
- generation
- current_island
- origin_island
- operator_type
- source_path
- parent_ids
- valid
- evaluation_success
- created_at

### evaluations

- individual_id
- planning_time
- path_length
- arrival_time
- fitness
- success
- error_message
- raw_log_path
- evaluated_at

### archive_entries

- island_name
- planning_time_bin
- path_length_bin
- arrival_time_bin
- individual_id
- updated_at

### migrations

- source_individual_id
- copied_individual_id
- source_island
- target_island
- generation
- migrated_at

### change_history

- individual_id
- sequence_number
- operation_type
- description
- diff_text

---

## 15. インターフェース

### 評価器

```python
class Evaluator(Protocol):
    def evaluate(self, individual: Individual) -> EvaluationResult:
        ...
```

### 交叉

```python
class CrossoverOperator(Protocol):
    def crossover(
        self,
        parent1: Individual,
        parent2: Individual,
    ) -> Individual:
        ...
```

### LLM変異

```python
class MutationOperator(Protocol):
    def mutate(
        self,
        individual: Individual,
        context: MutationContext,
    ) -> Individual:
        ...
```

LLM API未設定時はモック実装を使う。

---

## 16. 推奨ディレクトリ構成

```text
llm_gp_framework/
├─ README.md
├─ pyproject.toml
├─ config/
│  └─ default.yaml
├─ src/
│  └─ llm_gp/
│     ├─ main.py
│     ├─ config.py
│     ├─ models.py
│     ├─ fitness.py
│     ├─ bins.py
│     ├─ database.py
│     ├─ archive.py
│     ├─ island.py
│     ├─ selection.py
│     ├─ migration.py
│     ├─ validation.py
│     ├─ operators/
│     │  ├─ crossover.py
│     │  ├─ mutation.py
│     │  ├─ mock_crossover.py
│     │  └─ mock_mutation.py
│     └─ evaluators/
│        ├─ base.py
│        ├─ mock.py
│        └─ ros_gazebo.py
├─ initial_individuals/
│  ├─ astar/
│  ├─ relaxed_astar/
│  ├─ dwa/
│  └─ llm_large_mutation/
├─ generated_individuals/
├─ experiment_logs/
├─ experiment_results/
└─ tests/
   ├─ test_bins.py
   ├─ test_archive.py
   ├─ test_selection.py
   ├─ test_migration.py
   ├─ test_fitness.py
   └─ test_generation_loop.py
```

---

## 17. 世代ループ

```python
for generation in range(1, max_generations + 1):

    for island in islands:

        for _ in range(parent_pairs_per_island):
            parent1 = tournament_selection(island.archive)
            parent2 = tournament_selection(island.archive)

            child_c = crossover(parent1, parent2)

            mutation_parent = random.choice([parent1, parent2])
            child_m = llm_mutation(mutation_parent)

            intermediate = crossover(parent1, parent2)
            child_cm = llm_mutation(intermediate)

            children = [child_c, child_m, child_cm]

            for child in children:
                save_individual(child)

                if not validate(child):
                    save_failure(child)
                    continue

                result = evaluator.evaluate(child)

                if not result.success:
                    child.fitness = failure_fitness
                    save_evaluation(child, result)
                    continue

                child.fitness = calculate_fitness(result)
                child.cell_key = classify_to_cell(result)

                save_evaluation(child, result)
                island.archive.try_insert(child)

    if generation % migration_interval == 0:
        perform_ring_migration(islands)

    save_generation_summary(generation, islands)
```

---

## 18. 設定ファイル例

```yaml
evolution:
  max_generations: 50
  parent_pairs_per_island: 5

selection:
  method: tournament
  tournament_size: 3

migration:
  interval: 5
  migrants_per_island: 2
  reevaluate_migrants: false

archive:
  individuals_per_cell: 1

fitness:
  planning_weight: 0.33
  path_weight: 0.34
  arrival_weight: 0.33
  planning_reference: 1.0
  path_reference: 30.0
  arrival_reference: 60.0
  failure_fitness: 0.0

evaluation:
  evaluator_type: mock
  repetitions_per_goal: 3
  timeout_seconds: 120

database:
  path: experiment_results/evolution.db

random_seed: 42
```

---

## 19. テスト要件

最低限、以下をpytestで確認する。

- 空セルに個体を登録できる
- 同一セルで高適応度個体に置換される
- 低適応度個体では置換されない
- 各島のアーカイブが27セルを超えない
- 親ペア1組から3種類の子が生成される
- 1島1世代で15個体生成される
- 4島1世代で60個体生成される
- 5世代ごとにのみ島間コピーが実行される
- コピー元個体が削除されない
- コピー先でもMAP-Elites規則が適用される
- 全生成個体がSQLiteに保存される
- 同じ乱数シードで同じ結果になる

---

## 20. 完了条件

以下でモック実験を実行できること。

```bash
python -m llm_gp.main --config config/default.yaml
```

実行後に確認できること：

- 4島が作成される
- 初期12個体が評価される
- 各世代で全体60個体が生成される
- `crossover_only`、`mutation_only`、`crossover_and_mutation`が記録される
- 各島最大27個体を保持する
- 5世代ごとに各島から最大2個体が隣接島へコピーされる
- SQLiteに全個体、評価、履歴、移住情報が残る
- 世代統計がCSVと標準出力へ保存される
- `pytest`が成功する

---

## 21. Codexへの最終指示

この仕様に基づき、まずモック評価器・モック交叉・モックLLM変異で全体ループが動く完成版を実装すること。

要件：

- Python 3.11以上
- 型ヒントを使用
- dataclassまたはPydanticを使用
- 設定値をハードコードしない
- 乱数シードを固定可能にする
- SQLiteへ保存する
- pytestを作成する
- READMEへ実行方法を書く
- ROS/Gazebo、LLM API、交叉処理は後から差し替え可能にする
- 不明点は合理的な仮定を置き、モック版を先に完成させる
- 実装後、生成ファイル一覧、実行手順、主要設計を説明する
