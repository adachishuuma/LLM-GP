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
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from llm_gp.config import load_config
from llm_gp.evaluator import cleanup_stray_ros_gazebo_processes
from llm_gp.run_lock import ros_gazebo_run_lock
from llm_gp.verification import run_comparison


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


def main() -> int:
    args = parse_args()
    config = load_config(args.config)

    source_directory = args.run_dir / "individual_sources"
    initial_source = source_directory / f"{args.initial_id}.cpp"
    best_source = source_directory / f"{args.best_source_id}.cpp"

    with ros_gazebo_run_lock(PROJECT_ROOT):
        try:
            return run_comparison(
                project_root=PROJECT_ROOT,
                config=config,
                repetitions=args.repetitions,
                initial_label=f"initial_{args.initial_id}",
                initial_source=initial_source,
                best_label=f"best_{args.best_id}",
                best_source=best_source,
                output_dir=args.run_dir / "analysis",
                output_csv=args.output,
            )
        finally:
            if config.evaluation.ros_gazebo is not None:
                cleanup_stray_ros_gazebo_processes(config.evaluation.ros_gazebo, PROJECT_ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
