from __future__ import annotations

from pathlib import Path

from llm_gp.config import AppConfig
from llm_gp.fitness import calculate_fitness, compute_generation_stats
from llm_gp.models import EvaluationResult, Individual
from llm_gp.validation import validate_individual


def test_fitness_follows_configuration(test_config: AppConfig) -> None:
    result = EvaluationResult(True, 0.15, 12.0, 36.0)
    assert calculate_fitness(result, test_config.fitness, None) > 0.0


def test_non_finite_evaluation_receives_failure_fitness(test_config: AppConfig) -> None:
    result = EvaluationResult(True, float("nan"), 12.0, 36.0)
    assert calculate_fitness(result, test_config.fitness, None) == test_config.fitness.failure_fitness


def test_fitness_scores_node_expansions_when_present(test_config: AppConfig) -> None:
    # node_expansions is the search-cost metric fitness actually scores now;
    # planning_time is only a fallback for results predating this field.
    with_expansions = EvaluationResult(True, 0.15, 12.0, 36.0, node_expansions=500.0)
    assert calculate_fitness(with_expansions, test_config.fitness, None) > 0.0


def test_fewer_node_expansions_yields_higher_fitness(test_config: AppConfig) -> None:
    fewer = EvaluationResult(True, 0.15, 12.0, 36.0, node_expansions=100.0)
    more = EvaluationResult(True, 0.15, 12.0, 36.0, node_expansions=1900.0)
    # Fitness is normalized against the min/max of the generation the two
    # results belong to, not against a fixed reference constant.
    stats = compute_generation_stats([fewer, more])
    assert calculate_fitness(fewer, test_config.fitness, stats) > calculate_fitness(
        more, test_config.fitness, stats
    )


def test_non_finite_node_expansions_receives_failure_fitness(test_config: AppConfig) -> None:
    result = EvaluationResult(True, 0.15, 12.0, 36.0, node_expansions=float("nan"))
    assert calculate_fitness(result, test_config.fitness, None) == test_config.fitness.failure_fitness


def test_evaluation_result_without_node_expansions_falls_back_to_planning_time(
    test_config: AppConfig,
) -> None:
    # Backward compatibility: old-style positional construction (as used
    # throughout this test suite and by MockEvaluator before this field
    # existed) must still produce a sensible fitness via planning_time.
    legacy = EvaluationResult(True, 0.15, 12.0, 36.0)
    assert legacy.node_expansions is None
    assert calculate_fitness(legacy, test_config.fitness, None) > 0.0


def test_generation_stats_none_when_fewer_than_two_successes(test_config: AppConfig) -> None:
    only_success = EvaluationResult(True, 0.15, 12.0, 36.0, node_expansions=500.0)
    failure = EvaluationResult(False, None, None, None, "boom")
    assert compute_generation_stats([only_success, failure]) is None
    # With no range to normalize against, the lone success gets full marks.
    settings = test_config.fitness
    assert calculate_fitness(only_success, settings, None) == (
        settings.planning_weight + settings.path_weight + settings.arrival_weight
    )


def test_generation_stats_rank_best_and_worst_at_the_extremes(test_config: AppConfig) -> None:
    best = EvaluationResult(True, 0.1, 10.0, 30.0, node_expansions=100.0)
    middle = EvaluationResult(True, 0.2, 20.0, 60.0, node_expansions=550.0)
    worst = EvaluationResult(True, 0.3, 30.0, 90.0, node_expansions=1000.0)
    stats = compute_generation_stats([best, middle, worst])
    assert stats is not None
    settings = test_config.fitness
    full_marks = settings.planning_weight + settings.path_weight + settings.arrival_weight
    assert calculate_fitness(best, settings, stats) == full_marks
    assert calculate_fitness(worst, settings, stats) == 0.0
    assert (
        calculate_fitness(worst, settings, stats)
        < calculate_fitness(middle, settings, stats)
        < calculate_fitness(best, settings, stats)
    )


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
