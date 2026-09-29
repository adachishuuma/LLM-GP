from __future__ import annotations

import argparse
import json
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path

from .config import AppConfig, load_config
from .evaluator import cleanup_stray_ros_gazebo_processes
from .evolution import EvolutionEngine
from .report import (
    generate_report,
    load_rows_with_corrected_generation_zero_fitness,
    select_baseline_and_best,
    token_usage_summary,
)
from .run_lock import ros_gazebo_run_lock
from .verification import run_comparison


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the LLM-GP roulette island experiment")
    parser.add_argument("--config", required=True, help="Path to a YAML configuration file")
    parser.add_argument(
        "--verify-repetitions", type=int, default=10,
        help="After the run (ros_gazebo evaluator only), re-evaluate the initial baseline "
             "and the overall-best individual this many times each and compare, to check "
             "whether the generational gain survives repeated Gazebo trials or is just "
             "evaluation noise. Pass 0 to skip this step. Default: 10.",
    )
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
        try:
            engine = EvolutionEngine(prepared_config)
            summaries = engine.run()
            analysis_directory = run_directory / "analysis"
            report_outputs = generate_report(
                prepared_config.database_path, analysis_directory, prepared_config.fitness
            )
            if (
                args.verify_repetitions > 0
                and prepared_config.evaluation.evaluator_type == "ros_gazebo"
            ):
                _verify_generational_gain(
                    prepared_config, analysis_directory, project_root, args.verify_repetitions
                )
        finally:
            if prepared_config.evaluation.evaluator_type == "ros_gazebo":
                cleanup_stray_ros_gazebo_processes(
                    prepared_config.evaluation.ros_gazebo, project_root
                )
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
    token_totals = token_usage_summary(prepared_config.database_path)
    if token_totals["calls"] > 0:
        print(
            f"LLM tokens: total={token_totals['total_tokens']} "
            f"(prompt={token_totals['prompt_tokens']}, completion={token_totals['completion_tokens']}) "
            f"over {token_totals['calls']} call(s), "
            f"{token_totals['calls_missing_usage']} without usage data"
        )


def _verify_generational_gain(
    config: AppConfig, analysis_directory: Path, project_root: Path, repetitions: int
) -> None:
    """Re-evaluate the initial baseline and the overall-best individual
    `repetitions` times each and report whether the gap between them holds up
    under repeated Gazebo trials, or is within run-to-run noise. Runs inside
    the same ros_gazebo_run_lock already held for the evolutionary run that
    produced these individuals (see scripts/verify_generation_gain.py for the
    standalone, run-directory-driven version of this same check)."""
    rows = load_rows_with_corrected_generation_zero_fitness(config.database_path, config.fitness)
    baseline, best = select_baseline_and_best(rows)
    if best is None:
        print("\nSkipping initial-vs-best verification: no successful evaluations to compare.")
        return
    print(
        f"\nVerifying generational gain x{repetitions} each: "
        f"initial {baseline.individual_id} (gen {baseline.generation}, fitness={baseline.fitness:.6f}) "
        f"vs best {best.individual_id} (gen {best.generation}, fitness={best.fitness:.6f})"
    )
    if baseline.individual_id == best.individual_id:
        print(
            "Note: the initial baseline is already the overall-best individual in this run; "
            "this only estimates single-individual noise, there is no generational gain to verify."
        )
    run_comparison(
        project_root=project_root,
        config=config,
        repetitions=repetitions,
        initial_label=f"initial_{baseline.individual_id}",
        initial_source=baseline.source_path,
        best_label=f"best_{best.individual_id}",
        best_source=best.source_path,
        output_dir=analysis_directory,
    )


if __name__ == "__main__":
    main()
