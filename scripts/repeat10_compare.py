#!/usr/bin/env python3
"""Re-evaluate two fixed individuals (initial vs. best) N times each and compare averages.

Reuses the project's RosGazeboEvaluator so each run is a real WSL/Gazebo trial,
independent of repetitions_per_goal in the experiment config. Results are written
next to the existing analysis outputs of the given run directory.

Example:
  python scripts/repeat10_compare.py \
      --run-dir experiment_results/ten_generation_repeated/run_20260722_053957 \
      --repetitions 10
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from llm_gp.config import load_config
from llm_gp.evaluator import RosGazeboEvaluator
from llm_gp.fitness import calculate_fitness
from llm_gp.models import Individual
from llm_gp.run_lock import ros_gazebo_run_lock
from llm_gp.stats import permutation_test_p_value, relative_improvement_percent

_COMPARISON_METRICS = ("planning_time", "path_length", "arrival_time", "node_expansions")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path,
        default=PROJECT_ROOT / "config" / "ros_gazebo_10gen_repeated.yaml",
        help="YAML config to reuse for ROS/Gazebo settings and fitness weights",
    )
    parser.add_argument("--run-dir", type=Path, required=True,
                         help="Existing run directory containing individual_sources/")
    parser.add_argument("--repetitions", type=int, default=10)
    parser.add_argument("--initial-id", default="ind_000001",
                         help="Individual id of the initial/baseline candidate")
    parser.add_argument("--best-id", default="ind_000050",
                         help="Individual id of the overall-best candidate (for labeling only)")
    parser.add_argument("--best-source-id", default="ind_000012",
                         help="Individual id whose .cpp actually holds the best individual's "
                              "source (may differ from --best-id after dedup)")
    parser.add_argument("--output", type=Path, default=None,
                         help="Output CSV path (default: <run-dir>/analysis/repeat<N>_statistics.csv)")
    return parser.parse_args()


def _load_successful_payloads(log_directory: Path, individual_id: str) -> list[dict]:
    files = sorted(log_directory.glob(f"{individual_id}_repetition_*.json"))
    payloads = []
    for file_path in files:
        try:
            payloads.append(json.loads(file_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return payloads


def summarize(log_directory: Path, individual_id: str) -> dict[str, object]:
    payloads = _load_successful_payloads(log_directory, individual_id)
    successful = [p for p in payloads if p.get("success")]
    row: dict[str, object] = {
        "individual_id": individual_id,
        "attempted_repetitions": len(payloads),
        "successful_repetitions": len(successful),
        "success_rate_percent": (len(successful) / len(payloads) * 100.0) if payloads else None,
    }
    for metric in _COMPARISON_METRICS:
        values = [float(p[metric]) for p in successful if p.get(metric) is not None]
        row[f"{metric}_mean"] = statistics.mean(values) if values else None
        row[f"{metric}_stddev"] = statistics.stdev(values) if len(values) >= 2 else None
    return row


def raw_metric_values(log_directory: Path, individual_id: str, metric: str) -> list[float]:
    """Raw per-repetition values for one metric, successful runs only."""
    payloads = _load_successful_payloads(log_directory, individual_id)
    return [
        float(p[metric])
        for p in payloads
        if p.get("success") and p.get(metric) is not None
    ]


def compare_candidates(
    log_directory: Path, initial_id: str, best_id: str
) -> list[dict[str, object]]:
    """Per-metric relative improvement (%) and permutation-test significance
    between the baseline's and best candidate's repeated evaluations."""
    rows: list[dict[str, object]] = []
    for metric in _COMPARISON_METRICS:
        initial_values = raw_metric_values(log_directory, initial_id, metric)
        best_values = raw_metric_values(log_directory, best_id, metric)
        initial_mean = statistics.mean(initial_values) if initial_values else None
        best_mean = statistics.mean(best_values) if best_values else None
        p_value = permutation_test_p_value(initial_values, best_values)
        rows.append({
            "metric": metric,
            "initial_n": len(initial_values),
            "initial_mean": initial_mean,
            "best_n": len(best_values),
            "best_mean": best_mean,
            "relative_improvement_percent": relative_improvement_percent(initial_mean, best_mean),
            "p_value": p_value,
            "significant_at_0.05": (p_value is not None and p_value < 0.05),
        })
    return rows


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    if config.evaluation.ros_gazebo is None:
        print("Config has no evaluation.ros_gazebo section", file=sys.stderr)
        return 2

    source_directory = args.run_dir / "individual_sources"
    initial_source = source_directory / f"{args.initial_id}.cpp"
    best_source = source_directory / f"{args.best_source_id}.cpp"
    for path in (initial_source, best_source):
        if not path.is_file():
            print(f"Missing source file: {path}", file=sys.stderr)
            return 2

    output_dir = args.run_dir / "analysis"
    output_dir.mkdir(parents=True, exist_ok=True)
    log_directory = output_dir / f"repeat{args.repetitions}_logs"
    output_csv = args.output or output_dir / f"repeat{args.repetitions}_statistics.csv"

    evaluator = RosGazeboEvaluator(
        settings=config.evaluation.ros_gazebo,
        fixed_goal=config.evaluation.fixed_goal,
        timeout_seconds=config.evaluation.timeout_seconds,
        repetitions=args.repetitions,
        build_timeout_seconds=config.validation.build_timeout_seconds,
        log_directory=log_directory,
        project_root=PROJECT_ROOT,
    )

    candidates = [
        (f"initial_{args.initial_id}", initial_source),
        (f"best_{args.best_id}", best_source),
    ]

    rows = []
    with ros_gazebo_run_lock(PROJECT_ROOT):
        for log_id, source_path in candidates:
            individual = Individual(
                individual_id=log_id,
                generation=0,
                current_island="manual",
                origin_island="manual",
                source_path=str(source_path),
                parent_ids=[],
                operator_type="manual_repeat_check",
            )
            print(f"Evaluating {log_id} x{args.repetitions} ...", flush=True)
            result = evaluator.evaluate(individual)
            fitness = calculate_fitness(result, config.fitness)
            print(
                f"  -> success={result.success} fitness={fitness:.6f} "
                f"planning={result.planning_time} path={result.path_length} "
                f"arrival={result.arrival_time}"
            )
            row = summarize(log_directory, log_id)
            row["fitness_mean"] = fitness
            rows.append(row)

    fields = [
        "individual_id", "attempted_repetitions", "successful_repetitions",
        "success_rate_percent", "fitness_mean",
        "planning_time_mean", "planning_time_stddev",
        "path_length_mean", "path_length_stddev",
        "arrival_time_mean", "arrival_time_stddev",
        "node_expansions_mean", "node_expansions_stddev",
    ]
    with output_csv.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nWrote {output_csv}")

    comparison_rows = compare_candidates(
        log_directory, f"initial_{args.initial_id}", f"best_{args.best_id}"
    )
    comparison_csv = output_dir / f"repeat{args.repetitions}_significance.csv"
    comparison_fields = [
        "metric", "initial_n", "initial_mean", "best_n", "best_mean",
        "relative_improvement_percent", "p_value", "significant_at_0.05",
    ]
    with comparison_csv.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=comparison_fields)
        writer.writeheader()
        writer.writerows(comparison_rows)
    print(f"Wrote {comparison_csv}")

    print(
        f"\n{'metric':<18}{'initial_mean':>14}{'best_mean':>14}"
        f"{'rel.improve %':>15}{'p-value':>10}{'sig@.05':>9}"
    )
    for row in comparison_rows:
        def fmt(value: object, digits: int = 4) -> str:
            return f"{value:.{digits}f}" if isinstance(value, float) else "   n/a"
        print(
            f"{row['metric']:<18}{fmt(row['initial_mean']):>14}{fmt(row['best_mean']):>14}"
            f"{fmt(row['relative_improvement_percent'], 2):>15}"
            f"{fmt(row['p_value']):>10}{str(row['significant_at_0.05']):>9}"
        )
    print(
        "\nrel.improve % is (initial - best) / initial * 100: positive means the "
        "best candidate is lower/better. p-value is a two-sided permutation test "
        "on the two repeated-evaluation samples (exact when feasible, else a "
        "seeded 20000-iteration Monte Carlo estimate); it answers 'could this gap "
        "plausibly be run-to-run Gazebo noise?', not 'is the effect large'."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
