from __future__ import annotations

from pathlib import Path

from llm_gp.config import AppConfig
from llm_gp.fitness import calculate_fitness
from llm_gp.models import EvaluationResult, Individual
from llm_gp.validation import validate_individual


def test_fitness_follows_configuration(test_config: AppConfig) -> None:
    result = EvaluationResult(True, 0.15, 12.0, 36.0)
    assert calculate_fitness(result, test_config.fitness) > 0.0


def test_non_finite_evaluation_receives_failure_fitness(test_config: AppConfig) -> None:
    result = EvaluationResult(True, float("nan"), 12.0, 36.0)
    assert calculate_fitness(result, test_config.fitness) == test_config.fitness.failure_fitness


def test_fitness_scores_node_expansions_when_present(test_config: AppConfig) -> None:
    # node_expansions is the search-cost metric fitness actually scores now;
    # planning_time is only a fallback for results predating this field.
    with_expansions = EvaluationResult(True, 0.15, 12.0, 36.0, node_expansions=500.0)
    assert calculate_fitness(with_expansions, test_config.fitness) > 0.0


def test_fewer_node_expansions_yields_higher_fitness(test_config: AppConfig) -> None:
    fewer = EvaluationResult(True, 0.15, 12.0, 36.0, node_expansions=100.0)
    more = EvaluationResult(True, 0.15, 12.0, 36.0, node_expansions=1900.0)
    assert calculate_fitness(fewer, test_config.fitness) > calculate_fitness(more, test_config.fitness)


def test_non_finite_node_expansions_receives_failure_fitness(test_config: AppConfig) -> None:
    result = EvaluationResult(True, 0.15, 12.0, 36.0, node_expansions=float("nan"))
    assert calculate_fitness(result, test_config.fitness) == test_config.fitness.failure_fitness


def test_evaluation_result_without_node_expansions_falls_back_to_planning_time(
    test_config: AppConfig,
) -> None:
    # Backward compatibility: old-style positional construction (as used
    # throughout this test suite and by MockEvaluator before this field
    # existed) must still produce a sensible fitness via planning_time.
    legacy = EvaluationResult(True, 0.15, 12.0, 36.0)
    assert legacy.node_expansions is None
    assert calculate_fitness(legacy, test_config.fitness) > 0.0


def test_fixed_goal_is_loaded_and_passed_to_mock_evaluator(test_config: AppConfig) -> None:
    from llm_gp.database import ExperimentDatabase
    from llm_gp.evolution import EvolutionEngine

    assert test_config.evaluation.fixed_goal.as_tuple() == (1.69, 0.954, -0.00143)
    database = ExperimentDatabase(test_config.database_path, reset=True)
    try:
        engine = EvolutionEngine(test_config, database=database)
        assert engine.evaluator.fixed_goal.as_tuple() == (1.69, 0.954, -0.00143)
    finally:
        database.close()


def test_validator_rejects_missing_plan(tmp_path: Path) -> None:
    path = tmp_path / "bad.py"
    path.write_text("VALUE = 1\n", encoding="utf-8")
    individual = Individual("bad", 1, "astar", "astar", str(path), [], "test")
    valid, error = validate_individual(individual)
    assert not valid
    assert error and "plan" in error


def test_invalid_individual_is_not_sent_to_evaluator(
    test_config: AppConfig, tmp_path: Path
) -> None:
    from llm_gp.database import ExperimentDatabase
    from llm_gp.evolution import EvolutionEngine

    class CountingEvaluator:
        calls = 0

        def evaluate(self, individual: Individual) -> EvaluationResult:
            self.calls += 1
            return EvaluationResult(True, 0.1, 8.0, 15.0)

    bad_path = tmp_path / "invalid.py"
    bad_path.write_text("VALUE = 1\n", encoding="utf-8")
    bad = Individual("invalid", 1, "island_1", "island_1", str(bad_path), [], "test")
    evaluator = CountingEvaluator()
    database = ExperimentDatabase(test_config.database_path, reset=True)
    try:
        engine = EvolutionEngine(test_config, database=database, evaluator=evaluator)
        engine._process_individual(bad)
        assert evaluator.calls == 0
        assert bad.fitness == test_config.fitness.failure_fitness
    finally:
        database.close()
