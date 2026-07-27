from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from llm_gp.config import AppConfig, load_config
from llm_gp.main import prepare_run_output_directory


def test_prepare_run_output_directory_creates_single_run_folder(tmp_path: Path) -> None:
    base_config = load_config(Path(__file__).parents[1] / "config" / "default.yaml")
    config = replace(
        base_config,
        database_path=tmp_path / "experiment_results" / "demo.db",
        source_directory=tmp_path / "experiment_results" / "sources",
        generation_csv=tmp_path / "experiment_results" / "summary.csv",
    )

    prepared_config, run_dir = prepare_run_output_directory(
        config,
        Path(__file__).parents[1] / "config" / "default.yaml",
    )

    assert prepared_config.database_path.parent == run_dir
    assert prepared_config.source_directory.parent == run_dir
    assert prepared_config.generation_csv.parent == run_dir
    assert (run_dir / "run_manifest.json").is_file()
    assert (run_dir / "default.yaml").is_file()
