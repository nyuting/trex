"""CLI wiring: every subcommand reaches its library function and reports an exit code.

These exercise argument handling and dispatch; the behaviour behind each command
is covered by the module tests.
"""

from __future__ import annotations

import pytest

from trex import cli


def test_no_subcommand_is_an_error(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main([])
    assert exit_info.value.code != 0


def test_version_is_reported(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--version"])
    assert exit_info.value.code == 0
    assert "trex" in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv",
    [
        ["extract", "--help"],
        ["parse", "--help"],
        ["reconcile", "--help"],
        ["summarize", "--help"],
        ["rules", "--help"],
        ["rules", "suggest-brands", "--help"],
    ],
)
def test_every_subcommand_has_help(argv):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(argv)
    assert exit_info.value.code == 0


def test_rules_requires_an_action():
    with pytest.raises(SystemExit):
        cli.main(["rules"])


# --- commands that touch the fixture data tree --------------------------


def test_summarize_writes_a_summary(data_dir):
    assert cli.main(["summarize", "--year", "2026"]) == 0
    assert (data_dir / "summary" / "summary2026.csv").is_file()
    assert (data_dir / "summary" / "summary2026_01jan.csv").is_file()


def test_summarize_check_passes_on_a_fresh_summary(data_dir):
    assert cli.main(["summarize", "--year", "2026", "--check"]) == 0


def test_summarize_check_fails_on_a_stale_summary(data_dir, monkeypatch):
    cli.main(["summarize", "--year", "2026"])

    # summarize always rewrites the file, so corrupt the total after it is read back
    monkeypatch.setattr(cli, "check_summary_total", lambda **_: (100.0, 1.0, 99.0))
    assert cli.main(["summarize", "--year", "2026", "--check"]) == 1


def test_summarize_fail_on_uncategorized(data_dir, monkeypatch):
    assert cli.main(["summarize", "--year", "2026", "--fail-on-uncategorized"]) == 0

    monkeypatch.setattr(cli, "find_uncategorized", lambda **_: ["a row nothing claims"])
    assert cli.main(["summarize", "--year", "2026", "--fail-on-uncategorized"]) == 1


def test_summarize_fail_on_multi_category(data_dir, monkeypatch):
    monkeypatch.setattr(cli, "find_multi_category", lambda **_: [])
    assert cli.main(["summarize", "--year", "2026", "--fail-on-multi-category"]) == 0

    monkeypatch.setattr(cli, "find_multi_category", lambda **_: ["a row split two ways"])
    assert cli.main(["summarize", "--year", "2026", "--fail-on-multi-category"]) == 1
    assert cli.main(["summarize", "--year", "2026"]) == 0


def test_summarize_fails_on_a_changed_statement_total_until_accepted(data_dir):
    assert cli.main(["summarize", "--year", "2026"]) == 0
    assert (data_dir / "summary" / "statement_totals2026.csv").is_file()

    path = data_dir / "parsed" / "UOB01.csv"
    path.write_text(path.read_text().replace("     12.00    3", "     15.00    3"))
    assert cli.main(["summarize", "--year", "2026"]) == 1
    assert cli.main(["summarize", "--year", "2026", "--accept-totals"]) == 0
    assert cli.main(["summarize", "--year", "2026"]) == 0


def test_summarize_fails_when_a_statement_does_not_balance(data_dir):
    path = data_dir / "extracted" / "PLG01.csv"
    path.write_text(path.read_text().replace("20.30 CR", "25.30 CR"))
    assert cli.main(["summarize", "--year", "2026"]) == 1


def test_rules_regroup_keeps_cat_csv_loadable(data_dir):
    from trex.categorize.rules import CatRules

    cat_file = data_dir / "categories" / "cat.csv"
    before = len(CatRules(cat_file).load())
    assert cli.main(["rules", "regroup"]) == 0
    assert 0 < len(CatRules(cat_file).load()) <= before


def test_rules_sort_brands_succeeds(data_dir):
    assert cli.main(["rules", "sort-brands"]) == 0


def test_rules_suggest_brands_succeeds(data_dir):
    assert cli.main(["rules", "suggest-brands", "--min-keys", "2"]) == 0


def test_reconcile_with_no_edits_succeeds(data_dir):
    assert cli.main(["reconcile", "UOB01"]) == 0


def test_reconcile_of_a_missing_statement_reports_failure(data_dir):
    assert cli.main(["reconcile", "NEW01"]) == 1


def test_missing_statement_is_reported_not_raised(data_dir, capsys):
    assert cli.main(["parse", "NOPE99"]) == 1
    assert "error:" in capsys.readouterr().err


# --- parse --commit-balanced (--commit is in test_vcs.py) ----------


@pytest.fixture
def fake_parse(monkeypatch):
    """Parse without PDFs: statements named in `bad` fail the balance check.

    Returns the list that records each `_commit` call's (paths, message).
    """
    from types import SimpleNamespace

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


def test_commit_balanced_commits_only_the_balanced(data_dir, fake_parse):
    assert cli.main(["parse", "UOB06", "Chase06", "--commit-balanced"]) == 1
    [(paths, message)] = fake_parse
    assert {p.name for p in paths} == {"Chase06.csv"}
    assert message == "chore(data): parse Chase06"


def test_commit_balanced_with_nothing_balanced_commits_nothing(data_dir, fake_parse):
    assert cli.main(["parse", "UOB06", "--commit-balanced"]) == 1
    assert fake_parse == []


def test_commit_balanced_when_all_balance_commits_all(data_dir, fake_parse):
    assert cli.main(["parse", "Chase06", "PLG06", "--commit-balanced"]) == 0
    [(paths, message)] = fake_parse
    assert message == "chore(data): parse Chase06, PLG06"
