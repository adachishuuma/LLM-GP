from __future__ import annotations

import argparse
from pathlib import Path

from .config import load_config
from .evaluator import RosGazeboEvaluator
from .models import Individual
from .operators import initial_cpp_source
from .run_lock import ros_gazebo_run_lock
from .validation import validate_individual


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate one baseline A* candidate in Gazebo")
    parser.add_argument("--config", default="config/ros_gazebo.yaml")
    args = parser.parse_args()
    config = load_config(args.config)
    settings = config.evaluation.ros_gazebo
    if config.evaluation.evaluator_type != "ros_gazebo" or settings is None:
        raise SystemExit("A ros_gazebo evaluator configuration is required")
    project_root = Path(__file__).parents[1]
    source_path = initial_cpp_source(
        config.source_directory,
        "ros_smoke_baseline",
        project_root / settings.candidate_target,
    )
    individual = Individual(
        individual_id="ros_smoke_baseline",
        generation=0,
        current_island="astar",
        origin_island="astar",
        source_path=str(source_path),
        parent_ids=[],
        operator_type="initial",
    )
    valid, error = validate_individual(individual)
    if not valid:
        raise SystemExit(error)
    evaluator = RosGazeboEvaluator(
        settings=settings,
        fixed_goal=config.evaluation.fixed_goal,
        timeout_seconds=config.evaluation.timeout_seconds,
        repetitions=1,
        build_timeout_seconds=config.validation.build_timeout_seconds,
        log_directory=config.database_path.parent / "ros_logs",
        project_root=project_root,
    )
    with ros_gazebo_run_lock(project_root):
        result = evaluator.evaluate(individual)
    print(f"success={result.success}")
    print(f"planning_time={result.planning_time}")
    print(f"path_length={result.path_length}")
    print(f"arrival_time={result.arrival_time}")
    print(f"error_message={result.error_message}")
    print(f"raw_log_path={result.raw_log_path}")
    if not result.success:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
