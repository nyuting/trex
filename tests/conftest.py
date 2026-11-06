"""Shared fixtures. Tests never touch the real data tree."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from trex.categorize import reset_rules
from trex.categorize.rules import CatRules
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


@pytest.fixture(autouse=True)
def isolate_git(monkeypatch):
    """Drop GIT_* variables so git in a test only ever sees the test's own repo.

    A git hook exports GIT_DIR and friends, and pre-push runs this suite: without
    this, test_vcs's throwaway `git init --bare` and `git config` rewrote the real
    repo's .git/config, leaving it bare with hooks off.
    """
    for name in [name for name in os.environ if name.startswith("GIT_")]:
        monkeypatch.delenv(name)


@pytest.fixture
def data_dir(tmp_path, monkeypatch) -> Path:
    """Point trex at a throwaway copy of tests/fixtures/data via TREX_DATA_DIR."""
    destination = tmp_path / "data"
    shutil.copytree(FIXTURES_DIR / "data", destination)
    monkeypatch.setenv("TREX_DATA_DIR", str(destination))
    return destination


@pytest.fixture
def rules(data_dir) -> CatRules:
    """The fixture tree's cat.csv and cat_personal.csv, loaded."""
    return CatRules().load()


@pytest.fixture
def parsed_dir(tmp_path, monkeypatch):
    """A parsed/ directory in a throwaway data tree, so summaries land in tmp_path/summary."""
    monkeypatch.setenv("TREX_DATA_DIR", str(tmp_path))
    directory = tmp_path / "parsed"
    directory.mkdir()
    return directory


@pytest.fixture
def summary_dir(parsed_dir):
    return parsed_dir.parent / "summary"
