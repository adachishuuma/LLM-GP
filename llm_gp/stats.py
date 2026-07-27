"""Statistics helpers for comparing two repeated-evaluation samples.

Used by scripts/repeat10_compare.py to report, for each metric, how much a
candidate improved over the baseline in relative terms and whether that gap
is distinguishable from run-to-run Gazebo noise -- an absolute difference of
a few milliseconds can be a real, significant improvement or pure noise
depending on how noisy repeated evaluations of the *same* individual are.
"""
from __future__ import annotations

import math
import random
import statistics
from itertools import combinations


def relative_improvement_percent(
    baseline: float | None, candidate: float | None
) -> float | None:
    """Percent improvement of candidate over baseline for a smaller-is-better
    metric (planning_time, path_length, arrival_time, node_expansions).

    Positive means the candidate is better (lower) than the baseline; e.g.
    baseline=4.87ms, candidate=3.90ms -> +20.0 (a 20% reduction), even though
    the absolute gap (0.97ms) looks negligible on its own.
    """
    if baseline in (None, 0.0) or candidate is None:
        return None
    return (baseline - candidate) / baseline * 100.0


def permutation_test_p_value(
    sample_a: list[float],
    sample_b: list[float],
    *,
    seed: int = 0,
    max_exact_combinations: int = 200_000,
    monte_carlo_iterations: int = 20_000,
) -> float | None:
    """Two-sided permutation test p-value for a difference in means between
    two independent samples (e.g. N repeated evaluations of the baseline vs.
    N repeated evaluations of the best evolved individual).

    Distribution-free (no normality assumption), which matters here because
    Gazebo run-to-run noise is not obviously Gaussian and sample sizes are
    small (typically N=10). Exact when the pooled sample is small enough to
    enumerate every relabeling exhaustively; otherwise falls back to a
    seeded Monte Carlo estimate so results are reproducible.

    Returns None if either sample has fewer than 2 values (nothing to test).
    """
    if len(sample_a) < 2 or len(sample_b) < 2:
        return None

    observed = abs(statistics.mean(sample_a) - statistics.mean(sample_b))
    combined = list(sample_a) + list(sample_b)
    n_a = len(sample_a)
    n_total = len(combined)

    total_combinations = math.comb(n_total, n_a)
    if total_combinations <= max_exact_combinations:
        extreme_count = 0
        for indices in combinations(range(n_total), n_a):
            index_set = set(indices)
            group_a = [combined[i] for i in indices]
            group_b = [combined[i] for i in range(n_total) if i not in index_set]
            diff = abs(statistics.mean(group_a) - statistics.mean(group_b))
            if diff >= observed - 1e-12:
                extreme_count += 1
        return extreme_count / total_combinations

    rng = random.Random(seed)
    extreme_count = 0
    working = list(combined)
    for _ in range(monte_carlo_iterations):
        rng.shuffle(working)
        group_a = working[:n_a]
        group_b = working[n_a:]
        diff = abs(statistics.mean(group_a) - statistics.mean(group_b))
        if diff >= observed - 1e-12:
            extreme_count += 1
    # +1/+1 (add-one smoothing) avoids reporting p=0.0 from a finite sample.
    return (extreme_count + 1) / (monte_carlo_iterations + 1)
