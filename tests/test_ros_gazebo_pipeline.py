from __future__ import annotations

import random
from dataclasses import replace
from pathlib import Path

from llm_gp.config import AppConfig, load_config
from llm_gp.database import ExperimentDatabase
from llm_gp.evolution import EvolutionEngine
from llm_gp.models import EvaluationResult, Individual, MutationContext
from llm_gp.operators import (
    CppRelaxedAStarMutationOperator,
    IdFactory,
    OpenAIGPT4oMiniMutationOperator,
    initial_cpp_source,
)
from llm_gp.validation import validate_cpp_source, validate_individual


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


def test_openai_mutation_extracts_cpp_fence_surrounded_by_prose(tmp_path: Path) -> None:
    ids = IdFactory()
    parent = _cpp_parent(tmp_path, ids)
    source = Path(parent.source_path).read_text(encoding="utf-8")
    client = FakeOpenAIClient(
        [f"Here's the improved implementation.\n\n```cpp\n{source}\n```\n\nImprovements: safer heap use."]
    )
    operator = OpenAIGPT4oMiniMutationOperator(
        ids, max_retries=2, client=client
    )

    child = operator.mutate(
        parent,
        MutationContext(1, "island_1", tmp_path, source_suffix=".cpp"),
    )
    generated = Path(child.source_path).read_text(encoding="utf-8")

    assert generated.strip() == source.strip()
    assert "```" not in generated
    assert "Here's" not in generated
    assert validate_individual(child) == (True, None)
    assert len(client.responses.inputs) == 1


def test_openai_mutation_retries_invalid_generated_source(tmp_path: Path) -> None:
    ids = IdFactory()
    parent = _cpp_parent(tmp_path, ids)
    source = Path(parent.source_path).read_text(encoding="utf-8")
    client = FakeOpenAIClient(
        [
            "Here is an explanation without any source code.",
            f"```cpp\n{source}\n```",
        ]
    )
    operator = OpenAIGPT4oMiniMutationOperator(
        ids, max_retries=2, client=client
    )

    child = operator.mutate(
        parent,
        MutationContext(1, "island_1", tmp_path, source_suffix=".cpp"),
    )

    assert validate_individual(child) == (True, None)
    assert len(client.responses.inputs) == 2
    assert "previous response was rejected" in client.responses.inputs[1]
    assert "2 generation attempt(s)" in child.change_history[0]


def test_cpp_validation_rejects_markdown_and_explanatory_prefix() -> None:
    assert validate_cpp_source("```cpp\nnamespace global_planner {}\n```")[0] is False
    assert validate_cpp_source("Here is the code.\n#include <global_planner/rastar.h>")[0] is False


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
