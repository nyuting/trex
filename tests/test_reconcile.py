"""Reconcile: turning manual edits in a parsed CSV into rules, and re-parsing it."""

from __future__ import annotations

import pytest

from trex import reconcile
from trex.categorize.rules import CatRules
from trex.models import RewardsBalance
from trex.serialize import read_parsed_expenses


@pytest.fixture
def parsed(data_dir):
    return data_dir / "parsed"


def edit_parsed(parsed, replacements: dict[str, str]) -> None:
    """Apply literal text replacements to UOB01.csv itself."""
    path = parsed / "UOB01.csv"
    text = path.read_text()
    for old, new in replacements.items():
        assert old in text, f"fixture no longer contains {old!r}"
        text = text.replace(old, new)
    path.write_text(text)


def categories_by_cost(path) -> dict[float, list[int]]:
    """Return each expense row's category ids, keyed by its cost."""
    return {row.cost: row.category_ids for row in read_parsed_expenses(path)}


def test_in_place_with_no_edits_leaves_the_file_as_it_was(data_dir, parsed, rules):
    before = (parsed / "UOB01.csv").read_bytes()

    report = reconcile.reconcile_in_place("UOB01", rules=rules)

    assert report is not None and report.unchanged > 0
    assert report.changed == report.added == []
    assert (parsed / "UOB01.csv").read_bytes() == before


def test_in_place_reparse_moves_unedited_rows_the_changed_rule_matches(data_dir, parsed, rules):
    """Two ACME rows: editing one to GANSHUN re-files the other as well."""
    edit_parsed(parsed, {"18.40    1    UOB-ONE": "18.40    5    UOB-ONE"})

    reconcile.reconcile_in_place("UOB01", rules=rules)

    categories = categories_by_cost(parsed / "UOB01.csv")
    assert categories[18.40] == [5]
    assert categories[42.15] == [5]


def test_an_edit_the_rules_cannot_hold_survives_the_reparse(data_dir, parsed, rules):
    for path in (parsed / "UOB01.csv", data_dir / "extracted" / "UOB01.csv"):
        path.write_text(path.read_text().replace("UNKNOWN MERCHANT XYZ", "SEND MONEY TO 91234567"))
    rules.rules = [r for r in rules.rules if "UNKNOWN" not in r.pattern]
    edit_parsed(parsed, {"9.99    1    UOB-ONE": "9.99    7    UOB-ONE"})

    report = reconcile.reconcile_in_place("UOB01", rules=rules)

    assert report.unmatched == [("SEND MONEY TO 91234567", "UOB-ONE", [7])]
    categories = categories_by_cost(parsed / "UOB01.csv")
    assert categories[9.99] == [7]


def test_a_missing_parsed_file_is_skipped(data_dir, rules):
    assert reconcile.reconcile_in_place("NEW01", rules=rules) is None


def test_editing_a_multi_category_row_replaces_only_the_removed_category(data_dir, parsed, rules):
    """SUNDRY SHOP 88 is filed under 2,7; editing it to 4,7 keeps the 7."""
    edit_parsed(parsed, {"63.75  2,7    UOB-ONE": "63.75  4,7    UOB-ONE"})

    reconcile.reconcile_in_place("UOB01", rules=rules)

    assert CatRules().load().find("SUNDRY SHOP 88").category_ids == [7, 4]


def test_a_row_with_no_rule_gets_a_literal_rule(data_dir, parsed, rules):
    """UNKNOWN MERCHANT XYZ was auto-filed; drop its rule so nothing matches it."""
    rules.rules = [r for r in rules.rules if "UNKNOWN" not in r.pattern]
    edit_parsed(parsed, {"9.99    1    UOB-ONE": "9.99    7    UOB-ONE"})

    report = reconcile.reconcile_in_place("UOB01", rules=rules)

    assert report.added == [(r"UNKNOWN\ MERCHANT\ XYZ", [7])]
    rule = CatRules().load().find("UNKNOWN MERCHANT XYZ")
    assert rule.category_ids == [7]
    assert rule.card_ids == {6}


def test_in_place_updates_the_rule_and_regroups_the_file(data_dir, parsed, rules):
    edit_parsed(parsed, {"96.30    2    UOB-ONE": "96.30    4    UOB-ONE"})

    report = reconcile.reconcile_in_place("UOB01", rules=rules)

    assert report.changed == [("GREENMART.*", [2], [4])]
    assert CatRules().load().find("GREENMART SUPERMARKET").category_ids == [4]
    text = (parsed / "UOB01.csv").read_text()
    assert text.index("GREENMART") > text.index("KIDS"), "moved into the KIDS section"


def test_narrowing_a_multi_category_row_leaves_the_rule_alone(data_dir, parsed, rules):
    """SUNDRY SHOP 88 is 2,7; picking 7 for this purchase says nothing about the next."""
    edit_parsed(parsed, {"63.75  2,7    UOB-ONE": "63.75    7    UOB-ONE"})

    report = reconcile.reconcile_in_place("UOB01", rules=rules)

    assert report.narrowed == 1
    assert report.changed == []
    assert CatRules().load().find("SUNDRY SHOP 88").category_ids == [2, 7]
    categories = categories_by_cost(parsed / "UOB01.csv")
    assert categories[63.75] == [7], "the row keeps the choice"


def test_rows_that_disagree_make_the_rule_multi_category(data_dir, parsed, rules):
    edit_parsed(
        parsed,
        {
            "18.40    1    UOB-ONE": "18.40    5    UOB-ONE",
            "42.15    1    UOB-ONE": "42.15    8    UOB-ONE",
        },
    )

    report = reconcile.reconcile_in_place("UOB01", rules=rules)

    assert report.changed == [("ACME COFFEE HOUSE", [1], [5, 8])]


def test_moving_a_multi_category_row_somewhere_new_adds_the_category(data_dir, parsed, rules):
    """SUNDRY SHOP 88 is 2,7; one purchase filed as 4 widens the rule, it doesn't replace it."""
    edit_parsed(parsed, {"63.75  2,7    UOB-ONE": "63.75    4    UOB-ONE"})

    report = reconcile.reconcile_in_place("UOB01", rules=rules)

    assert report.changed == [("SUNDRY SHOP 88", [2, 7], [2, 7, 4])]


def test_a_batch_judges_every_statement_against_the_rules_it_started_with(data_dir, parsed, rules):
    """UOB02 is untouched, so the ACME fix in UOB01 isn't undone by it; it follows."""
    for directory in (parsed, data_dir / "extracted"):
        (directory / "UOB02.csv").write_text((directory / "UOB01.csv").read_text())
    edit_parsed(parsed, {"18.40    1    UOB-ONE": "18.40    5    UOB-ONE"})

    report = reconcile.reconcile_in_place(["UOB01", "UOB02"], rules=rules)

    assert report.changed == [("ACME COFFEE HOUSE", [1], [5])]
    categories = categories_by_cost(parsed / "UOB02.csv")
    assert categories[18.40] == categories[42.15] == [5]


def test_a_batch_skips_a_statement_whose_total_moved(data_dir, parsed, rules):
    (parsed / "UOB02.csv").write_text((parsed / "UOB01.csv").read_text().replace("18.40", "99.99"))
    (data_dir / "extracted" / "UOB02.csv").write_text(
        (data_dir / "extracted" / "UOB01.csv").read_text()
    )

    report = reconcile.reconcile_in_place(["UOB01", "UOB02"], rules=rules)

    assert report.skipped == ["UOB02"]


def test_in_place_aborts_on_a_changed_total(data_dir, parsed, rules):
    edit_parsed(parsed, {"18.40": "99.99"})
    before = (parsed / "UOB01.csv").read_text()

    assert reconcile.reconcile_in_place("UOB01", rules=rules) is None
    assert (parsed / "UOB01.csv").read_text() == before
    assert not rules.dirty


# --- reading back ------------------------------------------------------


def test_credits_survive_the_round_trip(data_dir, parsed):
    statement = reconcile.read_parsed_statement(parsed / "UOB01.csv")
    assert [c.remark for c in statement.credits] == ["PAYMT THRU E-BANK/HOMEB/CYBERB"]
    assert statement.credits[0].cost == pytest.approx(1000.0)


def test_uni_figures_survive_the_round_trip(data_dir, parsed):
    statement = reconcile.read_parsed_statement(parsed / "UOB01.csv")
    assert statement.rewards == RewardsBalance(12000.0, 250.0, 0.0, -10.0, 12240.0)
