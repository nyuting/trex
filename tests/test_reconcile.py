"""The -update.csv workflow: teaching rules from hand edits, and regrouping."""

from __future__ import annotations

import pytest

from trex import reconcile
from trex.categorize.rules import CatRules


@pytest.fixture
def rules(data_dir) -> CatRules:
    return CatRules().load()


@pytest.fixture
def parsed(data_dir):
    return data_dir / "parsed"


def make_update(parsed, replacements: dict[str, str]) -> None:
    """Copy UOB01.csv to UOB01-update.csv, applying literal text replacements."""
    text = (parsed / "UOB01.csv").read_text()
    for old, new in replacements.items():
        assert old in text, f"fixture no longer contains {old!r}"
        text = text.replace(old, new)
    (parsed / "UOB01-update.csv").write_text(text)


# --- discovery ----------------------------------------------------------


def test_pending_updates_are_listed(data_dir, parsed):
    assert reconcile.list_pending_updates() == []
    (parsed / "UOB01-update.csv").write_text("")
    assert reconcile.list_pending_updates() == ["UOB01"]


def test_reconcile_without_an_update_file_does_nothing(data_dir, rules):
    assert reconcile.reconcile_update("UOB01", rules=rules) is None


def test_reconcile_without_an_original_does_nothing(data_dir, parsed, rules):
    (parsed / "NEW01-update.csv").write_text("")
    assert reconcile.reconcile_update("NEW01", rules=rules) is None


# --- guard rails --------------------------------------------------------


def test_a_changed_total_aborts_the_reconcile(data_dir, parsed, rules):
    make_update(parsed, {"18.40": "99.99"})
    assert reconcile.reconcile_update("UOB01", rules=rules) is None
    assert (parsed / "UOB01-update.csv").exists(), "the edit is kept for the user to fix"
    assert not rules.dirty


# --- teaching rules -----------------------------------------------------


def test_an_edited_category_is_appended_to_the_matching_rule(data_dir, parsed, rules):
    """Editing 2 -> 4 teaches the rule category 4; the original 2 is kept."""
    make_update(parsed, {"96.30    2    UOB-ONE": "96.30    4    UOB-ONE"})

    report = reconcile.reconcile_update("UOB01", rules=rules)

    assert report is not None
    assert ("GREENMART.*", 4) in report.appended
    assert sorted(CatRules().load().find("GREENMART SUPERMARKET").category_ids) == [2, 4]


def test_reconciling_consumes_the_update_file(data_dir, parsed, rules):
    make_update(parsed, {"96.30    2    UOB-ONE": "96.30    4    UOB-ONE"})
    reconcile.reconcile_update("UOB01", rules=rules)
    assert not (parsed / "UOB01-update.csv").exists()
    assert (parsed / "UOB01.csv").exists()


def test_untouched_rows_are_counted_not_rewritten(data_dir, parsed, rules):
    make_update(parsed, {})
    report = reconcile.reconcile_update("UOB01", rules=rules)
    assert report.unchanged > 0
    assert report.appended == []
    assert report.unmatched == []


def test_a_row_with_no_rule_is_reported_rather_than_guessed(data_dir, parsed, rules):
    """UNKNOWN MERCHANT XYZ was auto-filed; drop its rule so nothing matches it."""
    rules.rules = [r for r in rules.rules if "UNKNOWN" not in r.pattern]
    make_update(parsed, {"9.99    1    UOB-ONE": "9.99    7    UOB-ONE"})

    report = reconcile.reconcile_update("UOB01", rules=rules)

    assert [remark for remark, _, _ in report.unmatched] == ["UNKNOWN MERCHANT XYZ"]
    assert report.appended == []


# --- regrouping ---------------------------------------------------------


def test_regroup_moves_an_edited_row_into_its_new_section(data_dir, parsed):
    make_update(parsed, {"96.30    2    UOB-ONE": "96.30    7    UOB-ONE"})

    reconcile.regroup_update("UOB01")

    text = (parsed / "UOB01.csv").read_text()
    assert "7 HOME" in text
    assert text.index("7 HOME") < text.index("GREENMART SUPERMARKET")


def test_regroup_leaves_the_update_file_and_the_rules_alone(data_dir, parsed):
    before_rules = (data_dir / "categories" / "cat.csv").read_text()
    make_update(parsed, {"96.30    2    UOB-ONE": "96.30    7    UOB-ONE"})

    reconcile.regroup_update("UOB01")

    assert (parsed / "UOB01-update.csv").exists()
    assert (data_dir / "categories" / "cat.csv").read_text() == before_rules


def test_regroup_preserves_the_grand_total(data_dir, parsed):
    make_update(parsed, {"96.30    2    UOB-ONE": "96.30    7    UOB-ONE"})
    before = sum(row.cost for row in reconcile.read_parsed_csv(parsed / "UOB01.csv"))
    assert reconcile.regroup_update("UOB01") == pytest.approx(before)


def test_regroup_without_an_update_file_is_a_no_op(data_dir):
    assert reconcile.regroup_update("UOB01") == 0.0


# --- round trip ---------------------------------------------------------


def test_a_parsed_file_survives_being_read_and_written(data_dir, parsed, tmp_path):
    from trex.serialize import write_parsed_csv

    golden = parsed / "UOB01.csv"
    rewritten = tmp_path / "UOB01.csv"
    write_parsed_csv(reconcile.read_parsed_statement(golden), rewritten)
    assert rewritten.read_text() == golden.read_text()


def test_credits_survive_the_round_trip(data_dir, parsed):
    statement = reconcile.read_parsed_statement(parsed / "UOB01.csv")
    assert [c.remark for c in statement.credits] == ["PAYMT THRU E-BANK/HOMEB/CYBERB"]
    assert statement.credits[0].cost == pytest.approx(1000.0)
