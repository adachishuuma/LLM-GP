from __future__ import annotations

import csv
from pathlib import Path

from llm_gp.config import AppConfig
from llm_gp.database import ExperimentDatabase
from llm_gp.evolution import EvolutionEngine
from llm_gp.models import EvaluationResult, Individual, LLMCallRecord
from llm_gp.report import (
    generate_report,
    load_rows,
    load_rows_with_corrected_generation_zero_fitness,
    select_baseline_and_best,
    token_usage_summary,
)


def test_generation_and_algorithm_report_is_generated(
    test_config: AppConfig, tmp_path: Path
) -> None:
    database = ExperimentDatabase(test_config.database_path, reset=True)
    try:
        engine = EvolutionEngine(test_config, database=database)
        engine.initialize()
        engine.run_generation(1)
        database.commit()
    finally:
        database.close()
    outputs = generate_report(test_config.database_path, tmp_path / "analysis", test_config.fitness)
    assert all(path.is_file() for path in outputs.values())
    markdown = outputs["markdown"].read_text(encoding="utf-8")
    assert "世代 0" in markdown
    assert "世代 1" in markdown
    assert "crossover" in markdown or "mutation" in markdown
    metric_markdown = outputs["metric_markdown"].read_text(encoding="utf-8")
    assert "経路生成時間" in metric_markdown
    assert "経路長" in metric_markdown
    assert "到達時間" in metric_markdown
    metric_csv = outputs["metric_csv"].read_text(encoding="utf-8-sig")
    assert "planning_time_improvement_vs_initial_percent" in metric_csv
    repetition_csv = outputs["repetition_csv"].read_text(encoding="utf-8-sig")
    assert "successful_repetitions" in repetition_csv
    assert "planning_time_stddev" in repetition_csv
    assert outputs["best_source"].name in {"best_algorithm.py", "best_algorithm.cpp"}
    assert outputs["best_source"].read_text(encoding="utf-8")
    assert "個体ID" in outputs["best_info"].read_text(encoding="utf-8")
    assert outputs["best_diff"].is_file()


def test_report_survives_when_every_baseline_evaluation_failed(
    test_config: AppConfig, tmp_path: Path
) -> None:
    database = ExperimentDatabase(test_config.database_path, reset=True)
    try:
        engine = EvolutionEngine(test_config, database=database)
        engine.initialize()
        engine.run_generation(1)
        database.connection.execute(
            "UPDATE evaluations SET success = 0, fitness = 0.0 "
            "WHERE individual_id IN "
            "(SELECT individual_id FROM individuals WHERE generation = 0)"
        )
        database.commit()
    finally:
        database.close()

    outputs = generate_report(test_config.database_path, tmp_path / "failed_baseline", test_config.fitness)
    assert all(path.is_file() for path in outputs.values())
    markdown = outputs["markdown"].read_text(encoding="utf-8")
    assert "初期評価成功数: `0/" in markdown
    assert "改善率は算出できません" in markdown


def test_token_usage_is_aggregated_by_generation_and_summarized(
    test_config: AppConfig, tmp_path: Path
) -> None:
    """LLM token usage (added so 30-generation runs can report how much LLM
    cost they consumed) must be summed per generation in the report CSV and
    across the whole experiment by token_usage_summary(), while calls with
    no usage data (mock operators, or responses that omit usage) are counted
    separately instead of silently treated as zero tokens.

    engine.run_generation(1) also logs its own (token-less) mock LLMCallRecords
    for every generated individual, so this only asserts on the token sums
    (unaffected, since a mock call's token fields are None and contribute 0)
    and on the *increase* in call counts rather than absolute counts.
    """
    database = ExperimentDatabase(test_config.database_path, reset=True)
    try:
        engine = EvolutionEngine(test_config, database=database)
        engine.initialize()
        engine.run_generation(1)
        baseline_totals = token_usage_summary(test_config.database_path)
        gen0_individual = database.connection.execute(
            "SELECT individual_id FROM individuals WHERE generation = 0 LIMIT 1"
        ).fetchone()[0]
        gen1_individual = database.connection.execute(
            "SELECT individual_id FROM individuals WHERE generation = 1 LIMIT 1"
        ).fetchone()[0]
        database.save_llm_call(
            gen0_individual,
            LLMCallRecord(
                "gpt-4o-mini", "prompt-0", "response-0", True,
                prompt_tokens=100, completion_tokens=20, total_tokens=120,
            ),
        )
        database.save_llm_call(
            gen1_individual,
            LLMCallRecord(
                "gpt-4o-mini", "prompt-1", "response-1", True,
                prompt_tokens=200, completion_tokens=50, total_tokens=250,
            ),
        )
        database.save_llm_call(
            gen1_individual,
            LLMCallRecord("gpt-4o-mini", "prompt-2", "response-2", False, "no usage returned"),
        )
        database.commit()
    finally:
        database.close()

    totals = token_usage_summary(test_config.database_path)
    assert totals["total_tokens"] == 370
    assert totals["prompt_tokens"] == 300
    assert totals["completion_tokens"] == 70
    assert totals["calls"] - baseline_totals["calls"] == 3
    assert totals["successful_calls"] - baseline_totals["successful_calls"] == 2
    assert totals["calls_missing_usage"] - baseline_totals["calls_missing_usage"] == 1

    outputs = generate_report(test_config.database_path, tmp_path / "token_usage", test_config.fitness)
    assert all(path.is_file() for path in outputs.values())
    with outputs["token_usage_csv"].open(encoding="utf-8-sig", newline="") as handle:
        rows = {int(row["generation"]): row for row in csv.DictReader(handle)}
    assert int(rows[0]["prompt_tokens"]) == 100
    assert int(rows[0]["completion_tokens"]) == 20
    assert int(rows[0]["total_tokens"]) == 120
    assert int(rows[1]["prompt_tokens"]) == 200
    assert int(rows[1]["completion_tokens"]) == 50
    assert int(rows[1]["total_tokens"]) == 250
    markdown = outputs["markdown"].read_text(encoding="utf-8")
    assert "LLMトークン使用量" in markdown
    assert "`370`" in markdown


def test_select_baseline_and_best_requires_generation_zero_corrected_fitness(
    test_config: AppConfig, tmp_path: Path
) -> None:
    """Regression test for the bug documented in
    docs/lattice_fork_minmax_5gen_experiment_report.md: evaluations.fitness
    only ever holds a generation-0 individual's *last* rescoring, which can
    disagree with its true generation-0-pool-relative fitness (a survivor is
    rescored every generation it lives on, since fitness is generation-
    relative under min-max scaling). scripts/verify_generation_gain.py and
    llm_gp/main.py's automatic post-run verification used to call
    select_baseline_and_best() directly on plain load_rows() and could
    therefore name the wrong individual as "the initial baseline" -- they
    must go through load_rows_with_corrected_generation_zero_fitness()
    instead, as generate_report() already did.
    """
    database = ExperimentDatabase(test_config.database_path, reset=True)
    try:
        source_a = tmp_path / "a.cpp"
        source_b = tmp_path / "b.cpp"
        source_a.write_text("// a", encoding="utf-8")
        source_b.write_text("// b", encoding="utf-8")

        # True generation-0 ranking: "a" beats "b" on every raw metric (lower
        # is better), so a correct recomputation against their shared
        # generation-0 pool must rank "a" first. The fitness= values stored
        # below are the *opposite* ranking, simulating what a later
        # rescoring (as a surviving parent, re-evaluated against a different
        # generation's pool) leaves behind in evaluations.fitness.
        individual_a = Individual(
            individual_id="gen0_a", generation=0, current_island="main",
            origin_island="main", source_path=str(source_a), parent_ids=[],
            operator_type="initial", valid=True, evaluation_success=True,
            fitness=0.1,
        )
        individual_b = Individual(
            individual_id="gen0_b", generation=0, current_island="main",
            origin_island="main", source_path=str(source_b), parent_ids=[],
            operator_type="initial", valid=True, evaluation_success=True,
            fitness=0.9,
        )
        database.save_individual(individual_a)
        database.save_individual(individual_b)
        database.save_evaluation(
            individual_a,
            EvaluationResult(
                success=True, planning_time=0.1, path_length=10.0,
                arrival_time=50.0, node_expansions=100.0,
            ),
        )
        database.save_evaluation(
            individual_b,
            EvaluationResult(
                success=True, planning_time=0.2, path_length=20.0,
                arrival_time=100.0, node_expansions=200.0,
            ),
        )
        database.save_population_membership("main", 0, individual_a)
        database.save_population_membership("main", 0, individual_b)
        database.commit()
    finally:
        database.close()

    uncorrected_rows = load_rows(test_config.database_path)
    wrong_baseline, _ = select_baseline_and_best(uncorrected_rows)
    assert wrong_baseline.individual_id == "gen0_b", (
        "fixture assumption broken: the stale stored fitness (b=0.9 > a=0.1) "
        "should mislead the uncorrected path -- this documents the bug being "
        "regression-tested, not the fix itself"
    )

    corrected_rows = load_rows_with_corrected_generation_zero_fitness(
        test_config.database_path, test_config.fitness
    )
    correct_baseline, _ = select_baseline_and_best(corrected_rows)
    assert correct_baseline.individual_id == "gen0_a"
