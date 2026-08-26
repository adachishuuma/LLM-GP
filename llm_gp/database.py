from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import EvaluationResult, Individual, LLMCallRecord


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExperimentDatabase:
    def __init__(self, path: Path, reset: bool = True) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA foreign_keys = ON")
        self._create_schema(reset)

    def _create_schema(self, reset: bool) -> None:
        if reset:
            self.connection.executescript(
                """
                DROP TABLE IF EXISTS llm_calls;
                DROP TABLE IF EXISTS change_history;
                DROP TABLE IF EXISTS migrations;
                DROP TABLE IF EXISTS population_memberships;
                DROP TABLE IF EXISTS archive_entries;
                DROP TABLE IF EXISTS evaluations;
                DROP TABLE IF EXISTS individuals;
                """
            )
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS individuals (
                individual_id TEXT PRIMARY KEY,
                generation INTEGER NOT NULL,
                current_island TEXT NOT NULL,
                origin_island TEXT NOT NULL,
                operator_type TEXT NOT NULL,
                source_path TEXT NOT NULL,
                parent_ids TEXT NOT NULL,
                random_seed INTEGER,
                valid INTEGER NOT NULL,
                evaluation_success INTEGER NOT NULL,
                is_elite INTEGER NOT NULL,
                selected_for_next_generation INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS evaluations (
                individual_id TEXT PRIMARY KEY REFERENCES individuals(individual_id),
                planning_time REAL,
                path_length REAL,
                arrival_time REAL,
                node_expansions REAL,
                fitness REAL,
                success INTEGER NOT NULL,
                error_message TEXT,
                raw_log_path TEXT,
                evaluated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS population_memberships (
                island_name TEXT NOT NULL,
                generation INTEGER NOT NULL,
                individual_id TEXT NOT NULL REFERENCES individuals(individual_id),
                selection_method TEXT NOT NULL,
                selected_at TEXT NOT NULL,
                PRIMARY KEY (island_name, generation, individual_id)
            );
            CREATE TABLE IF NOT EXISTS migrations (
                source_individual_id TEXT NOT NULL REFERENCES individuals(individual_id),
                copied_individual_id TEXT NOT NULL REFERENCES individuals(individual_id),
                source_island TEXT NOT NULL,
                target_island TEXT NOT NULL,
                generation INTEGER NOT NULL,
                migrated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS change_history (
                individual_id TEXT NOT NULL REFERENCES individuals(individual_id),
                sequence_number INTEGER NOT NULL,
                operation_type TEXT NOT NULL,
                description TEXT NOT NULL,
                diff_text TEXT,
                PRIMARY KEY (individual_id, sequence_number)
            );
            CREATE TABLE IF NOT EXISTS llm_calls (
                call_id INTEGER PRIMARY KEY AUTOINCREMENT,
                individual_id TEXT NOT NULL REFERENCES individuals(individual_id),
                model_name TEXT NOT NULL,
                prompt_text TEXT NOT NULL,
                response_text TEXT NOT NULL,
                success INTEGER NOT NULL,
                error_message TEXT,
                created_at TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def save_individual(self, individual: Individual) -> None:
        self.connection.execute(
            """
            INSERT INTO individuals (
                individual_id, generation, current_island, origin_island,
                operator_type, source_path, parent_ids, random_seed, valid,
                evaluation_success, is_elite, selected_for_next_generation, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                individual.individual_id,
                individual.generation,
                individual.current_island,
                individual.origin_island,
                individual.operator_type,
                individual.source_path,
                json.dumps(individual.parent_ids),
                individual.random_seed,
                int(individual.valid),
                int(individual.evaluation_success),
                int(individual.is_elite),
                int(individual.selected_for_next_generation),
                _now(),
            ),
        )
        for sequence, description in enumerate(individual.change_history, start=1):
            self.connection.execute(
                "INSERT INTO change_history VALUES (?, ?, ?, ?, ?)",
                (individual.individual_id, sequence, individual.operator_type, description, None),
            )
        for call in individual.llm_calls:
            self.save_llm_call(individual.individual_id, call)

    def update_individual_status(self, individual: Individual) -> None:
        self.connection.execute(
            """
            UPDATE individuals
            SET valid = ?, evaluation_success = ?, is_elite = ?,
                selected_for_next_generation = ?
            WHERE individual_id = ?
            """,
            (
                int(individual.valid),
                int(individual.evaluation_success),
                int(individual.is_elite),
                int(individual.selected_for_next_generation),
                individual.individual_id,
            ),
        )

    def save_evaluation(self, individual: Individual, result: EvaluationResult) -> None:
        self.connection.execute(
            "INSERT INTO evaluations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                individual.individual_id,
                result.planning_time,
                result.path_length,
                result.arrival_time,
                result.node_expansions,
                individual.fitness,
                int(result.success),
                result.error_message,
                result.raw_log_path,
                _now(),
            ),
        )

    def update_evaluation_fitness(self, individual_id: str, fitness: float | None) -> None:
        """Fitness is now normalized against a whole generation's min/max
        (see EvolutionEngine._score_individuals), so a surviving parent's
        fitness can change every generation it is re-compared in, even
        though its own raw metrics (already recorded by save_evaluation)
        never change. Call this after rescoring a parent so its evaluations
        row reflects the value actually used for that generation's
        selection, instead of staying frozen at whatever it was when first
        evaluated."""
        self.connection.execute(
            "UPDATE evaluations SET fitness = ? WHERE individual_id = ?",
            (fitness, individual_id),
        )

    def save_population_membership(
        self, island_name: str, generation: int, individual: Individual
    ) -> None:
        method = "elite" if individual.is_elite else "roulette_without_replacement"
        if generation == 0:
            method = "initial"
        self.connection.execute(
            "INSERT OR REPLACE INTO population_memberships VALUES (?, ?, ?, ?, ?)",
            (island_name, generation, individual.individual_id, method, _now()),
        )

    def save_migration(
        self, source: Individual, copied: Individual, source_island: str, target_island: str
    ) -> None:
        self.connection.execute(
            "INSERT INTO migrations VALUES (?, ?, ?, ?, ?, ?)",
            (
                source.individual_id,
                copied.individual_id,
                source_island,
                target_island,
                copied.generation,
                _now(),
            ),
        )

    def save_llm_call(self, individual_id: str, call: LLMCallRecord) -> None:
        self.connection.execute(
            """
            INSERT INTO llm_calls (
                individual_id, model_name, prompt_text, response_text,
                success, error_message, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                individual_id,
                call.model_name,
                call.prompt_text,
                call.response_text,
                int(call.success),
                call.error_message,
                _now(),
            ),
        )

    def commit(self) -> None:
        self.connection.commit()

    def count(self, table: str) -> int:
        allowed = {
            "individuals",
            "evaluations",
            "population_memberships",
            "migrations",
            "change_history",
            "llm_calls",
        }
        if table not in allowed:
            raise ValueError(f"Unsupported table: {table}")
        row = self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return int(row[0])

    def close(self) -> None:
        self.connection.commit()
        self.connection.close()

    def __enter__(self) -> ExperimentDatabase:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
