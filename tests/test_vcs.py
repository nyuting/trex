"""commit_paths and the --commit flags of `trex parse`, `reconcile` and `summarize`,
against a throwaway git repository; `parse --commit-balanced` against a fake commit."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from trex import cli
from trex.models import CardBalance, Statement
from trex.reconcile import ReconcileReport
from trex.vcs import GitError, commit_paths


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


# --- parse --commit-balanced ---------------------------------------------


@pytest.fixture
def parse_without_pdfs(monkeypatch):
    """Parse without PDFs: statements named in `bad` fail the balance check.

    Returns the list that records each `_commit` call's (paths, message).
    """
    bad = {"UOB06"}
    commits = []
    monkeypatch.setattr(cli, "parse_statement", lambda name, **_: SimpleNamespace(name=name))
    monkeypatch.setattr(
        cli, "check_card_totals", lambda statement: ["mismatch"] if statement.name in bad else []
    )
    monkeypatch.setattr(
        cli, "_commit", lambda paths, message, push_after: commits.append((paths, message))
    )
    return commits


def test_commit_balanced_commits_only_the_balanced(data_dir, parse_without_pdfs):
    assert cli.main(["parse", "UOB06", "Chase06", "--commit-balanced"]) == 1
    [(paths, message)] = parse_without_pdfs
    assert {p.name for p in paths} == {"Chase06.csv"}
    assert message == "chore(data): parse Chase06"


def test_commit_balanced_with_nothing_balanced_commits_nothing(data_dir, parse_without_pdfs):
    assert cli.main(["parse", "UOB06", "--commit-balanced"]) == 1
    assert parse_without_pdfs == []


def test_commit_balanced_when_all_balance_commits_all(data_dir, parse_without_pdfs):
    assert cli.main(["parse", "Chase06", "PLG06", "--commit-balanced"]) == 0
    [(paths, message)] = parse_without_pdfs
    assert message == "chore(data): parse Chase06, PLG06"


# --- reconcile and summarize ----------------------------------------------


def test_reconcile_commit_takes_the_statement_csvs_and_cat_csv(repo, monkeypatch):
    # an extracted CSV left uncommitted by a `trex parse` run without --commit
    (repo / "data" / "extracted" / "UOB01.csv").write_text("re-extracted\n")

    def fake_reconcile(names):
        (repo / "data" / "parsed" / "UOB01.csv").write_text("regrouped\n")
        (repo / "data" / "categories" / "cat.csv").write_text("regex,category,card\n")
        return ReconcileReport()

    monkeypatch.setattr(cli, "reconcile_in_place", fake_reconcile)

    assert cli.main(["reconcile", "UOB01", "--commit"]) == 0
    assert git(repo, "log", "-1", "--format=%s").strip() == "chore(data): reconcile UOB01"
    assert committed_files(repo) == {
        "data/extracted/UOB01.csv",
        "data/parsed/UOB01.csv",
        "data/categories/cat.csv",
    }


def test_reconcile_commit_leaves_out_a_skipped_statement(repo, monkeypatch):
    def fake_reconcile(names):
        for name in names:
            (repo / "data" / "parsed" / f"{name}.csv").write_text("edited\n")
        return ReconcileReport(skipped=["UOB02"])

    monkeypatch.setattr(cli, "reconcile_in_place", fake_reconcile)

    assert cli.main(["reconcile", "UOB01", "UOB02", "--commit"]) == 1
    assert committed_files(repo) == {"data/parsed/UOB01.csv"}


# the fixture's UOB01 has a multi-category row, which summarize fails on by default
SUMMARIZE = ["summarize", "--year", "2026", "--no-fail-on-multi-category"]


def test_summarize_commit_takes_all_pipeline_data_and_nothing_else(repo):
    extracted = repo / "data" / "extracted" / "UOB01.csv"
    extracted.write_text(extracted.read_text() + "\n")
    (repo / "notes.txt").write_text("not pipeline data\n")

    assert cli.main([*SUMMARIZE, "--commit"]) == 0
    assert git(repo, "log", "-1", "--format=%s").strip() == "chore(data): summarize 2026"
    committed = committed_files(repo)
    assert {"data/extracted/UOB01.csv", "data/summary/summary2026.csv"} <= committed
    assert all(path.startswith(("data/extracted/", "data/summary/")) for path in committed)


def test_summarize_commit_skips_a_failing_check(repo, monkeypatch):
    head = git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(cli, "check_year_summary_total", lambda **_: (100.0, 1.0, 99.0))

    assert cli.main([*SUMMARIZE, "--commit"]) == 1
    assert git(repo, "rev-parse", "HEAD") == head


# --- pushing ----------------------------------------------------------------


@pytest.fixture
def remote(repo, tmp_path_factory) -> Path:
    """A bare repository that `repo`'s branch tracks."""
    remote = tmp_path_factory.mktemp("remote")
    git(remote, "init", "--quiet", "--bare")
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "--quiet", "--set-upstream", "origin", "HEAD")
    return remote


def test_commit_paths_pushes_when_asked(repo, remote):
    parsed = repo / "data" / "parsed" / "NEW01.csv"
    parsed.write_text("row\n")

    assert commit_paths([parsed], "chore(data): parse NEW01", cwd=repo, push=True)
    assert git(remote, "rev-parse", "HEAD") == git(repo, "rev-parse", "HEAD")


def test_a_failing_git_command_raises_git_error(repo):
    parsed = repo / "data" / "parsed" / "NEW01.csv"
    parsed.write_text("row\n")

    with pytest.raises(GitError) as raised:
        commit_paths([parsed], "chore(data): parse NEW01", cwd=repo, push=True)  # no upstream
    assert raised.value.command == "push"
    assert raised.value.output


def test_a_git_failure_exits_1_naming_the_command(repo, monkeypatch, caplog):
    def fake_reconcile(names):
        (repo / "data" / "parsed" / "UOB01.csv").write_text("regrouped\n")
        return ReconcileReport()

    monkeypatch.setattr(cli, "reconcile_in_place", fake_reconcile)

    assert cli.main(["reconcile", "UOB01", "--push"]) == 1  # no upstream
    assert "git push failed" in caplog.text


def test_push_pushes_what_was_committed(repo, remote, monkeypatch):
    def fake_reconcile(names):
        (repo / "data" / "parsed" / "UOB01.csv").write_text("regrouped\n")
        return ReconcileReport()

    monkeypatch.setattr(cli, "reconcile_in_place", fake_reconcile)

    assert cli.main(["reconcile", "UOB01", "--push"]) == 0
    assert git(remote, "rev-parse", "HEAD") == git(repo, "rev-parse", "HEAD")
