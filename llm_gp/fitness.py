from __future__ import annotations

import math
from dataclasses import dataclass

from .config import FitnessSettings
from .models import EvaluationResult


@dataclass(frozen=True)
class GenerationMetricStats:
    """Min/max of each cost metric across one generation's successfully
    evaluated individuals. calculate_fitness() normalizes an individual's
    metrics against these instead of against FitnessSettings' fixed
    reference constants, so a given raw metric value can score differently
    from one generation to the next."""

    cost_min: float
    cost_max: float
    path_min: float
    path_max: float
    arrival_min: float
    arrival_max: float


def _metric_triplet(result: EvaluationResult) -> tuple[float, float, float] | None:
    # See calculate_fitness below for why node_expansions is preferred over
    # planning_time as the cost metric.
    using_expansions = result.node_expansions is not None
    cost_metric = result.node_expansions if using_expansions else result.planning_time
    metrics = (cost_metric, result.path_length, result.arrival_time)
    if (
        not result.success
        or any(value is None for value in metrics)
        or not all(math.isfinite(float(value)) for value in metrics)
    ):
        return None
    cost_value, path_length, arrival_time = (float(value) for value in metrics)
    return cost_value, path_length, arrival_time


def compute_generation_stats(
    results: list[EvaluationResult],
) -> GenerationMetricStats | None:
    """Min/max per metric across `results`' successfully evaluated members.
    None when fewer than two qualify: with 0 or 1 successful individuals
    there is no range to normalize against, so calculate_fitness() treats
    every success in that generation as tied for best.
    """
    triplets = [
        triplet
        for triplet in (_metric_triplet(result) for result in results)
        if triplet is not None
    ]
    if len(triplets) < 2:
        return None
    costs, paths, arrivals = zip(*triplets)
    return GenerationMetricStats(
        cost_min=min(costs),
        cost_max=max(costs),
        path_min=min(paths),
        path_max=max(paths),
        arrival_min=min(arrivals),
        arrival_max=max(arrivals),
    )


def _minmax_score(value: float, lo: float, hi: float) -> float:
    if hi <= lo:
        # Every successful individual tied on this metric -- no basis to
        # rank them against each other, so nobody is penalized for it.
        return 1.0
    return max(0.0, min(1.0, (hi - value) / (hi - lo)))


def calculate_fitness(
    result: EvaluationResult,
    settings: FitnessSettings,
    stats: GenerationMetricStats | None,
) -> float:
    # The search-cost axis is scored from node_expansions (counted inside the
    # search loop, immune to ROS/Gazebo scheduling noise) whenever it is
    # available. planning_time is wall-clock time from goal-sent to
    # first-plan-received as measured externally by ros_gazebo_trial.py; on
    # small maps it is dominated by fixed ROS/actionlib overhead and barely
    # differs between candidates, which is why this fallback exists rather
    # than being the primary signal. See docs/astar_dwa_run_guide.md.
    triplet = _metric_triplet(result)
    if triplet is None:
        return settings.failure_fitness
    cost_value, path_length, arrival_time = triplet
    if stats is None:
        # Fewer than two individuals succeeded in this generation, so there
        # is nothing to normalize against -- give the lone success full
        # marks rather than an arbitrary fixed-reference score.
        return settings.planning_weight + settings.path_weight + settings.arrival_weight
    cost_score = _minmax_score(cost_value, stats.cost_min, stats.cost_max)
    path_score = _minmax_score(path_length, stats.path_min, stats.path_max)
    arrival_score = _minmax_score(arrival_time, stats.arrival_min, stats.arrival_max)
    return (
        cost_score * settings.planning_weight
        + path_score * settings.path_weight
        + arrival_score * settings.arrival_weight
    )
