"""commit_paths and `trex parse --commit`, against a throwaway git repository."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from trex import cli
from trex.models import CardBalance, Statement
from trex.reconcile import ReconcileReport
from trex.vcs import commit_paths


def git(repo: Path, *args: str) -> str:
    """Run git in `repo` and return its stdout."""
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path, data_dir) -> Path:
    """A git repository at tmp_path holding the fixture data tree, all committed."""
    git(tmp_path, "init", "--quiet")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "core.hooksPath", "/dev/null")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "--quiet", "-m", "initial")
    return tmp_path


def committed_files(repo: Path) -> set[str]:
    """Return the paths changed by HEAD."""
    return set(git(repo, "show", "--name-only", "--format=", "HEAD").split())


def test_commits_only_the_given_paths(repo):
    parsed = repo / "data" / "parsed" / "NEW01.csv"
    parsed.write_text("row\n")
    other = repo / "other.txt"
    other.write_text("staged elsewhere\n")
    git(repo, "add", "other.txt")

    assert commit_paths([parsed], "chore(data): parse NEW01", cwd=repo)

    assert committed_files(repo) == {"data/parsed/NEW01.csv"}
    assert git(repo, "diff", "--cached", "--name-only").split() == ["other.txt"]


def test_unchanged_paths_make_no_commit(repo):
    head = git(repo, "rev-parse", "HEAD")
    cat_file = repo / "data" / "categories" / "cat.csv"

    assert not commit_paths([cat_file, repo / "missing.csv"], "chore: nothing", cwd=repo)
    assert git(repo, "rev-parse", "HEAD") == head


def test_parse_commit_includes_extracted_and_parsed(repo, monkeypatch):
    def fake_parse(name, **_kwargs):
        for directory in ("extracted", "parsed"):
            (repo / "data" / directory / f"{name}.csv").write_text(f"{directory}\n")
        return Statement(name=name, issuer="paylah")

    monkeypatch.setattr(cli, "parse_statement", fake_parse)

    assert cli.main(["parse", "NEW01", "NEW02", "--commit"]) == 0
    assert git(repo, "log", "-1", "--format=%s").strip() == "chore(data): parse NEW01, NEW02"
    assert committed_files(repo) == {
        f"data/{directory}/{name}.csv"
        for directory in ("extracted", "parsed")
        for name in ("NEW01", "NEW02")
    }


def test_parse_commit_skips_an_unbalanced_statement(repo, monkeypatch):
    head = git(repo, "rev-parse", "HEAD")

    def fake_parse(name, **_kwargs):
        (repo / "data" / "parsed" / f"{name}.csv").write_text("row\n")
        return Statement(
            name=name,
            issuer="uob",
            balances={"CARD": CardBalance(card="CARD", previous=0.0, stated_total=99.0)},
        )

    monkeypatch.setattr(cli, "parse_statement", fake_parse)

    assert cli.main(["parse", "NEW01", "--commit"]) == 1
    assert git(repo, "rev-parse", "HEAD") == head


def test_reconcile_commit_takes_the_parsed_csv_and_cat_csv(repo, monkeypatch):
    def fake_reconcile(names):
        (repo / "data" / "parsed" / "UOB01.csv").write_text("regrouped\n")
        (repo / "data" / "categories" / "cat.csv").write_text("regex,category,card\n")
        return ReconcileReport()

    monkeypatch.setattr(cli, "reconcile_in_place", fake_reconcile)

    assert cli.main(["reconcile", "UOB01", "--commit"]) == 0
    assert git(repo, "log", "-1", "--format=%s").strip() == "chore(data): reconcile UOB01"
    assert committed_files(repo) == {"data/parsed/UOB01.csv", "data/categories/cat.csv"}


def test_reconcile_commit_leaves_out_a_skipped_statement(repo, monkeypatch):
    def fake_reconcile(names):
        for name in names:
            (repo / "data" / "parsed" / f"{name}.csv").write_text("edited\n")
        return ReconcileReport(skipped=["UOB02"])

    monkeypatch.setattr(cli, "reconcile_in_place", fake_reconcile)

    assert cli.main(["reconcile", "UOB01", "UOB02", "--commit"]) == 1
    assert committed_files(repo) == {"data/parsed/UOB01.csv"}


def test_push_pushes_what_was_committed(repo, tmp_path_factory, monkeypatch):
    remote = tmp_path_factory.mktemp("remote")
    git(remote, "init", "--quiet", "--bare")
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "--quiet", "--set-upstream", "origin", "HEAD")

    def fake_reconcile(names):
        (repo / "data" / "parsed" / "UOB01.csv").write_text("regrouped\n")
        return ReconcileReport()

    monkeypatch.setattr(cli, "reconcile_in_place", fake_reconcile)

    assert cli.main(["reconcile", "UOB01", "--push"]) == 0
    assert git(remote, "rev-parse", "HEAD") == git(repo, "rev-parse", "HEAD")
