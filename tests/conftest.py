from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from llm_gp.config import AppConfig, load_config


@pytest.fixture
def test_config(tmp_path: Path) -> AppConfig:
    base = load_config(Path(__file__).parents[1] / "config" / "default.yaml")
    return replace(
        base,
        evolution=replace(base.evolution, max_generations=1),
        database_path=tmp_path / "evolution.db",
        source_directory=tmp_path / "sources",
        generation_csv=tmp_path / "summary.csv",
        llm=replace(base.llm, provider="mock"),
    )
