"""CLI wiring: every subcommand reaches its library function and reports an exit code.

These exercise argument handling and dispatch; the behaviour behind each command
is covered by the module tests.
"""

from __future__ import annotations

import pytest

from trex import cli, summary


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
        ["rules", "stats", "--help"],
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


# the fixture's UOB01 has a multi-category row, which summarize now fails on by default
SUMMARIZE = ["summarize", "--year", "2026", "--no-fail-on-multi-category"]


def test_summarize_writes_a_summary(data_dir):
    assert cli.main(SUMMARIZE) == 0
    assert (data_dir / "summary" / "summary2026.csv").is_file()
    assert (data_dir / "summary" / "summary2026_01jan.csv").is_file()


def test_summarize_check_fails_on_a_stale_summary(data_dir, monkeypatch):
    cli.main(SUMMARIZE)

    # summarize always rewrites the file, so corrupt the total after it is read back
    monkeypatch.setattr(cli, "check_year_summary_total", lambda **_: (100.0, 1.0, 99.0))
    assert cli.main(SUMMARIZE) == 1
    assert cli.main([*SUMMARIZE, "--no-check"]) == 0


def test_summarize_fail_on_uncategorized(data_dir, monkeypatch):
    built = summary.YearSummary(2026)
    built.uncategorized.rows = 1
    monkeypatch.setattr(cli, "build_year_summary", lambda *_: built)
    assert cli.main(SUMMARIZE) == 1
    assert cli.main([*SUMMARIZE, "--no-fail-on-uncategorized"]) == 0


def test_summarize_fail_on_multi_category(data_dir, monkeypatch):
    built = summary.YearSummary(2026)
    monkeypatch.setattr(cli, "build_year_summary", lambda *_: built)
    assert cli.main(["summarize", "--year", "2026"]) == 0

    built.multi_category.rows = 1
    assert cli.main(["summarize", "--year", "2026"]) == 1
    assert cli.main(["summarize", "--year", "2026", "--no-fail-on-multi-category"]) == 0


def test_summarize_fails_on_a_changed_statement_total_until_accepted(data_dir):
    assert cli.main(SUMMARIZE) == 0
    assert (data_dir / "summary" / "statement_totals2026.csv").is_file()

    path = data_dir / "parsed" / "UOB01.csv"
    path.write_text(path.read_text().replace("     12.00    3", "     15.00    3"))
    assert cli.main(SUMMARIZE) == 1
    assert cli.main([*SUMMARIZE, "--accept-totals"]) == 0
    assert cli.main(SUMMARIZE) == 0


def test_summarize_fails_when_a_statement_does_not_balance(data_dir):
    path = data_dir / "extracted" / "PLG01.csv"
    path.write_text(path.read_text().replace("20.30 CR", "25.30 CR"))
    assert cli.main(SUMMARIZE) == 1


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


@pytest.mark.parametrize("flags", [[], ["--sort", "count"], ["--sort", "pattern"]])
def test_rules_stats_succeeds(data_dir, flags):
    assert cli.main(["rules", "stats", *flags]) == 0


def test_rules_stats_unused_still_writes_every_rule(data_dir):
    assert cli.main(["rules", "stats", "--unused"]) == 0
    lines = (data_dir / "categories" / "cat_stats.csv").read_text().splitlines()
    assert len(lines) == 1 + 6  # header, then the fixture's cat.csv and cat_personal.csv rules


def test_reconcile_with_no_edits_succeeds(data_dir):
    assert cli.main(["reconcile", "UOB01"]) == 0


def test_reconcile_of_a_missing_statement_reports_failure(data_dir):
    assert cli.main(["reconcile", "NEW01"]) == 1


def test_missing_statement_is_reported_not_raised(data_dir, capsys):
    assert cli.main(["parse", "NOPE99"]) == 1
    assert "error:" in capsys.readouterr().err
