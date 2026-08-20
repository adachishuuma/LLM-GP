"""Re-evaluate an initial-vs-best pair of individuals N times each and report
whether the gap between them survives repeated Gazebo trials, or is just
evaluation noise.

Shared by scripts/repeat10_compare.py (manual individual ids),
scripts/verify_generation_gain.py (auto-detected from a run directory), and
llm_gp/main.py (automatic post-run check right after a GP experiment).
"""
from __future__ import annotations

import csv
import json
import statistics
import sys
from pathlib import Path

from .config import AppConfig
from .evaluator import RosGazeboEvaluator
from .fitness import calculate_fitness
from .models import Individual
from .stats import permutation_test_p_value, relative_improvement_percent

COMPARISON_METRICS = ("planning_time", "path_length", "arrival_time", "node_expansions")


def format_metric(value: object, digits: int = 4) -> str:
    return f"{value:.{digits}f}" if isinstance(value, float) else "n/a"


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
    for metric in COMPARISON_METRICS:
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
    for metric in COMPARISON_METRICS:
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


def run_comparison(
    *,
    project_root: Path,
    config: AppConfig,
    repetitions: int,
    initial_label: str,
    initial_source: Path,
    best_label: str,
    best_source: Path,
    output_dir: Path,
    output_csv: Path | None = None,
) -> int:
    """Evaluate `initial_source` vs. `best_source` `repetitions` times each,
    write the statistics/significance CSVs under `output_dir`, and print a
    summary table.

    Does NOT acquire ros_gazebo_run_lock or sweep stray processes itself --
    the caller must already hold the lock for the duration of this call (and
    is expected to call evaluator.cleanup_stray_ros_gazebo_processes()
    afterwards), since some callers (llm_gp/main.py) invoke this from inside
    a lock they are already holding for the GP run that produced these
    individuals.
    """
    if config.evaluation.ros_gazebo is None:
        print("Config has no evaluation.ros_gazebo section", file=sys.stderr)
        return 2
    for path in (initial_source, best_source):
        if not path.is_file():
            print(f"Missing source file: {path}", file=sys.stderr)
            return 2

    output_dir.mkdir(parents=True, exist_ok=True)
    log_directory = output_dir / f"repeat{repetitions}_logs"
    output_csv = output_csv or output_dir / f"repeat{repetitions}_statistics.csv"

    evaluator = RosGazeboEvaluator(
        settings=config.evaluation.ros_gazebo,
        fixed_goal=config.evaluation.fixed_goal,
        timeout_seconds=config.evaluation.timeout_seconds,
        repetitions=repetitions,
        build_timeout_seconds=config.validation.build_timeout_seconds,
        log_directory=log_directory,
        project_root=project_root,
        # Without these, every repetition uses the exact fixed_goal corner
        # point instead of sampling across the same range the GP experiment
        # itself evaluated against -- see llm_gp/evolution.py's
        # _build_evaluator(), which passes both. Omitting them here previously
        # made every repeated trial target one single (and possibly harder to
        # reach) point instead of a representative spread of goals.
        goal_x_range=config.evaluation.goal_x_range,
        goal_y_range=config.evaluation.goal_y_range,
    )

    candidates = [(initial_label, initial_source), (best_label, best_source)]

    rows = []
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
        print(f"Evaluating {log_id} x{repetitions} ...", flush=True)
        result = evaluator.evaluate(individual)
        # evaluator.evaluate() requires every repetition to succeed before it
        # reports success=True at all (matching how the GP's own fitness
        # function scores individuals) -- so a single bad repetition makes
        # this "strict" fitness 0.0 and its metrics None even when most
        # repetitions actually produced good data. summarize() below reads
        # each repetition's own JSON independently and is not subject to
        # that all-or-nothing collapse, so print its partial-success view too.
        fitness = calculate_fitness(result, config.fitness)
        row = summarize(log_directory, log_id)
        attempted = row["attempted_repetitions"] or 0
        succeeded = row["successful_repetitions"] or 0
        print(
            f"  -> {succeeded}/{attempted} repetitions succeeded "
            f"({row['success_rate_percent'] or 0:.0f}%); "
            f"partial means: planning={format_metric(row['planning_time_mean'])} "
            f"path={format_metric(row['path_length_mean'])} "
            f"arrival={format_metric(row['arrival_time_mean'])} "
            f"node_expansions={format_metric(row['node_expansions_mean'], 0)}"
        )
        print(
            f"  -> strict (all-{attempted}-must-succeed) result: "
            f"success={result.success} fitness={fitness:.6f}"
        )
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

    comparison_rows = compare_candidates(log_directory, initial_label, best_label)
    comparison_csv = output_dir / f"repeat{repetitions}_significance.csv"
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
        print(
            f"{row['metric']:<18}{format_metric(row['initial_mean']):>14}"
            f"{format_metric(row['best_mean']):>14}"
            f"{format_metric(row['relative_improvement_percent'], 2):>15}"
            f"{format_metric(row['p_value']):>10}{str(row['significant_at_0.05']):>9}"
        )
    print(
        "\nrel.improve % is (initial - best) / initial * 100: positive means the "
        "best candidate is lower/better. p-value is a two-sided permutation test "
        "on the two repeated-evaluation samples (exact when feasible, else a "
        "seeded 20000-iteration Monte Carlo estimate); it answers 'could this gap "
        "plausibly be run-to-run Gazebo noise?', not 'is the effect large'."
    )
    return 0
