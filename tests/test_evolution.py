from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from llm_gp.config import AppConfig
from llm_gp.database import ExperimentDatabase
from llm_gp.evolution import EvolutionEngine


def make_engine(config: AppConfig) -> tuple[EvolutionEngine, ExperimentDatabase]:
    database = ExperimentDatabase(config.database_path, reset=True)
    return EvolutionEngine(config, database=database), database


def test_parent_pair_generates_three_operator_types(test_config: AppConfig) -> None:
    engine, database = make_engine(test_config)
    try:
        engine.initialize()
        children = engine.generate_children(engine.islands[0], generation=1)
        assert len(children) == 3
        assert {child.operator_type for child in children} == {
            "crossover_only",
            "mutation_only",
            "crossover_and_mutation",
        }
    finally:
        database.close()


def test_initial_40_and_generation_60_keep_four_populations_of_10(
    test_config: AppConfig,
) -> None:
    engine, database = make_engine(test_config)
    try:
        engine.initialize()
        assert database.count("individuals") == 40
        assert database.count("population_memberships") == 40
        assert all(len(island.population) == 10 for island in engine.islands)

        summary = engine.run_generation(1)
        assert summary.generated_individuals == 60
        assert summary.migrations == 0
        assert database.count("individuals") == 100
        assert database.count("evaluations") == 100
        assert database.count("llm_calls") == 40
        assert all(size == 10 for size in summary.population_sizes.values())
        assert all(len(island.population) == 10 for island in engine.islands)
        assert all(sum(item.is_elite for item in island.population) == 1 for island in engine.islands)
        operator_counts = dict(
            database.connection.execute(
                "SELECT operator_type, COUNT(*) FROM individuals GROUP BY operator_type"
            ).fetchall()
        )
        assert operator_counts == {
            "initial": 40,
            "crossover_only": 20,
            "mutation_only": 20,
            "crossover_and_mutation": 20,
        }
        retained = database.connection.execute(
            "SELECT COUNT(*) FROM population_memberships WHERE generation = 1"
        ).fetchone()[0]
        assert retained == 40
        assert database.connection.execute(
            "SELECT COUNT(*) FROM individuals WHERE selected_for_next_generation = 0"
        ).fetchone()[0] > 0
    finally:
        database.close()


def test_ring_migration_only_at_generation_10_and_keeps_population_size(
    test_config: AppConfig,
) -> None:
    engine, database = make_engine(test_config)
    try:
        engine.initialize()
        engine.run_generation(1)
        assert database.count("migrations") == 0
        sources_before = {
            item.individual_id for island in engine.islands for item in island.population
        }
        summary = engine.run_generation(10)
        assert summary.migrations == 4
        assert database.count("migrations") == 4
        assert all(len(island.population) == 10 for island in engine.islands)
        rows = database.connection.execute(
            "SELECT source_island, target_island FROM migrations ORDER BY source_island"
        ).fetchall()
        assert rows == [
            ("island_1", "island_2"),
            ("island_2", "island_3"),
            ("island_3", "island_4"),
            ("island_4", "island_1"),
        ]
        persisted = {
            row[0]
            for row in database.connection.execute(
                "SELECT individual_id FROM individuals WHERE individual_id IN (%s)"
                % ",".join("?" for _ in sources_before),
                tuple(sources_before),
            )
        }
        assert persisted == sources_before
    finally:
        database.close()


def _evaluation_signature(config: AppConfig) -> list[tuple[float, float, float, float]]:
    engine, database = make_engine(config)
    try:
        engine.initialize()
        engine.run_generation(1)
        return database.connection.execute(
            """SELECT planning_time, path_length, arrival_time, fitness
               FROM evaluations ORDER BY individual_id"""
        ).fetchall()
    finally:
        database.close()


def test_same_seed_produces_same_results(test_config: AppConfig, tmp_path: Path) -> None:
    second = replace(
        test_config,
        database_path=tmp_path / "second.db",
        source_directory=tmp_path / "second_sources",
        generation_csv=tmp_path / "second.csv",
    )
    assert _evaluation_signature(test_config) == _evaluation_signature(second)
