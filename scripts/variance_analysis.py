#!/usr/bin/env python3
"""Estimate per-individual evaluation noise (Gazebo run-to-run variance) and,
from that, how many repetitions are needed to measure fitness reliably.

Scans every '<id>_repetition_<n>.json' file under one or more directories,
groups them by individual id, recomputes per-repetition fitness with the
project's actual fitness formula (not just the 3 raw metrics), and reports:

  1. Per-individual mean/stddev of fitness (and the 3 raw metrics).
  2. A pooled within-individual stddev (the experiment's typical noise level),
     computed as the RMS of each individual's own stddev - this stays valid
     even though different individuals may have (slightly) different code.
  3. Required repetition counts to reach a chosen precision, using:
       - single-mean 95% CI half-width:      N = (1.96 * sigma / E) ** 2
       - two-sample comparison (95% conf,
         80% power) to detect a fitness gap
         of size delta:                      N = 15.7 * sigma^2 / delta^2  (per individual)

Example:
  python scripts/variance_analysis.py \
      --config config/ros_gazebo_10gen_repeated.yaml \
      experiment_results/ten_generation_repeated/run_20260722_053957/ros_logs \
      experiment_results/ten_generation_repeated/run_20260722_053957/analysis/repeat10_logs
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from llm_gp.config import load_config
from llm_gp.fitness import calculate_fitness
from llm_gp.models import EvaluationResult

_FILE_PATTERN = re.compile(r"^(?P<id>.+)_repetition_(?P<n>\d+)\.json$")

# Individuals whose source is byte-identical (dedup) get merged under one key
# so their repeated evaluations are pooled together as one noise sample.
DEFAULT_ALIASES = {
    "initial_ind_000001": "ind_000001",
    "best_ind_000050": "ind_000012",  # ind_000050's source was deduped to ind_000012.cpp
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("log_dirs", nargs="+", type=Path, help="Directories to scan for *_repetition_*.json")
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "config" / "ros_gazebo_10gen_repeated.yaml")
    parser.add_argument("--alias", action="append", default=[], metavar="FROM=TO",
                         help="Merge FROM's repetitions into TO (repeatable). "
                              "Defaults already merge the known dedup pair.")
    parser.add_argument("--min-reps", type=int, default=2, help="Ignore individuals with fewer successful reps")
    parser.add_argument("--target-error", type=float, default=0.02,
                         help="Desired 95%% CI half-width on mean fitness (default 0.02)")
    parser.add_argument("--target-delta", type=float, default=0.02,
                         help="Smallest fitness gap between two individuals you want to reliably detect (default 0.02)")
    return parser.parse_args()


def collect(log_dirs: list[Path], aliases: dict[str, str]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for directory in log_dirs:
        if not directory.is_dir():
            print(f"warning: not a directory, skipping: {directory}", file=sys.stderr)
            continue
        for file_path in sorted(directory.glob("*_repetition_*.json")):
            match = _FILE_PATTERN.match(file_path.name)
            if not match:
                continue
            individual_id = aliases.get(match.group("id"), match.group("id"))
            try:
                payload = json.loads(file_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            payload["_source_file"] = str(file_path)
            grouped[individual_id].append(payload)
    return grouped


def to_result(payload: dict) -> EvaluationResult:
    return EvaluationResult(
        success=bool(payload.get("success")),
        planning_time=payload.get("planning_time"),
        path_length=payload.get("path_length"),
        arrival_time=payload.get("arrival_time"),
    )


def stats_for(values: list[float]) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    mean = statistics.mean(values)
    stdev = statistics.stdev(values) if len(values) >= 2 else None
    return mean, stdev


def main() -> int:
    args = parse_args()
    config = load_config(args.config)

    aliases = dict(DEFAULT_ALIASES)
    for item in args.alias:
        left, _, right = item.partition("=")
        if not right:
            print(f"invalid --alias {item!r}, expected FROM=TO", file=sys.stderr)
            return 2
        aliases[left] = right

    grouped = collect(args.log_dirs, aliases)
    if not grouped:
        print("No *_repetition_*.json files found under the given directories.", file=sys.stderr)
        return 1

    per_individual: dict[str, dict[str, object]] = {}
    for individual_id, payloads in sorted(grouped.items()):
        successful = [p for p in payloads if p.get("success")]
        if len(successful) < args.min_reps:
            continue
        fitness_values = [calculate_fitness(to_result(p), config.fitness) for p in successful]
        planning = [float(p["planning_time"]) for p in successful if p.get("planning_time") is not None]
        path = [float(p["path_length"]) for p in successful if p.get("path_length") is not None]
        arrival = [float(p["arrival_time"]) for p in successful if p.get("arrival_time") is not None]
        per_individual[individual_id] = {
            "n": len(successful),
            "attempted": len(payloads),
            "fitness_mean": stats_for(fitness_values)[0],
            "fitness_stdev": stats_for(fitness_values)[1],
            "planning_stdev": stats_for(planning)[1],
            "path_stdev": stats_for(path)[1],
            "arrival_stdev": stats_for(arrival)[1],
        }

    if not per_individual:
        print(f"No individual reached --min-reps={args.min_reps} successful repetitions.", file=sys.stderr)
        return 1

    print(f"{'individual':<24}{'n':>4}{'fitness_mean':>14}{'fitness_sd':>12}"
          f"{'planning_sd':>13}{'path_sd':>10}{'arrival_sd':>12}")
    for individual_id, row in per_individual.items():
        def fmt(value: object, digits: int = 4) -> str:
            return f"{value:.{digits}f}" if isinstance(value, float) else "  n/a"
        print(f"{individual_id:<24}{row['n']:>4}{fmt(row['fitness_mean']):>14}{fmt(row['fitness_stdev']):>12}"
              f"{fmt(row['planning_stdev'], 5):>13}{fmt(row['path_stdev']):>10}{fmt(row['arrival_stdev'], 2):>12}")

    fitness_sds = [row["fitness_stdev"] for row in per_individual.values() if row["fitness_stdev"] is not None]
    if not fitness_sds:
        print("\nNot enough repeated individuals (>=2 successful reps each) to pool a noise estimate.")
        return 0

    pooled_sigma = statistics.mean(fitness_sds)
    rms_sigma = (sum(s ** 2 for s in fitness_sds) / len(fitness_sds)) ** 0.5
    print(f"\nPooled within-individual fitness stddev across {len(fitness_sds)} individuals:")
    print(f"  simple average of per-individual stdev : {pooled_sigma:.4f}")
    print(f"  RMS of per-individual stdev             : {rms_sigma:.4f}  <- use this as sigma below")

    sigma = rms_sigma
    z95 = 1.959964
    z_power80 = 0.841621

    print(f"\nRequired repetitions N to estimate ONE individual's true mean fitness")
    print(f"within +-{args.target_error} at 95% confidence (N = (1.96*sigma/E)^2):")
    n_single = (z95 * sigma / args.target_error) ** 2
    print(f"  sigma={sigma:.4f}, E={args.target_error} -> N ~= {n_single:.1f} -> use N = {int(-(-n_single // 1))}")

    print(f"\nRequired repetitions N PER individual to reliably tell two individuals apart")
    print(f"when the true fitness gap is {args.target_delta} (95% confidence, 80% power):")
    n_pair = 2 * (z95 + z_power80) ** 2 * sigma ** 2 / args.target_delta ** 2
    print(f"  sigma={sigma:.4f}, delta={args.target_delta} -> N ~= {n_pair:.1f} -> use N = {int(-(-n_pair // 1))}")

    print("\nFor reference, a small table of N vs. target CI half-width / detectable gap:")
    print(f"{'target':>10}{'N (single-mean CI)':>22}{'N (two-sample, per group)':>28}")
    for target in (0.10, 0.05, 0.03, 0.02, 0.01):
        n1 = (z95 * sigma / target) ** 2
        n2 = 2 * (z95 + z_power80) ** 2 * sigma ** 2 / target ** 2
        print(f"{target:>10.2f}{n1:>22.1f}{n2:>28.1f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
