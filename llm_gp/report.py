from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import json
import re
import shutil
import sqlite3
import statistics
from dataclasses import dataclass, replace
from pathlib import Path

from .config import FitnessSettings, load_config
from .fitness import calculate_fitness, compute_generation_stats
from .models import EvaluationResult

_WEIGHT_PATTERN = re.compile(r"constexpr\s+float\s+kLlmGpHeuristicWeight\s*=\s*([0-9.]+)f\s*;")


@dataclass(frozen=True)
class ResultRow:
    individual_id: str
    generation: int
    island: str
    operator_type: str
    parent_ids: list[str]
    source_path: Path
    success: bool
    fitness: float | None
    planning_time: float | None
    path_length: float | None
    arrival_time: float | None
    node_expansions: float | None
    changes: str
    heuristic_weight: float | None
    source_sha256: str | None


def select_baseline_and_best(
    rows: list[ResultRow],
) -> tuple[ResultRow, ResultRow | None]:
    """The generation-0 fittest successful individual (falling back to the
    fittest generation-0 row at all if none succeeded), and the fittest
    successful individual across the whole run (None if nothing succeeded).

    Used both for the generation report below and for the optional
    initial-vs-best repeated-evaluation check run after a GP experiment
    (see llm_gp/main.py and llm_gp/verification.py).

    IMPORTANT: `rows` must come from
    `load_rows_with_corrected_generation_zero_fitness`, not the raw
    `load_rows`. A generation-0 individual that survives into later
    generations gets rescored every generation it lives on (fitness is
    generation-relative), so `evaluations.fitness` -- and therefore plain
    `load_rows` -- only ever holds its *last* rescoring, not its true
    generation-0 value. Picking `baseline_best` from that raw data can name
    the wrong individual as the initial baseline; see
    docs/lattice_fork_minmax_5gen_experiment_report.md for a real case this
    caused (and tests/test_report.py's
    test_select_baseline_and_best_requires_generation_zero_corrected_fitness
    for a regression test).
    """
    successful = [row for row in rows if row.success and row.fitness is not None]
    initial_all = [row for row in rows if row.generation == 0]
    if not initial_all:
        raise ValueError("Generation zero baseline evaluations are missing")
    initial_successful = [row for row in successful if row.generation == 0]
    baseline_pool = initial_successful or initial_all
    baseline_best = max(baseline_pool, key=lambda row: row.fitness or 0.0)
    experiment_best = (
        max(successful, key=lambda row: row.fitness or 0.0) if successful else None
    )
    return baseline_best, experiment_best


def load_rows_with_corrected_generation_zero_fitness(
    database_path: Path, fitness_settings: FitnessSettings
) -> list[ResultRow]:
    """load_rows(), but with every generation-0 row's fitness corrected to
    its true generation-0-pool-relative value.

    Generation 0's individuals may have gone on to survive into (and be
    rescored in) many later generations, so evaluations.fitness for them
    reflects their *last* rescoring rather than their original generation-0
    value. Anywhere generation 0 is treated as "the initial baseline"
    (select_baseline_and_best, and therefore both this module's own report
    and the standalone initial-vs-best verification in
    scripts/verify_generation_gain.py and llm_gp/main.py) must correct for
    this first, or the wrong individual can be picked as the baseline --
    see docs/lattice_fork_minmax_5gen_experiment_report.md for a real case
    this caused. Generation 0 has no parents, so its own scoring pool is
    simply its own population.
    """
    rows = load_rows(database_path)
    rows_by_id = {row.individual_id: row for row in rows}
    population_by_generation = _load_population_by_generation(database_path)
    gen0_stats = _reconstruct_generation_stats(0, population_by_generation, rows_by_id)
    return [
        replace(row, fitness=calculate_fitness(_result_from_row(row), fitness_settings, gen0_stats))
        if row.generation == 0 and row.success
        else row
        for row in rows
    ]


def generate_report(
    database_path: Path, output_directory: Path, fitness_settings: FitnessSettings
) -> dict[str, Path]:
    if not database_path.is_file():
        raise FileNotFoundError(database_path)
    output_directory.mkdir(parents=True, exist_ok=True)
    rows = load_rows_with_corrected_generation_zero_fitness(database_path, fitness_settings)
    rows_by_id = {row.individual_id: row for row in rows}
    population_by_generation = _load_population_by_generation(database_path)

    baseline_best, experiment_best = select_baseline_and_best(rows)
    successful = [row for row in rows if row.success and row.fitness is not None]
    initial_successful = [row for row in successful if row.generation == 0]
    baseline_average = (
        sum(row.fitness or 0.0 for row in initial_successful) / len(initial_successful)
        if initial_successful else None
    )
    baseline_source = _read_source(baseline_best.source_path)
    initial_all = [row for row in rows if row.generation == 0]

    individual_csv = output_directory / "individual_comparison.csv"
    _write_individual_csv(individual_csv, rows)
    repetition_csv = output_directory / "repetition_statistics.csv"
    _write_repetition_statistics(database_path, repetition_csv)
    generation_csv = output_directory / "generation_comparison.csv"
    records: list[dict[str, object]] = []
    diffs: list[tuple[int, ResultRow, Path]] = []
    running_best_id: str | None = None
    running_best_generation: int | None = None
    running_best_fitness: float | None = None
    for generation in sorted({row.generation for row in rows} | set(population_by_generation)):
        born_this_generation = [row for row in rows if row.generation == generation]
        # Recompute fitness for this generation's *entire* actual scoring
        # pool (population entering the generation, plus the children newly
        # born in it) in one pass -- evaluations.fitness only ever stores an
        # individual's most recent rescoring, so both "how good were this
        # generation's own new children" and "who is the fittest individual
        # actually in this generation's population" need values recomputed
        # against this generation's own pool rather than read directly.
        stats = _reconstruct_generation_stats(generation, population_by_generation, rows_by_id)
        pool_ids = _reconstruct_generation_pool_ids(generation, population_by_generation, rows_by_id)
        pool_fitness: dict[str, float] = {}
        for individual_id in pool_ids:
            row = rows_by_id.get(individual_id)
            if row is None or not row.success:
                continue
            pool_fitness[individual_id] = calculate_fitness(_result_from_row(row), fitness_settings, stats)

        born_success_ids = [row.individual_id for row in born_this_generation if row.individual_id in pool_fitness]
        if not born_success_ids and generation not in population_by_generation:
            continue
        average = (
            sum(pool_fitness[iid] for iid in born_success_ids) / len(born_success_ids)
            if born_success_ids else 0.0
        )

        # "generation_best" means the fittest individual actually present in
        # this generation's population (survivors carried over included), not
        # merely the fittest among the children newly born this generation --
        # a strong individual born several generations back (and still living
        # on as a survivor/elite) should be reported as this generation's best
        # if nothing newer has beaten it yet.
        population_fitness = [
            (individual_id, pool_fitness[individual_id])
            for individual_id in population_by_generation.get(generation, set())
            if individual_id in pool_fitness
        ]
        if population_fitness:
            best_id, best_fitness = max(population_fitness, key=lambda pair: pair[1])
        elif born_success_ids:
            best_id = max(born_success_ids, key=lambda iid: pool_fitness[iid])
            best_fitness = pool_fitness[best_id]
        else:
            best_id, best_fitness = None, None
        best = rows_by_id[best_id] if best_id is not None else max(born_this_generation, key=lambda row: row.fitness or 0.0)

        if best_fitness is not None and (running_best_fitness is None or best_fitness > running_best_fitness):
            running_best_id, running_best_generation, running_best_fitness = best_id, generation, best_fitness

        records.append({
            "generation": generation,
            "evaluated": len(born_this_generation),
            "successful": len(born_success_ids),
            "average_fitness": average,
            "average_improvement_vs_initial_percent": _percent_change(baseline_average, average),
            "generation_best_id": best.individual_id,
            "generation_best_island": best.island,
            "generation_best_operator": best.operator_type,
            "generation_best_fitness": best_fitness,
            "generation_best_planning_time": best.planning_time,
            "planning_time_improvement_vs_initial_percent": _reduction_percent(
                baseline_best.planning_time if initial_successful else None,
                best.planning_time,
            ),
            "generation_best_node_expansions": best.node_expansions,
            "node_expansions_improvement_vs_initial_percent": _reduction_percent(
                baseline_best.node_expansions if initial_successful else None,
                best.node_expansions,
            ),
            "generation_best_path_length": best.path_length,
            "path_length_improvement_vs_initial_percent": _reduction_percent(
                baseline_best.path_length if initial_successful else None,
                best.path_length,
            ),
            "generation_best_arrival_time": best.arrival_time,
            "arrival_time_improvement_vs_initial_percent": _reduction_percent(
                baseline_best.arrival_time if initial_successful else None,
                best.arrival_time,
            ),
            "generation_best_heuristic_weight": best.heuristic_weight,
            "best_so_far_id": running_best_id,
            "best_so_far_generation": running_best_generation,
            "best_so_far_fitness": running_best_fitness,
            "best_so_far_improvement_vs_initial_best_percent": _percent_change(
                baseline_best.fitness if initial_successful else None, running_best_fitness
            ),
            # Unlike best_so_far above (which is pinned to whichever generation
            # first produced the overall-best individual, and never moves once
            # set), this is recomputed fresh each generation from that
            # generation's own population -- so it will track best_so_far
            # exactly while the reigning best individual is still alive in the
            # population, and can only differ if it has since been dropped.
            "generation_best_improvement_vs_initial_best_percent": _percent_change(
                baseline_best.fitness if initial_successful else None, best_fitness
            ),
        })
        best_display = replace(best, fitness=best_fitness)
        diff_path = output_directory / f"generation_{generation:03d}_best_{best.individual_id}.diff"
        _write_diff(diff_path, baseline_source, _read_source(best.source_path), baseline_best.source_path, best.source_path)
        diffs.append((generation, best_display, diff_path))
    _write_dict_csv(generation_csv, records)
    metric_csv = output_directory / "metric_comparison.csv"
    _write_metric_csv(metric_csv, records)
    metric_markdown = output_directory / "metric_comparison.md"
    _write_metric_markdown(
        metric_markdown,
        baseline_best,
        experiment_best,
        records,
    )
    best_outputs = _export_best_algorithm(
        output_directory / "best_algorithm", baseline_best, experiment_best
    )
    token_usage_csv = output_directory / "token_usage_by_generation.csv"
    token_totals = _write_token_usage_csv(database_path, token_usage_csv)
    markdown = output_directory / "generation_algorithm_report.md"
    _write_markdown(markdown, database_path, baseline_best, baseline_average,
                    len(initial_successful), len(initial_all), records, diffs, token_totals)
    outputs = {
        "generation_csv": generation_csv,
        "individual_csv": individual_csv,
        "repetition_csv": repetition_csv,
        "metric_csv": metric_csv,
        "metric_markdown": metric_markdown,
        "markdown": markdown,
        "token_usage_csv": token_usage_csv,
    }
    outputs.update(best_outputs)
    return outputs


def _load_population_by_generation(database_path: Path) -> dict[int, set[str]]:
    connection = sqlite3.connect(database_path)
    records = connection.execute(
        "SELECT generation, individual_id FROM population_memberships"
    ).fetchall()
    connection.close()
    by_generation: dict[int, set[str]] = {}
    for generation, individual_id in records:
        by_generation.setdefault(int(generation), set()).add(individual_id)
    return by_generation


def token_usage_summary(database_path: Path) -> dict[str, int]:
    """Whole-experiment LLM token totals, for printing a one-line summary
    right after a run finishes (see llm_gp/main.py) without needing to open
    the generated report. `calls_missing_usage` counts calls whose response
    carried no usage data (e.g. mock operators, or an older run's database
    predating this column) and are therefore excluded from the token sums."""
    return _aggregate_token_usage(_load_llm_call_rows(database_path))[1]


def _load_llm_call_rows(
    database_path: Path,
) -> list[tuple[int, bool, int | None, int | None, int | None]]:
    connection = sqlite3.connect(database_path)
    try:
        return connection.execute(
            """
            SELECT i.generation, c.success, c.prompt_tokens, c.completion_tokens, c.total_tokens
            FROM llm_calls c JOIN individuals i USING(individual_id)
            """
        ).fetchall()
    finally:
        connection.close()


def _new_usage_bucket() -> dict[str, int]:
    return {
        "calls": 0,
        "successful_calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "calls_missing_usage": 0,
    }


def _aggregate_token_usage(
    records: list[tuple[int, bool, int | None, int | None, int | None]],
) -> tuple[dict[int, dict[str, int]], dict[str, int]]:
    by_generation: dict[int, dict[str, int]] = {}
    totals = _new_usage_bucket()
    for generation, success, prompt_tokens, completion_tokens, total_tokens in records:
        bucket = by_generation.setdefault(int(generation), _new_usage_bucket())
        for target in (bucket, totals):
            target["calls"] += 1
            if success:
                target["successful_calls"] += 1
            if total_tokens is None:
                target["calls_missing_usage"] += 1
                continue
            target["prompt_tokens"] += prompt_tokens or 0
            target["completion_tokens"] += completion_tokens or 0
            target["total_tokens"] += total_tokens
    return by_generation, totals


def _write_token_usage_csv(database_path: Path, path: Path) -> dict[str, int]:
    by_generation, totals = _aggregate_token_usage(_load_llm_call_rows(database_path))
    fields = [
        "generation", "calls", "successful_calls", "calls_missing_usage",
        "prompt_tokens", "completion_tokens", "total_tokens",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for generation in sorted(by_generation):
            writer.writerow({"generation": generation, **by_generation[generation]})
    return totals


def _result_from_row(row: ResultRow) -> EvaluationResult:
    return EvaluationResult(
        row.success, row.planning_time, row.path_length, row.arrival_time,
        node_expansions=row.node_expansions,
    )


def _reconstruct_generation_pool_ids(
    generation: int, population_by_generation: dict[int, set[str]], rows_by_id: dict[str, ResultRow]
) -> set[str]:
    """The exact set of individuals EvolutionEngine._score_individuals scored
    together when it last (re)computed fitness for this generation: the
    population entering the generation (its parents; for generation 0 there
    are none, so it's just generation 0's own population) plus every
    individual born in this generation (its children).

    Needed because evaluations.fitness only ever stores an individual's most
    recent rescoring -- a survivor that lives on for several more generations
    keeps getting overwritten, so simply reading row.fitness for an older
    generation's row would show a later generation's value instead of what
    was actually true at the time. Recomputing fitness against this
    generation's own reconstructed pool (see _reconstruct_generation_stats)
    recovers the true historical value.
    """
    if generation == 0:
        return set(population_by_generation.get(0, set()))
    pool_ids = set(population_by_generation.get(generation - 1, set()))
    pool_ids |= {
        row.individual_id for row in rows_by_id.values() if row.generation == generation
    }
    return pool_ids


def _reconstruct_generation_stats(
    generation: int, population_by_generation: dict[int, set[str]], rows_by_id: dict[str, ResultRow]
):
    pool_ids = _reconstruct_generation_pool_ids(generation, population_by_generation, rows_by_id)
    results = [
        _result_from_row(rows_by_id[individual_id])
        for individual_id in pool_ids
        if individual_id in rows_by_id
    ]
    return compute_generation_stats(results)


def _historical_fitness(
    row: ResultRow,
    generation: int,
    population_by_generation: dict[int, set[str]],
    rows_by_id: dict[str, ResultRow],
    fitness_settings: FitnessSettings,
) -> float | None:
    """row's fitness as it actually was at `generation`, recomputed against
    that generation's own reconstructed scoring pool rather than read
    directly from the (possibly since-overwritten) evaluations.fitness
    column. Falls back to the stored value if the pool can't be
    reconstructed (e.g. a database from before population_memberships
    tracked this, or the row's own metrics are missing/non-finite)."""
    if not row.success:
        return row.fitness
    stats = _reconstruct_generation_stats(generation, population_by_generation, rows_by_id)
    return calculate_fitness(_result_from_row(row), fitness_settings, stats)


def load_rows(database_path: Path) -> list[ResultRow]:
    connection = sqlite3.connect(database_path)
    records = connection.execute("""
        SELECT i.individual_id, i.generation, i.current_island, i.operator_type,
               i.parent_ids, i.source_path, e.success, e.fitness,
               e.planning_time, e.path_length, e.arrival_time, e.node_expansions,
               COALESCE(GROUP_CONCAT(c.description, ' | '), '')
        FROM individuals i JOIN evaluations e USING(individual_id)
        LEFT JOIN change_history c USING(individual_id)
        GROUP BY i.individual_id ORDER BY i.generation, i.individual_id
    """).fetchall()
    connection.close()
    rows = []
    for record in records:
        source_path = Path(record[5])
        source = _read_source(source_path)
        weight = _WEIGHT_PATTERN.search(source)
        rows.append(ResultRow(
            record[0], int(record[1]), record[2], record[3], json.loads(record[4]),
            source_path, bool(record[6]), record[7], record[8], record[9], record[10],
            record[11], record[12], float(weight.group(1)) if weight else None,
            hashlib.sha256(source.encode()).hexdigest() if source else None,
        ))
    return rows


def _write_individual_csv(path: Path, rows: list[ResultRow]) -> None:
    fields = ["individual_id", "generation", "island", "operator_type", "parent_ids",
              "success", "fitness", "planning_time", "path_length", "arrival_time",
              "node_expansions", "heuristic_weight", "source_sha256", "source_path", "changes"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: json.dumps(row.parent_ids) if name == "parent_ids" else getattr(row, name) for name in fields})


def _write_repetition_statistics(database_path: Path, path: Path) -> None:
    connection = sqlite3.connect(database_path)
    evaluation_logs = connection.execute(
        "SELECT individual_id, raw_log_path FROM evaluations ORDER BY individual_id"
    ).fetchall()
    connection.close()
    fields = [
        "individual_id", "attempted_repetitions", "successful_repetitions",
        "success_rate_percent", "planning_time_mean", "planning_time_stddev",
        "path_length_mean", "path_length_stddev", "arrival_time_mean",
        "arrival_time_stddev", "node_expansions_mean", "node_expansions_stddev",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for individual_id, raw_pattern in evaluation_logs:
            files = _resolve_log_files(raw_pattern)
            payloads = []
            for file_path in files:
                try:
                    payloads.append(json.loads(file_path.read_text(encoding="utf-8")))
                except (OSError, json.JSONDecodeError):
                    continue
            successful = [payload for payload in payloads if payload.get("success")]
            row: dict[str, object] = {
                "individual_id": individual_id,
                "attempted_repetitions": len(payloads),
                "successful_repetitions": len(successful),
                "success_rate_percent": (
                    len(successful) / len(payloads) * 100.0 if payloads else None
                ),
            }
            for metric in ("planning_time", "path_length", "arrival_time", "node_expansions"):
                values = [
                    float(payload[metric])
                    for payload in successful
                    if payload.get(metric) is not None
                ]
                row[f"{metric}_mean"] = statistics.mean(values) if values else None
                row[f"{metric}_stddev"] = statistics.stdev(values) if len(values) >= 2 else None
            writer.writerow(row)


def _resolve_log_files(raw_pattern: str | None) -> list[Path]:
    if not raw_pattern:
        return []
    pattern = Path(raw_pattern)
    if "*" not in pattern.name:
        return [pattern] if pattern.is_file() else []
    return sorted(pattern.parent.glob(pattern.name))


def _write_dict_csv(path: Path, records: list[dict[str, object]]) -> None:
    if not records:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)


def _write_metric_csv(path: Path, records: list[dict[str, object]]) -> None:
    fields = [
        "generation",
        "successful",
        "evaluated",
        "generation_best_id",
        "generation_best_operator",
        "generation_best_fitness",
        "generation_best_planning_time",
        "planning_time_improvement_vs_initial_percent",
        "generation_best_node_expansions",
        "node_expansions_improvement_vs_initial_percent",
        "generation_best_path_length",
        "path_length_improvement_vs_initial_percent",
        "generation_best_arrival_time",
        "arrival_time_improvement_vs_initial_percent",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def _format_metric(value: object, digits: int = 6) -> str:
    return f"{value:.{digits}f}" if isinstance(value, (int, float)) else "算出不能"


def _format_improvement(value: object) -> str:
    return f"{value:+.2f}%" if isinstance(value, (int, float)) else "算出不能"


def _write_metric_markdown(
    path: Path,
    baseline_best: ResultRow,
    experiment_best: ResultRow | None,
    records: list[dict[str, object]],
) -> None:
    lines = [
        "# 3指標による世代比較",
        "",
        "正の改善率は、初期最良個体より値が小さく改善したことを表します。",
        "",
        f"- 初期最良個体: `{baseline_best.individual_id}`",
    ]
    if experiment_best is not None:
        lines.extend([
            f"- 実験全体の最良個体: `{experiment_best.individual_id}`（世代 {experiment_best.generation}）",
            "",
            "## 初期最良と実験全体最良",
            "",
            "| 指標 | 初期最良 | 実験全体最良 | 改善率 |",
            "|---|---:|---:|---:|",
            f"| 経路生成時間 [秒]（参考・適応度には非使用） | {_format_metric(baseline_best.planning_time)} | {_format_metric(experiment_best.planning_time)} | {_format_improvement(_reduction_percent(baseline_best.planning_time, experiment_best.planning_time))} |",
            f"| ノード展開数（適応度で使用） | {_format_metric(baseline_best.node_expansions, 1)} | {_format_metric(experiment_best.node_expansions, 1)} | {_format_improvement(_reduction_percent(baseline_best.node_expansions, experiment_best.node_expansions))} |",
            f"| 経路長 [m] | {_format_metric(baseline_best.path_length)} | {_format_metric(experiment_best.path_length)} | {_format_improvement(_reduction_percent(baseline_best.path_length, experiment_best.path_length))} |",
            f"| 到達時間 [秒] | {_format_metric(baseline_best.arrival_time)} | {_format_metric(experiment_best.arrival_time)} | {_format_improvement(_reduction_percent(baseline_best.arrival_time, experiment_best.arrival_time))} |",
        ])
    lines.extend([
        "",
        "## 各世代の適応度最良個体",
        "",
        "| 世代 | 個体 | 経路生成時間 [秒] | 初期比 | 経路長 [m] | 初期比 | 到達時間 [秒] | 初期比 |",
        "|---:|---|---:|---:|---:|---:|---:|---:|",
    ])
    for row in records:
        lines.append(
            f"| {row['generation']} | `{row['generation_best_id']}` | "
            f"{_format_metric(row['generation_best_planning_time'])} | "
            f"{_format_improvement(row['planning_time_improvement_vs_initial_percent'])} | "
            f"{_format_metric(row['generation_best_path_length'])} | "
            f"{_format_improvement(row['path_length_improvement_vs_initial_percent'])} | "
            f"{_format_metric(row['generation_best_arrival_time'])} | "
            f"{_format_improvement(row['arrival_time_improvement_vs_initial_percent'])} |"
        )
    lines.extend([
        "",
        "> 各個体1回評価の予備実験であるため、改善率にはGazebo走行のばらつきが含まれます。",
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def _export_best_algorithm(
    directory: Path,
    baseline_best: ResultRow,
    experiment_best: ResultRow | None,
) -> dict[str, Path]:
    if experiment_best is None or not experiment_best.source_path.is_file():
        return {}
    directory.mkdir(parents=True, exist_ok=True)
    suffix = experiment_best.source_path.suffix or ".txt"
    best_source = directory / f"best_algorithm{suffix}"
    shutil.copy2(experiment_best.source_path, best_source)
    diff_path = directory / "diff_from_initial.diff"
    _write_diff(
        diff_path,
        _read_source(baseline_best.source_path),
        _read_source(experiment_best.source_path),
        baseline_best.source_path,
        experiment_best.source_path,
    )
    info_path = directory / "README.md"
    info_path.write_text(
        "\n".join([
            "# 実験全体の最良アルゴリズム",
            "",
            f"- 個体ID: `{experiment_best.individual_id}`",
            f"- 世代: `{experiment_best.generation}`",
            f"- 島: `{experiment_best.island}`",
            f"- 生成方法: `{experiment_best.operator_type}`",
            f"- 適応度: `{experiment_best.fitness}`",
            f"- 経路生成時間（参考・適応度には非使用）: `{experiment_best.planning_time}` 秒",
            f"- ノード展開数（適応度で使用）: `{experiment_best.node_expansions}`",
            f"- 経路長: `{experiment_best.path_length}` m",
            f"- 到達時間: `{experiment_best.arrival_time}` 秒",
            f"- 元ファイル: `{experiment_best.source_path}`",
            f"- 保存コード: `{best_source.name}`",
            f"- 初期コードとの差分: `{diff_path.name}`",
            "",
            "> 最良とは設定された重み付き適応度が最大という意味です。",
            "",
        ]),
        encoding="utf-8",
    )
    return {
        "best_source": best_source,
        "best_info": info_path,
        "best_diff": diff_path,
    }


def _write_diff(path: Path, baseline: str, candidate: str, baseline_path: Path, candidate_path: Path) -> None:
    path.write_text("".join(difflib.unified_diff(
        baseline.splitlines(keepends=True), candidate.splitlines(keepends=True),
        fromfile=str(baseline_path), tofile=str(candidate_path),
    )), encoding="utf-8")


def _write_markdown(path: Path, database_path: Path, baseline_best: ResultRow,
                    baseline_average: float | None, baseline_successful: int,
                    baseline_evaluated: int, records: list[dict[str, object]],
                    diffs: list[tuple[int, ResultRow, Path]],
                    token_totals: dict[str, int] | None = None) -> None:
    average_text = f"{baseline_average:.8f}" if baseline_average is not None else "算出不能"
    lines = [
        "# 世代・アルゴリズム比較レポート", "", f"- Database: `{database_path}`",
        f"- 初期評価成功数: `{baseline_successful}/{baseline_evaluated}`",
        f"- 初期基準個体: `{baseline_best.individual_id}`",
        f"- 初期平均適応度: `{average_text}`", "",
    ]
    if baseline_successful == 0:
        lines.extend(["> 世代0のROS/Gazebo評価がすべて失敗したため、初期値に対する改善率は算出できません。", ""])
    if token_totals and token_totals["calls"] > 0:
        lines.extend([
            "## LLMトークン使用量(実験全体)", "",
            f"- LLM呼び出し回数: `{token_totals['calls']}`(成功 `{token_totals['successful_calls']}`)",
            f"- prompt tokens: `{token_totals['prompt_tokens']}`",
            f"- completion tokens: `{token_totals['completion_tokens']}`",
            f"- total tokens: `{token_totals['total_tokens']}`",
        ])
        if token_totals["calls_missing_usage"] > 0:
            lines.append(
                f"- (usage情報なしの呼び出し: `{token_totals['calls_missing_usage']}`件。"
                "mock演算子、またはusageを返さない応答のため、上記トークン数には未集計)"
            )
        lines.extend(["", "世代ごとの内訳は `token_usage_by_generation.csv` を参照。", ""])
    lines.extend(["## 世代比較", "",
                  "> 「成功/評価」はその世代で新規に生まれた子個体のうち何体が評価に成功したか。"
                  "「世代最良個体」はその世代の**集団に実際に所属していた**個体(前の世代からの生存者を含む)の中の最良個体で、"
                  "新規に生まれた子だけの最良ではない。「世代最良比」はその世代最良個体を初期最良個体と比べた改善率、"
                  "「累積最良比」はそれまでに見つかった最良個体(best-so-far)による改善率(単調非減少)。"
                  "最良個体が世代をまたいで生存し続けている間は両者は一致する。",
                  "",
                  "| 世代 | 成功/評価 | 平均適応度 | 世代最良個体 | 演算 | 最良適応度 | 世代最良比 | 累積最良比 |",
                  "|---:|---:|---:|---|---|---:|---:|---:|"])
    for row in records:
        generation_improvement = row["generation_best_improvement_vs_initial_best_percent"]
        generation_improvement_text = (
            f"{generation_improvement:+.2f}%"
            if isinstance(generation_improvement, (int, float))
            else "算出不能"
        )
        cumulative_improvement = row["best_so_far_improvement_vs_initial_best_percent"]
        cumulative_improvement_text = (
            f"{cumulative_improvement:+.2f}%"
            if isinstance(cumulative_improvement, (int, float))
            else "算出不能"
        )
        generation_best_fitness = row["generation_best_fitness"]
        generation_best_fitness_text = (
            f"{generation_best_fitness:.6f}"
            if isinstance(generation_best_fitness, (int, float))
            else "算出不能"
        )
        lines.append(f"| {row['generation']} | {row['successful']}/{row['evaluated']} | "
                     f"{row['average_fitness']:.6f} | `{row['generation_best_id']}` | "
                     f"`{row['generation_best_operator']}` | {generation_best_fitness_text} | "
                     f"{generation_improvement_text} | {cumulative_improvement_text} |")
    lines.extend(["", "## 各世代の最良アルゴリズム", ""])
    for generation, best, diff_path in diffs:
        fitness_text = f"{best.fitness:.8f}" if best.fitness is not None else "算出不能"
        lines.extend([
            f"### 世代 {generation}: `{best.individual_id}`", "", f"- 島: `{best.island}`",
            f"- 演算: `{best.operator_type}`", f"- 親: `{', '.join(best.parent_ids) or 'なし'}`",
            f"- 適応度: `{fitness_text}`",
            f"- planning/path/arrival: `{best.planning_time}`, `{best.path_length}`, `{best.arrival_time}`",
            f"- 変更履歴: {best.changes or 'なし'}", f"- ソース: `{best.source_path}`",
            f"- 初期基準個体との差分: `{diff_path}`", "",
        ])
    lines.extend(["## 解釈上の注意", "",
                  "同一コードでも評価値は走行ごとに変動します。複数回試行の平均と分散を確認してください。", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def _read_source(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _percent_change(baseline: float | None, value: float | None) -> float | None:
    return None if baseline in (None, 0.0) or value is None else (value - baseline) / baseline * 100.0


def _reduction_percent(baseline: float | None, value: float | None) -> float | None:
    """Percentage improvement for metrics where a smaller value is better."""
    return None if baseline in (None, 0.0) or value is None else (baseline - value) / baseline * 100.0


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare fitness and algorithms by generation")
    parser.add_argument("--database", type=Path, default=Path("experiment_results/roulette_evolution.db"))
    parser.add_argument("--output", type=Path, default=Path("experiment_results/roulette_evolution_analysis"))
    parser.add_argument(
        "--config", type=Path, required=True,
        help="YAML config whose fitness weights/references produced this database "
             "(the run directory keeps a copy of the exact one used, alongside the .db).",
    )
    args = parser.parse_args()
    fitness_settings = load_config(args.config).fitness
    for name, output in generate_report(args.database, args.output, fitness_settings).items():
        print(f"{name}: {output.resolve()}")


if __name__ == "__main__":
    main()
