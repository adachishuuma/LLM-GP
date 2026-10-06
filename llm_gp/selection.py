from __future__ import annotations

import math
import random
from collections.abc import Iterable

from .models import Individual


def selection_weights(
    population: Iterable[Individual], epsilon: float = 1.0e-9
) -> list[float]:
    individuals = list(population)
    if not individuals:
        return []
    fitnesses = [
        item.fitness if item.fitness is not None and math.isfinite(item.fitness) else 0.0
        for item in individuals
    ]
    minimum = min(fitnesses)
    offset = -minimum + epsilon if minimum <= 0.0 else 0.0
    weights = [max(0.0, fitness + offset) for fitness in fitnesses]
    if sum(weights) <= epsilon * len(weights):
        return [1.0] * len(weights)
    return weights


def roulette_select(
    population: Iterable[Individual],
    rng: random.Random,
    *,
    exclude_ids: set[str] | None = None,
    epsilon: float = 1.0e-9,
) -> Individual:
    excluded = exclude_ids or set()
    candidates = [item for item in population if item.individual_id not in excluded]
    if not candidates:
        raise ValueError("Cannot roulette-select from an empty population")
    return rng.choices(candidates, weights=selection_weights(candidates, epsilon), k=1)[0]


def select_parent_pair(
    population: list[Individual], rng: random.Random, epsilon: float = 1.0e-9# 個体がたくさん入ったリスト
) -> tuple[Individual, Individual]:
    if not population:
        raise ValueError("Cannot select parents from an empty population")
    parent1 = roulette_select(population, rng, epsilon=epsilon)# 適応度が高い個体を選ぶ
    if len(population) == 1:
        return parent1, parent1
    parent2 = roulette_select(
        population, rng, exclude_ids={parent1.individual_id}, epsilon=epsilon
    )# 選ばれた個体以外で再びルーレット選択を行います。
    return parent1, parent2


def rank_select_parent_pair(
    population: list[Individual], pair_index: int
) -> tuple[Individual, Individual]:
    """Deterministic parent selection: no randomness. Sort the population by
    fitness (ties broken by elite_sort_key, same tiebreak truncation_select_
    survivors uses, so results are reproducible across runs of the same
    data) and pair up adjacent ranks -- pair_index 0 gets rank 1 & 2,
    pair_index 1 gets rank 3 & 4, and so on. Wraps around (modulo population
    size) if parent_pairs_per_island * 2 exceeds the population size, so it
    never raises for a valid config."""
    if not population:
        raise ValueError("Cannot select parents from an empty population")
    ranked = sorted(population, key=elite_sort_key)
    if len(ranked) == 1:
        return ranked[0], ranked[0]
    first_idx = (2 * pair_index) % len(ranked)
    second_idx = (2 * pair_index + 1) % len(ranked)
    if first_idx == second_idx:
        second_idx = (second_idx + 1) % len(ranked)
    return ranked[first_idx], ranked[second_idx]


def elite_sort_key(individual: Individual) -> tuple[float, int, float, float, float, str]:
    def metric(value: float | None) -> float:
        return value if value is not None and math.isfinite(value) else math.inf

    return (
        -(individual.fitness if individual.fitness is not None else -math.inf),
        -int(individual.evaluation_success),
        metric(individual.arrival_time),
        metric(individual.path_length),
        metric(individual.planning_time),
        individual.individual_id,
    )


def select_survivors(
    candidates: list[Individual],
    population_size: int,
    elite_count: int,
    rng: random.Random,
    epsilon: float = 1.0e-9,
) -> list[Individual]:
    if population_size < 1 or elite_count != 1:
        raise ValueError("The current specification requires one elite and a positive population")
    unique = list({item.individual_id: item for item in candidates}.values())
    if not unique:
        return []
    for item in unique:
        item.is_elite = False
        item.selected_for_next_generation = False
    elite = min(unique, key=elite_sort_key)
    elite.is_elite = True
    elite.selected_for_next_generation = True
    selected = [elite]
    remaining = [item for item in unique if item.individual_id != elite.individual_id]
    while remaining and len(selected) < population_size:
        chosen = roulette_select(remaining, rng, epsilon=epsilon)
        chosen.selected_for_next_generation = True
        selected.append(chosen)
        remaining = [item for item in remaining if item.individual_id != chosen.individual_id]
    return selected


def truncation_select_survivors(
    candidates: list[Individual],
    population_size: int,
    elite_count: int,
) -> list[Individual]:
    """Deterministic survivor selection: no randomness. Sort parents+children
    by fitness (elite_sort_key, best first) and keep exactly the top
    population_size -- the same rule select_survivors already uses for the
    single elite, just extended to the whole next generation instead of
    handing the rest to roulette_select."""
    if population_size < 1 or elite_count != 1:
        raise ValueError("The current specification requires one elite and a positive population")
    unique = list({item.individual_id: item for item in candidates}.values())
    if not unique:
        return []
    for item in unique:
        item.is_elite = False
        item.selected_for_next_generation = False
    ranked = sorted(unique, key=elite_sort_key)
    selected = ranked[:population_size]
    selected[0].is_elite = True
    for item in selected:
        item.selected_for_next_generation = True
    return selected
