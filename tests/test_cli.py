"""CLI wiring: every subcommand reaches its library function and reports an exit code.

These exercise argument handling and dispatch; the behaviour behind each command
is covered by the module tests.
"""

from __future__ import annotations

import pytest

from trex import cli


def run(argv, data_dir):
    """Run the CLI against the fixture data tree and return its exit code."""
    return cli.main(argv)


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
        ["regroup", "--help"],
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
    assert run(["summarize", "--year", "2026"], data_dir) == 0
    assert (data_dir / "parsed" / "summary2026.csv").is_file()


def test_summarize_check_passes_on_a_fresh_summary(data_dir):
    assert run(["summarize", "--year", "2026", "--check"], data_dir) == 0


def test_summarize_check_fails_on_a_stale_summary(data_dir, monkeypatch):
    run(["summarize", "--year", "2026"], data_dir)

    # summarize always rewrites the file, so corrupt the total after it is read back
    from trex import cli

    monkeypatch.setattr(cli, "check_summary_total", lambda **_: (100.0, 1.0, 99.0))
    assert run(["summarize", "--year", "2026", "--check"], data_dir) == 1


def test_summarize_fail_on_uncategorized(data_dir, monkeypatch):
    from trex import cli

    assert run(["summarize", "--year", "2026", "--fail-on-uncategorized"], data_dir) == 0

    monkeypatch.setattr(cli, "find_uncategorized", lambda **_: ["a row nothing claims"])
    assert run(["summarize", "--year", "2026", "--fail-on-uncategorized"], data_dir) == 1


def test_rules_pending_reports_nothing_when_clean(data_dir):
    assert run(["rules", "pending"], data_dir) == 0


def test_rules_pending_lists_awaiting_statements(data_dir, capsys):
    (data_dir / "parsed" / "UOB01-update.csv").write_text("")
    assert run(["rules", "pending"], data_dir) == 0
    assert "UOB01" in capsys.readouterr().err


def test_rules_regroup_keeps_cat_csv_loadable(data_dir):
    from trex.categorize.rules import CatRules

    cat_file = data_dir / "categories" / "cat.csv"
    before = len(CatRules(cat_file).load())
    assert run(["rules", "regroup"], data_dir) == 0
    assert 0 < len(CatRules(cat_file).load()) <= before


def test_rules_sort_brands_succeeds(data_dir):
    assert run(["rules", "sort-brands"], data_dir) == 0


def test_rules_suggest_brands_succeeds(data_dir):
    assert run(["rules", "suggest-brands", "--min-keys", "2"], data_dir) == 0


def test_reconcile_without_an_update_file_reports_failure(data_dir):
    assert run(["reconcile", "UOB01"], data_dir) == 1


def test_regroup_without_an_update_file_is_not_fatal(data_dir):
    assert run(["regroup", "UOB01"], data_dir) == 0


def test_missing_statement_is_reported_not_raised(data_dir, capsys):
    assert run(["parse", "NOPE99"], data_dir) == 1
    assert "error:" in capsys.readouterr().err
