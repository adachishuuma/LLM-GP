#!/usr/bin/env python3
"""Check whether a run's generation-over-generation fitness gain is real or
just Gazebo evaluation noise, by re-evaluating the initial baseline and the
overall-best individual N times each and comparing the averages.

Uses the same "generation 0 best" / "overall best" definitions as
llm_gp/report.py (i.e. generation_algorithm_report.md's initial reference
individual and best-so-far individual), so results line up with the report
you already generated. Only the run directory needs to be given; the config
YAML and the sqlite database are auto-detected from that directory.

Example:
  python scripts/verify_generation_gain.py run_20260811_065434
  python scripts/verify_generation_gain.py run_20260811_065434 --repetitions 10
"""
from __future__ import annotations

import argparse
import dataclasses
import os
import re
import sys
from pathlib import Path

_WINDOWS_ABS_PATH = re.compile(r"^[A-Za-z]:[\\/]")


def _normalize_recorded_path(raw: Path) -> Path:
    """individuals.source_path in the sqlite db was written by whatever
    interpreter ran the experiment. If that was native Windows Python
    ("C:\\Users\\...") but this script is running under WSL/Linux (posix),
    backslash-separated drive paths don't parse as valid paths there -- translate
    them to the /mnt/<drive>/... mount point instead. No-op in every other case."""
    text = str(raw)
    if os.name != "nt" and _WINDOWS_ABS_PATH.match(text):
        drive, rest = text.split(":", 1)
        posix_rest = rest.replace("\\", "/").lstrip("/")
        return Path(f"/mnt/{drive.lower()}/{posix_rest}")
    return raw

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from llm_gp.config import load_config
from llm_gp.evaluator import cleanup_stray_ros_gazebo_processes
from llm_gp.report import load_rows_with_corrected_generation_zero_fitness, select_baseline_and_best
from llm_gp.run_lock import ros_gazebo_run_lock
from llm_gp.verification import run_comparison


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "run",
        help="Run directory name (searched under experiment_results/) or a path to it, "
             "e.g. run_20260811_065434",
    )
    parser.add_argument("--repetitions", type=int, default=10)
    parser.add_argument(
        "--config", type=Path, default=None,
        help="Config YAML to reuse for ROS/Gazebo settings and fitness weights "
             "(default: config/<name>.yaml, matched by the name of the single "
             "*.yaml snapshot copied into the run directory)",
    )
    parser.add_argument(
        "--output", type=Path, default=None,
        help="Output CSV path (default: <run-dir>/analysis/repeat<N>_statistics.csv)",
    )
    return parser.parse_args()


def resolve_run_dir(run_arg: str) -> Path:
    candidate = Path(run_arg)
    if candidate.is_dir():
        return candidate.resolve()
    experiment_results = PROJECT_ROOT / "experiment_results"
    matches = sorted({p for p in experiment_results.rglob(run_arg) if p.is_dir()})
    if not matches:
        raise SystemExit(
            f"Run directory not found: {run_arg!r} "
            f"(tried it as a path, and searched under {experiment_results})"
        )
    if len(matches) > 1:
        listing = "\n".join(f"  - {m}" for m in matches)
        raise SystemExit(
            f"Multiple run directories named {run_arg!r} found, pass a full path instead:\n{listing}"
        )
    return matches[0]


def resolve_single_file(run_dir: Path, pattern: str, override: Path | None, label: str) -> Path:
    if override is not None:
        return override
    candidates = sorted(run_dir.glob(pattern))
    if len(candidates) != 1:
        raise SystemExit(
            f"Expected exactly one {label} ({pattern}) in {run_dir}, found {len(candidates)}"
        )
    return candidates[0]


def resolve_config(run_dir: Path, override: Path | None) -> Path:
    """llm_gp.config.load_config() derives the project root from the config
    file's own location (two directories up), assuming it lives under
    <project_root>/config/. The copy snapshotted into the run directory sits
    one level too deep for that to work (it would resolve relative paths like
    launch_file against experiment_results/<experiment>/ instead of the repo
    root), so prefer the canonical config/<name>.yaml with the same filename."""
    if override is not None:
        return override
    snapshot = resolve_single_file(run_dir, "*.yaml", None, "config YAML")
    canonical = PROJECT_ROOT / "config" / snapshot.name
    if canonical.is_file():
        return canonical
    raise SystemExit(
        f"Found a config snapshot at {snapshot}, but no matching {canonical} "
        "-- load_config() needs the file at its original config/ location to "
        "resolve relative paths (launch_file, database, ...) correctly. "
        "Pass --config explicitly."
    )


def main() -> int:
    args = parse_args()
    run_dir = resolve_run_dir(args.run)
    database_path = resolve_single_file(run_dir, "*.db", None, "sqlite database")
    config_path = resolve_config(run_dir, args.config)
    config = load_config(config_path)

    rows = load_rows_with_corrected_generation_zero_fitness(database_path, config.fitness)
    baseline, best = select_baseline_and_best(rows)
    if best is None:
        raise SystemExit(f"No successful evaluations in {database_path} to compare against")
    baseline = dataclasses.replace(baseline, source_path=_normalize_recorded_path(baseline.source_path))
    best = dataclasses.replace(best, source_path=_normalize_recorded_path(best.source_path))

    print(f"Run: {run_dir}")
    print(
        f"Initial baseline : {baseline.individual_id} (gen {baseline.generation}, "
        f"fitness={baseline.fitness:.6f}, source={baseline.source_path.name})"
    )
    print(
        f"Overall best     : {best.individual_id} (gen {best.generation}, "
        f"fitness={best.fitness:.6f}, source={best.source_path.name})"
    )
    if baseline.individual_id == best.individual_id:
        print(
            "\nNote: the initial baseline is already the overall-best individual in this run; "
            "the repeated evaluation below only estimates single-individual noise, there is no "
            "generational gain to verify."
        )
    print()

    with ros_gazebo_run_lock(PROJECT_ROOT):
        try:
            return run_comparison(
                project_root=PROJECT_ROOT,
                config=config,
                repetitions=args.repetitions,
                initial_label=f"initial_{baseline.individual_id}",
                initial_source=baseline.source_path,
                best_label=f"best_{best.individual_id}",
                best_source=best.source_path,
                output_dir=run_dir / "analysis",
                output_csv=args.output,
            )
        finally:
            if config.evaluation.ros_gazebo is not None:
                cleanup_stray_ros_gazebo_processes(config.evaluation.ros_gazebo, PROJECT_ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
