from __future__ import annotations

import argparse
import json
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path

from .config import AppConfig, load_config
from .evolution import EvolutionEngine
from .report import generate_report
from .run_lock import ros_gazebo_run_lock


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the LLM-GP roulette island experiment")
    parser.add_argument("--config", required=True, help="Path to a YAML configuration file")
    return parser


def prepare_run_output_directory(config: AppConfig, config_path: Path) -> tuple[AppConfig, Path]:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_directory = config.database_path.parent / f"run_{timestamp}"
    run_directory.mkdir(parents=True, exist_ok=True)

    config_path = config_path.resolve()
    database_path = run_directory / config.database_path.name
    source_directory = run_directory / "individual_sources"
    generation_csv = run_directory / config.generation_csv.name
    prepared_config = AppConfig(
        evolution=config.evolution,
        selection=config.selection,
        migration=config.migration,
        fitness=config.fitness,
        validation=config.validation,
        evaluation=config.evaluation,
        llm=config.llm,
        database_path=database_path,
        source_directory=source_directory,
        generation_csv=generation_csv,
        islands=config.islands,
        random_seed=config.random_seed,
    )
    (run_directory / "run_manifest.json").write_text(
        json.dumps(
            {
                "config_path": str(config_path),
                "database_path": str(database_path),
                "source_directory": str(source_directory),
                "generation_csv": str(generation_csv),
                "timestamp": timestamp,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_directory / config_path.name).write_text(config_path.read_text(encoding="utf-8"), encoding="utf-8")
    return prepared_config, run_directory


def main() -> None:
    args = build_parser().parse_args()
    config = load_config(args.config)
    prepared_config, run_directory = prepare_run_output_directory(config, Path(args.config))
    project_root = Path(__file__).parents[1]
    lock = (
        ros_gazebo_run_lock(project_root)
        if prepared_config.evaluation.evaluator_type == "ros_gazebo"
        else nullcontext()
    )
    with lock:
        engine = EvolutionEngine(prepared_config)
        summaries = engine.run()
        analysis_directory = run_directory / "analysis"
        report_outputs = generate_report(prepared_config.database_path, analysis_directory)
    print(
        f"Completed {len(summaries)} generations; "
        f"generated={engine.total_generated}, migrations={engine.total_migrations}"
    )
    print(f"SQLite: {prepared_config.database_path}")
    print(f"CSV: {prepared_config.generation_csv}")
    print(f"Analysis: {report_outputs['markdown']}")
    if "best_source" in report_outputs:
        print(f"Best algorithm: {report_outputs['best_source']}")
    print(f"Run directory: {run_directory}")


if __name__ == "__main__":
    main()
