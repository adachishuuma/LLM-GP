from __future__ import annotations

import random

from llm_gp.models import Individual
from llm_gp.selection import (
    rank_select_parent_pair,
    roulette_select,
    select_parent_pair,
    select_survivors,
    truncation_select_survivors,
)


def make_individual(identifier: str, fitness: float) -> Individual:
    return Individual(
        individual_id=identifier,
        generation=1,
        current_island="island_1",
        origin_island="island_1",
        source_path="unused.py",
        parent_ids=[],
        operator_type="test",
        fitness=fitness,
        evaluation_success=True,
        planning_time=0.1,
        path_length=10.0,
        arrival_time=20.0,
    )


def test_roulette_prefers_high_fitness_but_keeps_low_probability() -> None:
    rng = random.Random(7)
    low = make_individual("low", 1.0)
    high = make_individual("high", 9.0)
    selected = [roulette_select([low, high], rng).individual_id for _ in range(2000)]
    assert selected.count("high") > selected.count("low") * 5
    assert selected.count("low") > 0


def test_zero_weights_fall_back_to_uniform_selection() -> None:
    rng = random.Random(8)
    population = [make_individual(f"i{index}", 0.0) for index in range(4)]
    selected = [roulette_select(population, rng).individual_id for _ in range(800)]
    assert set(selected) == {item.individual_id for item in population}


def test_parent_pair_is_distinct_when_possible() -> None:
    population = [make_individual(f"i{index}", float(index)) for index in range(10)]
    rng = random.Random(9)
    for _ in range(100):
        parent1, parent2 = select_parent_pair(population, rng)
        assert parent1.individual_id != parent2.individual_id


def test_survivors_keep_elite_and_have_no_duplicates() -> None:
    candidates = [make_individual(f"i{index:02d}", float(index)) for index in range(25)]
    survivors = select_survivors(candidates, 10, 1, random.Random(10))
    assert len(survivors) == 10
    assert len({item.individual_id for item in survivors}) == 10
    assert candidates[-1] in survivors
    assert candidates[-1].is_elite
    assert sum(item.is_elite for item in survivors) == 1
    assert all(item.selected_for_next_generation for item in survivors)


def test_rank_select_parent_pair_pairs_adjacent_ranks_deterministically() -> None:
    # fitness 0..9, so individual_id "i9" is rank 1 (best), "i8" rank 2, etc.
    population = [make_individual(f"i{index}", float(index)) for index in range(10)]
    pair0 = rank_select_parent_pair(population, 0)
    pair1 = rank_select_parent_pair(population, 1)
    assert {p.individual_id for p in pair0} == {"i9", "i8"}
    assert {p.individual_id for p in pair1} == {"i7", "i6"}
    # No rng involved: repeated calls on the same population must agree.
    assert rank_select_parent_pair(population, 0) == pair0


def test_rank_select_parent_pair_wraps_around_without_raising() -> None:
    population = [make_individual(f"i{index}", float(index)) for index in range(4)]
    # pair_index 2 would need rank 5 & 6, which don't exist in a 4-member
    # population -- must wrap around (modulo population size) instead of
    # raising an IndexError.
    parent1, parent2 = rank_select_parent_pair(population, 2)
    assert parent1.individual_id != parent2.individual_id


def test_truncation_select_survivors_keeps_top_n_deterministically() -> None:
    candidates = [make_individual(f"i{index:02d}", float(index)) for index in range(25)]
    survivors = truncation_select_survivors(candidates, 10, 1)
    assert len(survivors) == 10
    assert len({item.individual_id for item in survivors}) == 10
    # Top 10 by fitness are indices 15..24.
    assert {item.individual_id for item in survivors} == {
        f"i{index:02d}" for index in range(15, 25)
    }
    assert candidates[-1].is_elite
    assert sum(item.is_elite for item in survivors) == 1
    assert all(item.selected_for_next_generation for item in survivors)
    # No rng involved: repeated calls on the same candidates must agree.
    assert {item.individual_id for item in truncation_select_survivors(candidates, 10, 1)} == {
        item.individual_id for item in survivors
    }
