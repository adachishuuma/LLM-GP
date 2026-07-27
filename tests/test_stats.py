from __future__ import annotations

from llm_gp.stats import permutation_test_p_value, relative_improvement_percent


def test_relative_improvement_percent_matches_manual_example() -> None:
    # The motivating example: 0.004873517666965199s -> 0.0039s "looks tiny"
    # in absolute terms but is a real 20% reduction.
    value = relative_improvement_percent(0.004873517666965199, 0.0039)
    assert value is not None
    assert abs(value - 19.9757) < 1e-3


def test_relative_improvement_percent_none_on_missing_or_zero_baseline() -> None:
    assert relative_improvement_percent(None, 1.0) is None
    assert relative_improvement_percent(1.0, None) is None
    assert relative_improvement_percent(0.0, 1.0) is None


def test_permutation_test_none_for_too_few_samples() -> None:
    assert permutation_test_p_value([1.0], [1.0, 2.0]) is None
    assert permutation_test_p_value([], []) is None


def test_permutation_test_detects_a_real_gap() -> None:
    baseline = [1.0] * 10
    candidate = [5.0] * 10
    p_value = permutation_test_p_value(baseline, candidate)
    assert p_value is not None
    assert p_value < 0.01


def test_permutation_test_does_not_flag_indistinguishable_noise() -> None:
    baseline = [1.0, 1.01, 0.99, 1.02, 0.98, 1.0, 1.01, 0.99, 1.0, 1.0]
    candidate = [1.0, 1.02, 0.98, 1.01, 0.99, 1.0, 1.0, 1.01, 0.99, 1.0]
    p_value = permutation_test_p_value(baseline, candidate)
    assert p_value is not None
    assert p_value > 0.05


def test_permutation_test_is_reproducible_for_large_samples() -> None:
    # 15 vs 15 exceeds the exact-enumeration threshold (C(30,15) ~= 155M),
    # so this exercises the seeded Monte Carlo fallback.
    baseline = [float(i % 5) for i in range(15)]
    candidate = [float((i % 5) + 3) for i in range(15)]
    first = permutation_test_p_value(baseline, candidate, seed=7)
    second = permutation_test_p_value(baseline, candidate, seed=7)
    assert first == second
