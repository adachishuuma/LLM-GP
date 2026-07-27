from __future__ import annotations

from pathlib import Path

from llm_gp.config import AppConfig
from llm_gp.database import ExperimentDatabase
from llm_gp.evolution import EvolutionEngine
from llm_gp.report import generate_report


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
    outputs = generate_report(test_config.database_path, tmp_path / "analysis")
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

    outputs = generate_report(test_config.database_path, tmp_path / "failed_baseline")
    assert all(path.is_file() for path in outputs.values())
    markdown = outputs["markdown"].read_text(encoding="utf-8")
    assert "初期評価成功数: `0/" in markdown
    assert "改善率は算出できません" in markdown
