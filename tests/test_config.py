"""Paths must resolve identically no matter what the current directory is."""

from __future__ import annotations

from trex import config


def test_data_dir_defaults_to_repo_data(monkeypatch, tmp_path):
    monkeypatch.delenv("TREX_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    assert config.get_data_dir() == config.find_repo_root() / "data"


def test_data_dir_honours_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("TREX_DATA_DIR", str(tmp_path))
    assert config.get_data_dir() == tmp_path.resolve()
    assert config.get_cat_file() == tmp_path.resolve() / "categories" / "cat.csv"


def test_paths_are_stable_across_cwd_changes(monkeypatch, tmp_path):
    monkeypatch.delenv("TREX_DATA_DIR", raising=False)
    before = config.get_parsed_dir()
    monkeypatch.chdir(tmp_path)
    assert config.get_parsed_dir() == before


def test_override_is_read_per_call_not_cached(monkeypatch, tmp_path):
    monkeypatch.setenv("TREX_DATA_DIR", str(tmp_path / "a"))
    first = config.get_data_dir()
    monkeypatch.setenv("TREX_DATA_DIR", str(tmp_path / "b"))
    assert config.get_data_dir() != first


def test_find_repo_root_locates_pyproject():
    assert (config.find_repo_root() / "pyproject.toml").is_file()
