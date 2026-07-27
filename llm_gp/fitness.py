from __future__ import annotations

import math

from .config import FitnessSettings
from .models import EvaluationResult


def calculate_fitness(result: EvaluationResult, settings: FitnessSettings) -> float:
    # The search-cost axis is scored from node_expansions (counted inside the
    # search loop, immune to ROS/Gazebo scheduling noise) whenever it is
    # available. planning_time is wall-clock time from goal-sent to
    # first-plan-received as measured externally by ros_gazebo_trial.py; on
    # small maps it is dominated by fixed ROS/actionlib overhead and barely
    # differs between candidates, which is why this fallback exists rather
    # than being the primary signal. See docs/astar_dwa_run_guide.md.
    using_expansions = result.node_expansions is not None
    cost_metric = result.node_expansions if using_expansions else result.planning_time
    cost_reference = settings.expansion_reference if using_expansions else settings.planning_reference

    metrics = (cost_metric, result.path_length, result.arrival_time)
    if (
        not result.success
        or any(value is None for value in metrics)
        or not all(math.isfinite(float(value)) for value in metrics if value is not None)
    ):
        return settings.failure_fitness
    cost_value, path_length, arrival_time = (float(value) for value in metrics)
    cost_score = max(0.0, 1.0 - cost_value / cost_reference)
    path_score = max(0.0, 1.0 - path_length / settings.path_reference)
    arrival_score = max(0.0, 1.0 - arrival_time / settings.arrival_reference)
    return (
        cost_score * settings.planning_weight
        + path_score * settings.path_weight
        + arrival_score * settings.arrival_weight
    )
