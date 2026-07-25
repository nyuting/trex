"""Shared fixtures. Tests never touch the real data tree."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from trex.categorize import reset_rules
from trex.log import get_logger

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def isolate_logging_and_rules():
    """Let caplog see trex logs, and never carry a shared rule set between tests."""
    reset_rules()
    logger = get_logger()
    logger.propagate = True
    yield
    reset_rules()


@pytest.fixture
def data_dir(tmp_path, monkeypatch) -> Path:
    """Point trex at a throwaway copy of tests/fixtures/data via TREX_DATA_DIR."""
    destination = tmp_path / "data"
    shutil.copytree(FIXTURES_DIR / "data", destination)
    monkeypatch.setenv("TREX_DATA_DIR", str(destination))
    return destination
