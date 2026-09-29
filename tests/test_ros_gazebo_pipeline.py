from __future__ import annotations

import random
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from llm_gp.config import AppConfig, load_config
from llm_gp.database import ExperimentDatabase
from llm_gp.evaluator import RosGazeboEvaluator
from llm_gp.evolution import EvolutionEngine
from llm_gp.models import EvaluationResult, GoalPose, Individual, MutationContext
from llm_gp.operators import (
    CppRelaxedAStarCrossoverOperator,
    CppRelaxedAStarMutationOperator,
    IdFactory,
    MockCrossoverOperator,
    OpenAIGPT4oMiniCrossoverOperator,
    OpenAIGPT4oMiniMutationOperator,
    build_crossover_operator,
    initial_cpp_source,
)
from llm_gp.validation import validate_cpp_source, validate_individual, validate_meaningful_change


class SuccessfulEvaluator:
    def evaluate(self, individual: Individual) -> EvaluationResult:
        return EvaluationResult(True, 0.1, 8.0, 15.0)


def ros_config(tmp_path: Path) -> AppConfig:
    root = Path(__file__).parents[1]
    base = load_config(root / "config" / "ros_gazebo.yaml")
    return replace(
        base,
        database_path=tmp_path / "ros.db",
        source_directory=tmp_path / "cpp_sources",
        generation_csv=tmp_path / "ros_summary.csv",
    )


def test_mini_configuration_is_explicitly_small() -> None:
    root = Path(__file__).parents[1]
    config = load_config(root / "config" / "ros_gazebo_mini.yaml")
    assert config.evolution.smoke_mode
    assert config.evolution.max_generations == 1
    assert config.evolution.population_size_per_island == 1
    assert config.evolution.parent_pairs_per_island == 1
    assert config.evaluation.repetitions_per_goal == 1


def test_connection_retry_defaults_apply_to_existing_yaml() -> None:
    root = Path(__file__).parents[1]
    config = load_config(root / "config" / "default.yaml")
    assert config.llm.connection_max_retries == 8
    assert config.llm.connection_retry_base_seconds == 15.0
    assert config.llm.connection_retry_max_seconds == 300.0


def test_meaningful_change_validation_defaults_apply_to_existing_yaml() -> None:
    """New ValidationSettings fields all have defaults, so every existing
    config file (none of which mention them) should still load and pick up
    the new gate enabled by default."""
    root = Path(__file__).parents[1]
    config = load_config(root / "config" / "default.yaml")
    assert config.validation.require_meaningful_change is True
    assert config.validation.min_change_ratio == 0.005
    assert config.validation.jitter_fallback_on_trivial_change is True


def test_ten_generation_small_configuration() -> None:
    root = Path(__file__).parents[1]
    config = load_config(root / "config" / "ros_gazebo_10gen_small.yaml")
    assert config.evolution.smoke_mode
    assert config.evolution.max_generations == 10
    assert config.evolution.population_size_per_island == 2
    assert config.evolution.parent_pairs_per_island == 1
    assert config.evaluation.repetitions_per_goal == 1


def test_ten_generation_repeated_configuration() -> None:
    root = Path(__file__).parents[1]
    config = load_config(root / "config" / "ros_gazebo_10gen_repeated.yaml")
    assert config.evolution.max_generations == 10
    assert config.evolution.population_size_per_island == 2
    assert config.evaluation.repetitions_per_goal == 3
    assert config.llm.provider == "openai"
    assert config.llm.fallback_to_mock is False
    assert "ten_generation_repeated" in str(config.database_path)


def test_ros_configuration_targets_fixed_goal_and_wsl_workspace(tmp_path: Path) -> None:
    config = ros_config(tmp_path)
    assert config.evaluation.fixed_goal.as_tuple() == (1.69, 0.954, -0.00143)
    assert config.evaluation.evaluator_type == "ros_gazebo"
    assert config.evaluation.ros_gazebo is not None
    assert config.evaluation.ros_gazebo.workspace.startswith("/home/adachi/")
    assert config.evaluation.ros_gazebo.candidate_target.replace("\\", "/").endswith(
        "global_planner/src/rastar.cpp"
    )
    assert config.islands == tuple(f"island_{index}" for index in range(1, 5))
    assert config.validation.max_repair_attempts == 1
    assert config.evaluation.repetitions_per_goal == 3
    launch = (
        Path(__file__).parents[1]
        / config.evaluation.ros_gazebo.launch_file
    ).read_text(encoding="utf-8")
    assert 'base_local_planner" value="dwa_local_planner/DWAPlannerROS"' in launch
    assert (
        config.evaluation.ros_gazebo.node_expansions_topic
        == "/move_base/GlobalPlanner/node_expansions"
    )


def test_complex_map_configuration_targets_myroom_plus_x4(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    config = load_config(root / "config" / "ros_gazebo_complex_map.yaml")
    assert config.evaluation.evaluator_type == "ros_gazebo"
    assert config.evaluation.ros_gazebo is not None
    assert "complex_map" in config.evaluation.ros_gazebo.launch_file.name
    launch = config.evaluation.ros_gazebo.launch_file.read_text(encoding="utf-8")
    assert "myroom_plus_x4_v3" in launch
    assert config.evaluation.fixed_goal.as_tuple() == (23.875, 1.475, 0.0)
    assert config.evaluation.ros_gazebo.start_pose.as_tuple() == (-0.325, 14.125, 0.0)


def test_cpp_mutation_changes_functional_heuristic_weight(tmp_path: Path) -> None:
    baseline = Path(__file__).parents[1] / "src" / "global_planner" / "src" / "rastar.cpp"
    ids = IdFactory()
    initial_path = initial_cpp_source(tmp_path, ids.next(), baseline)
    parent = Individual("parent", 0, "island_1", "island_1", str(initial_path), [], "initial")
    operator = CppRelaxedAStarMutationOperator(ids, random.Random(42))
    child = operator.mutate(parent, MutationContext(1, "island_1", tmp_path, source_suffix=".cpp"))
    source = Path(child.source_path).read_text(encoding="utf-8")
    assert child.source_path.endswith(".cpp")
    assert "kLlmGpHeuristicWeight" in source
    assert "RAStarExpansion::calculatePotentials" in source
    assert "distance * neutral_cost_ * tBreak * kLlmGpHeuristicWeight" in source
    assert "1.00000000f" not in source
    assert validate_individual(child) == (True, None)


def test_real_mode_initializes_cpp_candidates_without_calling_ros(tmp_path: Path) -> None:
    config = ros_config(tmp_path)
    database = ExperimentDatabase(config.database_path, reset=True)
    try:
        engine = EvolutionEngine(config, database=database, evaluator=SuccessfulEvaluator())
        engine.initialize()
        assert database.count("individuals") == 40
        assert all(
            item.source_path.endswith(".cpp")
            for island in engine.islands
            for item in island.population
        )
    finally:
        database.close()


def test_build_command_uses_leased_workspace_not_settings_workspace(tmp_path: Path) -> None:
    """_build_command must use the workspace argument (leased from the pool)
    rather than settings.workspace directly, otherwise every concurrent
    evaluation would race on the same catkin workspace regardless of
    WorkspacePool."""
    config = ros_config(tmp_path)
    settings = config.evaluation.ros_gazebo
    assert settings is not None
    evaluator = RosGazeboEvaluator(
        settings=settings,
        fixed_goal=config.evaluation.fixed_goal,
        timeout_seconds=1,
        repetitions=1,
        build_timeout_seconds=1,
        log_directory=tmp_path / "logs",
        project_root=Path(__file__).parents[1],
    )
    individual = Individual(
        "ind1", 0, "island_1", "island_1", str(tmp_path / "candidate.cpp"), [], "initial"
    )
    command = evaluator._build_command(
        individual, tmp_path / "out.json", GoalPose(1.0, 2.0, 0.0), "/home/adachi/other_workspace"
    )
    assert "/home/adachi/other_workspace" in command
    assert settings.workspace not in command


class ConcurrencyTrackingEvaluator:
    """Fake evaluator that records the high-water mark of concurrently active
    evaluate() calls, to verify EvolutionEngine actually dispatches to
    multiple worker threads instead of only claiming to."""

    def __init__(self, delay: float = 0.05) -> None:
        self.delay = delay
        self._lock = threading.Lock()
        self._current = 0
        self.max_concurrent = 0

    def evaluate(self, individual: Individual) -> EvaluationResult:
        with self._lock:
            self._current += 1
            self.max_concurrent = max(self.max_concurrent, self._current)
        time.sleep(self.delay)
        with self._lock:
            self._current -= 1
        return EvaluationResult(True, 0.1, 8.0, 15.0)


def _mini_config_with_additional_workspaces(
    tmp_path: Path, additional_workspaces: tuple[str, ...]
) -> AppConfig:
    root = Path(__file__).parents[1]
    base = load_config(root / "config" / "ros_gazebo_mini.yaml")
    settings = base.evaluation.ros_gazebo
    assert settings is not None
    return replace(
        base,
        database_path=tmp_path / "ros_mini.db",
        source_directory=tmp_path / "cpp_sources",
        generation_csv=tmp_path / "ros_mini_summary.csv",
        evaluation=replace(
            base.evaluation,
            ros_gazebo=replace(settings, additional_workspaces=additional_workspaces),
        ),
    )


def test_engine_evaluates_initial_population_concurrently_with_additional_workspaces(
    tmp_path: Path,
) -> None:
    config = _mini_config_with_additional_workspaces(
        tmp_path, ("/tmp/ws2", "/tmp/ws3", "/tmp/ws4")
    )
    database = ExperimentDatabase(config.database_path, reset=True)
    evaluator = ConcurrencyTrackingEvaluator(delay=0.05)
    engine = EvolutionEngine(config, database=database, evaluator=evaluator)
    try:
        engine.initialize()  # ros_gazebo_mini.yaml: population_size_per_island=1 x 4 islands
        assert evaluator.max_concurrent == 4
        assert database.count("individuals") == 4
    finally:
        engine.shutdown()
        database.close()


def test_engine_stays_sequential_without_additional_workspaces(tmp_path: Path) -> None:
    config = _mini_config_with_additional_workspaces(tmp_path, ())
    database = ExperimentDatabase(config.database_path, reset=True)
    evaluator = ConcurrencyTrackingEvaluator(delay=0.02)
    engine = EvolutionEngine(config, database=database, evaluator=evaluator)
    try:
        engine.initialize()
        assert evaluator.max_concurrent == 1
    finally:
        engine.shutdown()
        database.close()


class FakeResponse:
    def __init__(self, output_text: str) -> None:
        self.output_text = output_text


class FakeResponses:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = iter(outputs)
        self.inputs: list[str] = []

    def create(self, *, model: str, input: str) -> FakeResponse:
        self.inputs.append(input)
        return FakeResponse(next(self.outputs))


class FakeOpenAIClient:
    def __init__(self, outputs: list[str]) -> None:
        self.responses = FakeResponses(outputs)


class FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.total_tokens = input_tokens + output_tokens


class FakeResponseWithUsage:
    def __init__(self, output_text: str, usage: FakeUsage) -> None:
        self.output_text = output_text
        self.usage = usage


class FakeResponsesWithUsage:
    def __init__(self, output_text: str, usage: FakeUsage) -> None:
        self._response = FakeResponseWithUsage(output_text, usage)
        self.inputs: list[str] = []

    def create(self, *, model: str, input: str) -> FakeResponseWithUsage:
        self.inputs.append(input)
        return self._response


class FakeOpenAIClientWithUsage:
    def __init__(self, output_text: str, usage: FakeUsage) -> None:
        self.responses = FakeResponsesWithUsage(output_text, usage)


class FlakyThenOKResponses:
    """Raises a given (transient) error a fixed number of times before
    returning canned outputs -- exercises _create_response_with_retry's
    retry loop through the public mutate()/crossover() API."""

    def __init__(self, outputs: list[str], fail_times: int, make_error) -> None:
        self.outputs = iter(outputs)
        self.fail_times = fail_times
        self.make_error = make_error
        self.calls = 0
        self.inputs: list[str] = []

    def create(self, *, model: str, input: str) -> FakeResponse:
        self.calls += 1
        self.inputs.append(input)
        if self.calls <= self.fail_times:
            raise self.make_error()
        return FakeResponse(next(self.outputs))


class FlakyOpenAIClient:
    def __init__(self, outputs: list[str], fail_times: int, make_error) -> None:
        self.responses = FlakyThenOKResponses(outputs, fail_times, make_error)


class AlwaysFailingResponses:
    def __init__(self, make_error) -> None:
        self.make_error = make_error
        self.calls = 0

    def create(self, *, model: str, input: str) -> FakeResponse:
        self.calls += 1
        raise self.make_error()


class AlwaysFailingOpenAIClient:
    def __init__(self, make_error) -> None:
        self.responses = AlwaysFailingResponses(make_error)


def test_openai_mutation_retries_transient_connection_error(
    tmp_path: Path, test_config: AppConfig
) -> None:
    """Regression test for a real failure: a transient DNS/connection error
    during a multi-hour unattended run used to crash the whole process. A
    connection error that clears up within the retry budget must now be
    absorbed transparently."""
    import httpx
    from openai import APIConnectionError

    ids = IdFactory()
    parent = _cpp_parent(tmp_path, ids)
    source = Path(parent.source_path).read_text(encoding="utf-8")
    changed = _non_trivial_change(source)

    def make_error() -> APIConnectionError:
        return APIConnectionError(request=httpx.Request("POST", "https://api.openai.com/v1/responses"))

    client = FlakyOpenAIClient([f"```cpp\n{changed}\n```"], fail_times=2, make_error=make_error)
    operator = OpenAIGPT4oMiniMutationOperator(
        ids,
        max_retries=0,
        client=client,
        fitness_settings=test_config.fitness,
        validation_settings=test_config.validation,
        connection_max_retries=3,
        connection_retry_base_seconds=0.01,
        connection_retry_max_seconds=0.02,
    )

    child = operator.mutate(
        parent,
        MutationContext(1, "island_1", tmp_path, source_suffix=".cpp"),
    )

    assert validate_individual(child) == (True, None)
    assert client.responses.calls == 3  # failed twice, succeeded on the 3rd attempt


def test_openai_mutation_does_not_retry_non_transient_error(
    tmp_path: Path, test_config: AppConfig
) -> None:
    """A non-transient error (e.g. a 400 bad-request) must fail fast without
    burning the retry budget, since retrying can't fix it."""
    import httpx
    from openai import APIStatusError

    ids = IdFactory()
    parent = _cpp_parent(tmp_path, ids)

    def make_error() -> APIStatusError:
        response = httpx.Response(400, request=httpx.Request("POST", "https://api.openai.com/v1/responses"))
        return APIStatusError("bad request", response=response, body=None)

    client = AlwaysFailingOpenAIClient(make_error)
    operator = OpenAIGPT4oMiniMutationOperator(
        ids,
        max_retries=0,
        client=client,
        fitness_settings=test_config.fitness,
        validation_settings=test_config.validation,
        connection_max_retries=5,
        connection_retry_base_seconds=0.01,
        connection_retry_max_seconds=0.02,
    )

    with pytest.raises(APIStatusError):
        operator.mutate(parent, MutationContext(1, "island_1", tmp_path, source_suffix=".cpp"))
    assert client.responses.calls == 1


def _cpp_parent(tmp_path: Path, ids: IdFactory) -> Individual:
    baseline = Path(__file__).parents[1] / "src" / "global_planner" / "src" / "rastar.cpp"
    initial_path = initial_cpp_source(tmp_path, ids.next(), baseline)
    return Individual(
        "parent",
        0,
        "island_1",
        "island_1",
        str(initial_path),
        [],
        "initial",
    )


def _non_trivial_change(source: str) -> str:
    """A real one-line algorithm edit (rewrites the tie-break formula), used
    in place of a verbatim echo so fixtures also satisfy the
    validate_meaningful_change() gate -- verified in llm_gp/validation.py's
    own tests to be accepted as a genuine change, unlike a comment-only edit
    or a push_back->emplace_back-style token rename."""
    changed = source.replace(
        "float tBreak = 1 + 1 / (nx_ + ny_);",
        "float tBreak = 1 + 1 / static_cast<float>(nx_ + ny_ + 1);",
        1,
    )
    assert changed != source, "fixture source does not contain the expected tie-break line"
    return changed


def test_openai_mutation_extracts_cpp_fence_surrounded_by_prose(
    tmp_path: Path, test_config: AppConfig
) -> None:
    ids = IdFactory()
    parent = _cpp_parent(tmp_path, ids)
    source = Path(parent.source_path).read_text(encoding="utf-8")
    changed = _non_trivial_change(source)
    client = FakeOpenAIClient(
        [f"Here's the improved implementation.\n\n```cpp\n{changed}\n```\n\nImprovements: safer heap use."]
    )
    operator = OpenAIGPT4oMiniMutationOperator(
        ids,
        max_retries=2,
        client=client,
        fitness_settings=test_config.fitness,
        validation_settings=test_config.validation,
    )

    child = operator.mutate(
        parent,
        MutationContext(1, "island_1", tmp_path, source_suffix=".cpp"),
    )
    generated = Path(child.source_path).read_text(encoding="utf-8")

    assert generated.strip() == changed.strip()
    assert "```" not in generated
    assert "Here's" not in generated
    assert validate_individual(child) == (True, None)
    assert len(client.responses.inputs) == 1


def test_openai_mutation_records_token_usage_from_response(
    tmp_path: Path, test_config: AppConfig
) -> None:
    """Token counts from the Responses API usage field must be carried onto
    the LLMCallRecord, so a run's total LLM token consumption can be
    measured (needed for a 30-generation run, where cost adds up)."""
    ids = IdFactory()
    parent = _cpp_parent(tmp_path, ids)
    source = Path(parent.source_path).read_text(encoding="utf-8")
    changed = _non_trivial_change(source)
    client = FakeOpenAIClientWithUsage(f"```cpp\n{changed}\n```", FakeUsage(120, 45))
    operator = OpenAIGPT4oMiniMutationOperator(
        ids,
        max_retries=0,
        client=client,
        fitness_settings=test_config.fitness,
        validation_settings=test_config.validation,
    )

    child = operator.mutate(
        parent,
        MutationContext(1, "island_1", tmp_path, source_suffix=".cpp"),
    )

    assert len(child.llm_calls) == 1
    call = child.llm_calls[0]
    assert call.prompt_tokens == 120
    assert call.completion_tokens == 45
    assert call.total_tokens == 165


def test_openai_mutation_llm_call_has_no_tokens_when_response_has_no_usage(
    tmp_path: Path, test_config: AppConfig
) -> None:
    """A response with no `usage` attribute (like the plain FakeResponse
    used by other tests here) must leave the token fields as None rather
    than crashing or silently recording zero."""
    ids = IdFactory()
    parent = _cpp_parent(tmp_path, ids)
    source = Path(parent.source_path).read_text(encoding="utf-8")
    changed = _non_trivial_change(source)
    client = FakeOpenAIClient([f"```cpp\n{changed}\n```"])
    operator = OpenAIGPT4oMiniMutationOperator(
        ids,
        max_retries=0,
        client=client,
        fitness_settings=test_config.fitness,
        validation_settings=test_config.validation,
    )

    child = operator.mutate(
        parent,
        MutationContext(1, "island_1", tmp_path, source_suffix=".cpp"),
    )

    call = child.llm_calls[0]
    assert call.prompt_tokens is None
    assert call.completion_tokens is None
    assert call.total_tokens is None


def test_openai_mutation_rejects_verbatim_echo_as_not_meaningful(
    tmp_path: Path, test_config: AppConfig
) -> None:
    """Regression test for the real failure this gate exists to catch: a
    10-generation run's "best" individual turned out to be functionally
    identical to the unmutated baseline (comments stripped plus a
    push_back->emplace_back rename). An LLM response that echoes the parent
    verbatim must now be rejected and retried rather than accepted."""
    ids = IdFactory()
    parent = _cpp_parent(tmp_path, ids)
    source = Path(parent.source_path).read_text(encoding="utf-8")
    client = FakeOpenAIClient(
        [
            f"```cpp\n{source}\n```",  # verbatim echo: rejected
            f"```cpp\n{_non_trivial_change(source)}\n```",  # real edit: accepted
        ]
    )
    operator = OpenAIGPT4oMiniMutationOperator(
        ids,
        max_retries=2,
        client=client,
        fitness_settings=test_config.fitness,
        validation_settings=test_config.validation,
    )

    child = operator.mutate(
        parent,
        MutationContext(1, "island_1", tmp_path, source_suffix=".cpp"),
    )

    assert validate_individual(child) == (True, None)
    assert len(client.responses.inputs) == 2
    assert "previous response was rejected" in client.responses.inputs[1]
    assert "identical" in client.responses.inputs[1] or "equivalent tokens" in client.responses.inputs[1]
    generated = Path(child.source_path).read_text(encoding="utf-8")
    assert generated.strip() != source.strip()


def test_openai_mutation_retries_invalid_generated_source(
    tmp_path: Path, test_config: AppConfig
) -> None:
    ids = IdFactory()
    parent = _cpp_parent(tmp_path, ids)
    source = Path(parent.source_path).read_text(encoding="utf-8")
    client = FakeOpenAIClient(
        [
            "Here is an explanation without any source code.",
            f"```cpp\n{_non_trivial_change(source)}\n```",
        ]
    )
    operator = OpenAIGPT4oMiniMutationOperator(
        ids,
        max_retries=2,
        client=client,
        fitness_settings=test_config.fitness,
        validation_settings=test_config.validation,
    )

    child = operator.mutate(
        parent,
        MutationContext(1, "island_1", tmp_path, source_suffix=".cpp"),
    )

    assert validate_individual(child) == (True, None)
    assert len(client.responses.inputs) == 2
    assert "previous response was rejected" in client.responses.inputs[1]
    assert "2 generation attempt(s)" in child.change_history[0]


def test_openai_mutation_falls_back_to_weight_jitter_when_only_trivial_edits_offered(
    tmp_path: Path, test_config: AppConfig
) -> None:
    """When every retry only offers a trivial (push_back->emplace_back-style)
    edit, jitter_fallback_on_trivial_change should still leave the individual
    with a real numeric change (the heuristic weight) instead of shipping a
    functionally-unchanged candidate."""
    ids = IdFactory()
    parent = _cpp_parent(tmp_path, ids)
    source = Path(parent.source_path).read_text(encoding="utf-8")
    trivial = source.replace("queue_.push_back(", "queue_.emplace_back(")
    assert trivial != source
    client = FakeOpenAIClient([f"```cpp\n{trivial}\n```", f"```cpp\n{trivial}\n```"])
    operator = OpenAIGPT4oMiniMutationOperator(
        ids,
        max_retries=1,
        client=client,
        fitness_settings=test_config.fitness,
        validation_settings=test_config.validation,
    )

    child = operator.mutate(
        parent,
        MutationContext(1, "island_1", tmp_path, source_suffix=".cpp"),
    )

    assert validate_individual(child) == (True, None)
    generated = Path(child.source_path).read_text(encoding="utf-8")
    assert "kLlmGpHeuristicWeight = 1.00000000f" not in generated
    assert any("jitter" in entry for entry in child.change_history)


def _cpp_individual_with_metrics(
    tmp_path: Path,
    ids: IdFactory,
    individual_id: str,
    *,
    fitness: float | None = None,
    node_expansions: float | None = None,
    path_length: float | None = None,
    arrival_time: float | None = None,
) -> Individual:
    baseline = Path(__file__).parents[1] / "src" / "global_planner" / "src" / "rastar.cpp"
    initial_path = initial_cpp_source(tmp_path, ids.next(), baseline)
    return Individual(
        individual_id,
        0,
        "island_1",
        "island_1",
        str(initial_path),
        [],
        "initial",
        node_expansions=node_expansions,
        path_length=path_length,
        arrival_time=arrival_time,
        fitness=fitness,
    )


def test_mutation_prompt_includes_known_metrics_for_evaluated_parent(
    tmp_path: Path, test_config: AppConfig
) -> None:
    ids = IdFactory()
    parent = _cpp_individual_with_metrics(
        tmp_path, ids, "parent",
        fitness=0.0783, node_expansions=42462.0, path_length=38.93, arrival_time=174.22,
    )
    source = Path(parent.source_path).read_text(encoding="utf-8")
    client = FakeOpenAIClient([f"```cpp\n{_non_trivial_change(source)}\n```"])
    operator = OpenAIGPT4oMiniMutationOperator(
        ids, max_retries=0, client=client,
        fitness_settings=test_config.fitness, validation_settings=test_config.validation,
    )

    operator.mutate(parent, MutationContext(1, "island_1", tmp_path, source_suffix=".cpp"))

    prompt = client.responses.inputs[0]
    assert "parent" in prompt
    assert "fitness=0.0783" in prompt
    assert "node_expansions=" in prompt and "path_length=" in prompt and "arrival_time=" in prompt
    assert "reference" in prompt


def test_mutation_prompt_uses_reference_individuals_when_target_unevaluated(
    tmp_path: Path, test_config: AppConfig
) -> None:
    """crossover_and_mutation mutates a fresh, unevaluated intermediate --
    its metrics block should come from the two known-evaluated parents
    passed via MutationContext.reference_individuals, not the intermediate."""
    ids = IdFactory()
    parent1 = _cpp_individual_with_metrics(
        tmp_path, ids, "parent1", fitness=0.05, node_expansions=45000.0, path_length=39.0, arrival_time=180.0,
    )
    parent2 = _cpp_individual_with_metrics(
        tmp_path, ids, "parent2", fitness=0.09, node_expansions=38000.0, path_length=39.5, arrival_time=173.0,
    )
    intermediate = _cpp_individual_with_metrics(tmp_path, ids, "intermediate")
    assert intermediate.fitness is None
    source = Path(intermediate.source_path).read_text(encoding="utf-8")
    client = FakeOpenAIClient([f"```cpp\n{_non_trivial_change(source)}\n```"])
    operator = OpenAIGPT4oMiniMutationOperator(
        ids, max_retries=0, client=client,
        fitness_settings=test_config.fitness, validation_settings=test_config.validation,
    )
    context = MutationContext(
        1, "island_1", tmp_path, "crossover_and_mutation", ".cpp",
        reference_individuals=(parent1, parent2),
    )

    operator.mutate(intermediate, context)

    prompt = client.responses.inputs[0]
    assert "parent1" in prompt and "parent2" in prompt
    assert "fitness=0.0500" in prompt
    assert "fitness=0.0900" in prompt


def test_mutation_prompt_falls_back_to_no_data_notice_when_nothing_known(
    tmp_path: Path,
) -> None:
    ids = IdFactory()
    parent = _cpp_parent(tmp_path, ids)
    source = Path(parent.source_path).read_text(encoding="utf-8")
    client = FakeOpenAIClient([f"```cpp\n{_non_trivial_change(source)}\n```"])
    # No fitness_settings/validation_settings supplied.
    operator = OpenAIGPT4oMiniMutationOperator(ids, max_retries=0, client=client)

    operator.mutate(parent, MutationContext(1, "island_1", tmp_path, source_suffix=".cpp"))

    assert "No measured evaluation results are available" in client.responses.inputs[0]


def test_openai_crossover_merges_parents_and_logs_llm_calls(
    tmp_path: Path, test_config: AppConfig
) -> None:
    ids = IdFactory()
    parent1 = _cpp_individual_with_metrics(
        tmp_path, ids, "parent1", fitness=0.05, node_expansions=45000.0, path_length=39.0, arrival_time=180.0,
    )
    parent2 = _cpp_individual_with_metrics(
        tmp_path, ids, "parent2", fitness=0.09, node_expansions=38000.0, path_length=39.5, arrival_time=173.0,
    )
    source1 = Path(parent1.source_path).read_text(encoding="utf-8")
    merged = _non_trivial_change(source1)
    client = FakeOpenAIClient([f"```cpp\n{merged}\n```"])
    operator = OpenAIGPT4oMiniCrossoverOperator(
        ids, tmp_path, max_retries=1, client=client,
        fitness_settings=test_config.fitness, validation_settings=test_config.validation,
    )

    child = operator.crossover(parent1, parent2)

    assert validate_individual(child) == (True, None)
    assert child.parent_ids == ["parent1", "parent2"]
    assert child.operator_type == "crossover_only"
    assert len(child.llm_calls) == 1
    assert child.llm_calls[0].success
    prompt = client.responses.inputs[0]
    assert "parent1" in prompt and "parent2" in prompt
    assert "fitness=0.0500" in prompt and "fitness=0.0900" in prompt


def test_openai_crossover_records_token_usage_from_response(
    tmp_path: Path, test_config: AppConfig
) -> None:
    ids = IdFactory()
    parent1 = _cpp_individual_with_metrics(
        tmp_path, ids, "parent1", fitness=0.05, node_expansions=45000.0, path_length=39.0, arrival_time=180.0,
    )
    parent2 = _cpp_individual_with_metrics(
        tmp_path, ids, "parent2", fitness=0.09, node_expansions=38000.0, path_length=39.5, arrival_time=173.0,
    )
    source1 = Path(parent1.source_path).read_text(encoding="utf-8")
    merged = _non_trivial_change(source1)
    client = FakeOpenAIClientWithUsage(f"```cpp\n{merged}\n```", FakeUsage(300, 80))
    operator = OpenAIGPT4oMiniCrossoverOperator(
        ids, tmp_path, max_retries=1, client=client,
        fitness_settings=test_config.fitness, validation_settings=test_config.validation,
    )

    child = operator.crossover(parent1, parent2)

    assert len(child.llm_calls) == 1
    call = child.llm_calls[0]
    assert call.prompt_tokens == 300
    assert call.completion_tokens == 80
    assert call.total_tokens == 380


def test_openai_crossover_rejects_verbatim_parent_copy_and_falls_back(
    tmp_path: Path, test_config: AppConfig
) -> None:
    """Regression test for the original bug: crossover used to return
    parent1's source verbatim (plus an averaged heuristic weight). An LLM
    response that does the same must now be rejected by
    validate_meaningful_change and, once retries are exhausted, fall back to
    that same deterministic weight-average rather than silently shipping the
    verbatim copy as if it were a real merge."""
    ids = IdFactory()
    parent1 = _cpp_individual_with_metrics(
        tmp_path, ids, "parent1", fitness=0.05, node_expansions=45000.0, path_length=39.0, arrival_time=180.0,
    )
    parent2 = _cpp_individual_with_metrics(
        tmp_path, ids, "parent2", fitness=0.09, node_expansions=38000.0, path_length=39.5, arrival_time=173.0,
    )
    source1 = Path(parent1.source_path).read_text(encoding="utf-8")
    client = FakeOpenAIClient([f"```cpp\n{source1}\n```", f"```cpp\n{source1}\n```"])
    operator = OpenAIGPT4oMiniCrossoverOperator(
        ids, tmp_path, max_retries=1, client=client,
        fitness_settings=test_config.fitness, validation_settings=test_config.validation,
    )

    child = operator.crossover(parent1, parent2)

    assert validate_individual(child) == (True, None)
    assert len(client.responses.inputs) == 2
    assert all(not record.success for record in child.llm_calls)
    assert "fell back to averaging" in child.change_history[0]


def test_build_crossover_operator_falls_back_to_mock_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("llm_gp.operators.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    ids = IdFactory()
    operator = build_crossover_operator("mock", "gpt-4o-mini", ids, Path("."), cpp_mode=False)
    assert isinstance(operator, MockCrossoverOperator)


def test_build_crossover_operator_falls_back_to_deterministic_cpp_without_api_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr("llm_gp.operators.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    ids = IdFactory()
    operator = build_crossover_operator(
        "openai", "gpt-4o-mini", ids, tmp_path, cpp_mode=True, fallback_to_mock=True,
    )
    assert isinstance(operator, CppRelaxedAStarCrossoverOperator)


def test_build_crossover_operator_raises_without_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr("llm_gp.operators.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    ids = IdFactory()
    with pytest.raises(RuntimeError):
        build_crossover_operator(
            "openai", "gpt-4o-mini", ids, tmp_path, cpp_mode=True, fallback_to_mock=False,
        )


def test_cpp_validation_rejects_markdown_and_explanatory_prefix() -> None:
    assert validate_cpp_source("```cpp\nnamespace global_planner {}\n```")[0] is False
    assert validate_cpp_source("Here is the code.\n#include <global_planner/rastar.h>")[0] is False


def test_meaningful_change_rejects_comment_only_edit(tmp_path: Path) -> None:
    ids = IdFactory()
    parent = _cpp_parent(tmp_path, ids)
    source = Path(parent.source_path).read_text(encoding="utf-8")
    commented = source + "\n// a trailing comment\n"
    valid, error = validate_meaningful_change(source, commented)
    assert not valid
    assert error and "identical" in error


def test_meaningful_change_rejects_whitespace_only_edit(tmp_path: Path) -> None:
    ids = IdFactory()
    parent = _cpp_parent(tmp_path, ids)
    source = Path(parent.source_path).read_text(encoding="utf-8")
    reformatted = source.replace("    queue_.clear();", "    queue_.clear();   ", 1)
    assert reformatted != source
    valid, _ = validate_meaningful_change(source, reformatted)
    assert not valid


def test_meaningful_change_rejects_push_back_to_emplace_back_rename(tmp_path: Path) -> None:
    """Regression test: this exact rename (plus stripped comments) was the
    entire diff of a real 10-generation run's "best" individual vs the
    unmutated baseline -- functionally a no-op that must now be rejected."""
    ids = IdFactory()
    parent = _cpp_parent(tmp_path, ids)
    source = Path(parent.source_path).read_text(encoding="utf-8")
    trivial = source.replace("push_back(", "emplace_back(")
    assert trivial != source
    valid, error = validate_meaningful_change(source, trivial)
    assert not valid
    assert error and "equivalent tokens" in error


def test_meaningful_change_accepts_real_algorithm_edit(tmp_path: Path) -> None:
    ids = IdFactory()
    parent = _cpp_parent(tmp_path, ids)
    source = Path(parent.source_path).read_text(encoding="utf-8")
    changed = _non_trivial_change(source)
    valid, error = validate_meaningful_change(source, changed)
    assert valid
    assert error is None


def test_cpp_validation_rejects_dwa_related_changes(tmp_path: Path) -> None:
    ids = IdFactory()
    parent = _cpp_parent(tmp_path, ids)
    source = Path(parent.source_path).read_text(encoding="utf-8")
    source = source.replace(
        "namespace global_planner {",
        "namespace global_planner {\n// modify DWAPlannerROS",
        1,
    )
    valid, error = validate_cpp_source(source)
    assert not valid
    assert error and "DWA" in error
