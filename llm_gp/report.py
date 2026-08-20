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
from dataclasses import dataclass
from pathlib import Path

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


def generate_report(database_path: Path, output_directory: Path) -> dict[str, Path]:
    if not database_path.is_file():
        raise FileNotFoundError(database_path)
    output_directory.mkdir(parents=True, exist_ok=True)
    rows = load_rows(database_path)
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
    cumulative: list[ResultRow] = []
    for generation in sorted({row.generation for row in rows}):
        current = [row for row in successful if row.generation == generation]
        if not current:
            continue
        cumulative.extend(current)
        best = max(current, key=lambda row: row.fitness or 0.0)
        best_so_far = max(cumulative, key=lambda row: row.fitness or 0.0)
        average = sum(row.fitness or 0.0 for row in current) / len(current)
        records.append({
            "generation": generation,
            "evaluated": len([row for row in rows if row.generation == generation]),
            "successful": len(current),
            "average_fitness": average,
            "average_improvement_vs_initial_percent": _percent_change(baseline_average, average),
            "generation_best_id": best.individual_id,
            "generation_best_island": best.island,
            "generation_best_operator": best.operator_type,
            "generation_best_fitness": best.fitness,
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
            "best_so_far_id": best_so_far.individual_id,
            "best_so_far_generation": best_so_far.generation,
            "best_so_far_fitness": best_so_far.fitness,
            "best_so_far_improvement_vs_initial_best_percent": _percent_change(
                baseline_best.fitness if initial_successful else None, best_so_far.fitness
            ),
        })
        diff_path = output_directory / f"generation_{generation:03d}_best_{best.individual_id}.diff"
        _write_diff(diff_path, baseline_source, _read_source(best.source_path), baseline_best.source_path, best.source_path)
        diffs.append((generation, best, diff_path))
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
    markdown = output_directory / "generation_algorithm_report.md"
    _write_markdown(markdown, database_path, baseline_best, baseline_average,
                    len(initial_successful), len(initial_all), records, diffs)
    outputs = {
        "generation_csv": generation_csv,
        "individual_csv": individual_csv,
        "repetition_csv": repetition_csv,
        "metric_csv": metric_csv,
        "metric_markdown": metric_markdown,
        "markdown": markdown,
    }
    outputs.update(best_outputs)
    return outputs


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
                    diffs: list[tuple[int, ResultRow, Path]]) -> None:
    average_text = f"{baseline_average:.8f}" if baseline_average is not None else "算出不能"
    lines = [
        "# 世代・アルゴリズム比較レポート", "", f"- Database: `{database_path}`",
        f"- 初期評価成功数: `{baseline_successful}/{baseline_evaluated}`",
        f"- 初期基準個体: `{baseline_best.individual_id}`",
        f"- 初期平均適応度: `{average_text}`", "",
    ]
    if baseline_successful == 0:
        lines.extend(["> 世代0のROS/Gazebo評価がすべて失敗したため、初期値に対する改善率は算出できません。", ""])
    lines.extend(["## 世代比較", "",
                  "| 世代 | 成功/評価 | 平均適応度 | 世代最良個体 | 演算 | 最良適応度 | 初期最良比 |",
                  "|---:|---:|---:|---|---|---:|---:|"])
    for row in records:
        improvement = row["best_so_far_improvement_vs_initial_best_percent"]
        improvement_text = f"{improvement:+.2f}%" if isinstance(improvement, (int, float)) else "算出不能"
        lines.append(f"| {row['generation']} | {row['successful']}/{row['evaluated']} | "
                     f"{row['average_fitness']:.6f} | `{row['generation_best_id']}` | "
                     f"`{row['generation_best_operator']}` | {row['generation_best_fitness']:.6f} | {improvement_text} |")
    lines.extend(["", "## 各世代の最良アルゴリズム", ""])
    for generation, best, diff_path in diffs:
        lines.extend([
            f"### 世代 {generation}: `{best.individual_id}`", "", f"- 島: `{best.island}`",
            f"- 演算: `{best.operator_type}`", f"- 親: `{', '.join(best.parent_ids) or 'なし'}`",
            f"- 適応度: `{best.fitness:.8f}`",
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
    args = parser.parse_args()
    for name, output in generate_report(args.database, args.output).items():
        print(f"{name}: {output.resolve()}")


if __name__ == "__main__":
    main()
