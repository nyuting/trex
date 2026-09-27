"""Rule loading, matching, and the side effects that make cat.csv grow."""

from __future__ import annotations

import csv
import os
import subprocess
import sys

import pytest

from trex.categorize import brands, regroup
from trex.categorize.rules import CatRules

UOB_ONE = "UOB-ONE"  # card id 6
UOB_VISA = "UOB-VISA"  # card id 5
CHASE = "CHASE"  # card id 1


# --- loading ------------------------------------------------------------


def test_importing_the_package_reads_no_files(tmp_path):
    """Regression: rules used to load at import time, so a bad cwd crashed the import.

    Runs in a fresh interpreter: this one imported trex.categorize long ago.
    """
    env = {**os.environ, "TREX_DATA_DIR": str(tmp_path / "does-not-exist")}
    subprocess.run(
        [sys.executable, "-c", "import trex.categorize, trex.cli"],
        cwd=tmp_path,
        env=env,
        check=True,
    )
    assert CatRules(tmp_path / "nothing.csv").rules == []


def test_load_reads_every_well_formed_rule(rules):
    assert len(rules.rules) == 5
    assert not rules.dirty


def test_malformed_rules_are_skipped_not_fatal(data_dir):
    path = data_dir / "categories" / "cat.csv"
    with open(path, "a", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["unclosed [group", "1", "6"])  # bad regex
        writer.writerow(["no categories", "-", "6"])
        writer.writerow(["too", "many", "columns", "here"])

    assert len(CatRules(path).load().rules) == 5


def test_personal_rules_are_loaded_alongside_cat_csv(rules):
    assert rules.classify_remark(UOB_ONE, 50.00, "SEND EGIFT 90000001") == 10
    assert len(rules) == 6, "cat.csv plus cat_personal.csv"


def test_a_missing_personal_file_is_not_an_error(data_dir):
    (data_dir / "categories" / "cat_personal.csv").unlink()
    assert len(CatRules().load().rules) == 5


def test_writing_cat_csv_leaves_personal_rules_where_they_are(rules):
    personal = rules.path.parent / "cat_personal.csv"
    before = personal.read_text()

    rules.classify_remark(UOB_ONE, 5.00, "ZZZ LATE NIGHT SUPPER")
    assert rules.save_if_changed() is True

    assert personal.read_text() == before
    assert "SEND EGIFT" not in rules.path.read_text()


# --- matching -----------------------------------------------------------


def test_single_category_rule_returns_bare_id(rules):
    assert rules.classify_remark(UOB_ONE, 18.40, "ACME COFFEE HOUSE") == 1


def test_multi_category_rule_returns_tuple(rules):
    assert rules.classify_remark(UOB_ONE, 63.75, "SUNDRY SHOP 88") == (2, 7)


def test_regex_rule_matches_by_prefix(rules):
    assert rules.classify_remark(UOB_ONE, 96.30, "GREENMART SUPERMARKET BISHAN") == 2


def test_unknown_card_is_never_categorized(rules):
    assert rules.classify_remark("NOT-A-CARD", 10.0, "ACME COFFEE HOUSE") is None


# --- side effects -------------------------------------------------------


def test_matching_on_a_new_card_records_that_card(rules):
    rules.classify_remark(UOB_VISA, 18.40, "ACME COFFEE HOUSE")
    assert rules.find("ACME COFFEE HOUSE").card_ids == {5, 6}
    assert rules.dirty


def test_matching_on_a_known_card_leaves_rules_clean(rules):
    rules.classify_remark(UOB_ONE, 18.40, "ACME COFFEE HOUSE")
    assert not rules.dirty


def test_unmatched_small_purchase_becomes_a_dining_rule(rules):
    assert rules.classify_remark(UOB_ONE, 12.00, "NEW HAWKER STALL") == 1
    assert len(rules.rules) == 6
    assert rules.dirty
    assert rules.classify_remark(UOB_ONE, 12.00, "NEW HAWKER STALL") == 1
    assert len(rules.rules) == 6, "the auto-added rule must be reused, not duplicated"


def test_unmatched_large_purchase_stays_uncategorized(rules):
    assert rules.classify_remark(UOB_ONE, 500.00, "MYSTERY MERCHANT") is None
    assert len(rules.rules) == 5
    assert not rules.dirty


def test_unmatched_chase_spending_is_holiday_without_adding_a_rule(rules):
    assert rules.classify_remark(CHASE, 500.00, "SOME HOTEL") == 9
    assert len(rules.rules) == 5
    assert not rules.dirty


# --- persistence --------------------------------------------------------


def test_flush_is_a_no_op_when_nothing_changed(rules):
    before = rules.path.read_text()
    assert rules.save_if_changed() is False
    assert rules.path.read_text() == before


def test_flush_writes_sorted_rules_and_clears_dirty(rules):
    rules.classify_remark(UOB_ONE, 5.00, "ZZZ LATE NIGHT SUPPER")
    assert rules.save_if_changed() is True
    assert not rules.dirty

    reloaded = CatRules(rules.path).load()
    assert len(reloaded.rules) == 6
    categories = [rule.category_ids[0] for rule in reloaded.rules]
    assert categories == sorted(categories), "rules are grouped by category on disk"


# --- brands and regroup -------------------------------------------------


def test_brand_match_beats_remark_trimming(data_dir):
    matcher = brands.BrandMatcher()
    assert matcher.normalize_remark_key("ACME COFFEE HOUSE - ORCHARD") == "BRAND:Acme"
    assert matcher.normalize_remark_key("LOCAL STALL - TAMPINES") == "LOCAL STALL"


@pytest.mark.parametrize(
    "remark, expected",
    [
        ("PAYNOW - J O H N ... 1234", "PAYNOW"),
        ("SEND MONEY TO JANE", "SEND MONEY TO JANE"),
        ("BOOKSHOP Singapore", "BOOKSHOP"),
        ("12-XY (Corner Cafe)", "(Corner Cafe)"),
        ("KOPI STALL-04", "KOPI STALL"),
        ("PAYNOW TO LU HUI97124705", "PAYNOW TO LU HUI"),
        ("PAYNOW TO LU HUI 97124705", "PAYNOW TO LU HUI"),
        ("KOPI STALL 26030203213158950071", "KOPI STALL"),
    ],
)
def test_remark_keys_strip_the_varying_tail(data_dir, remark, expected):
    assert brands.BrandMatcher().normalize_remark_key(remark) == expected


def test_escape_and_unescape_are_inverses():
    literal = "CAFE (BISHAN) $5.00 [A]"
    assert brands.unescape_literal(brands.escape_literal(literal)) == literal
    assert brands.unescape_literal(".*ACME.*") is None


def test_manually_escaped_spaces_still_read_as_literal_text():
    """Manually written rules escape spaces; treating those as regexes stopped them grouping."""
    assert brands.unescape_literal(r"ACME\ COFFEE\ HOUSE") == "ACME COFFEE HOUSE"
    assert brands.unescape_literal(r"ACME\ \-\ BISHAN") == "ACME - BISHAN"
    assert brands.pick_representative_remark(r"ACME\ COFFEE.*") == "ACME COFFEE"


def test_regroup_merges_rules_for_the_same_merchant(data_dir):
    path = data_dir / "categories" / "cat.csv"
    with open(path, "a", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["ACME COFFEE HOUSE ORCHARD", "1", "5"])
        writer.writerow(["ACME COFFEE HOUSE BISHAN", "1", "6"])

    assert regroup.regroup_cat_file(path) < 7

    merged = CatRules(path).load()
    assert merged.find("ACME COFFEE HOUSE ORCHARD") is not None
    assert merged.find("ACME COFFEE HOUSE BISHAN") is not None
    assert merged.find("ACME COFFEE HOUSE") is not None


def test_regrouped_rules_still_match_everything_they_did_before(data_dir):
    path = data_dir / "categories" / "cat.csv"
    remarks = ["ACME COFFEE HOUSE", "GREENMART SUPERMARKET", "CITY RAIL TOP UP", "SUNDRY SHOP 88"]
    before = {r: CatRules(path).load().find(r).category_ids for r in remarks}

    regroup.regroup_cat_file(path)

    after = CatRules(path).load()
    assert {r: after.find(r).category_ids for r in remarks} == before


def test_regroup_merges_rules_differing_only_by_a_reference_number(data_dir):
    path = data_dir / "categories" / "cat.csv"
    with open(path, "a", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow([r"PAYNOW\ TO\ LU\ HUI97124705", "1", "3"])
        writer.writerow([r"PAYNOW\ TO\ LU\ HUI\ 97124705", "1", "3"])

    regroup.regroup_cat_file(path)

    merged = CatRules(path).load()
    assert merged.find("PAYNOW TO LU HUI97124705") is not None
    assert merged.find("PAYNOW TO LU HUI 97124705") is not None
    assert sum("LU HUI" in rule.pattern for rule in merged.rules) == 1


def test_regroup_leaves_a_group_with_no_common_prefix_unfused(data_dir):
    """Regression: `J-?? JAPANESE FOOD M??` was once fused into a bare `J-.* Singapore`."""
    path = data_dir / "categories" / "cat.csv"
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["regex", "category", "card"])
        writer.writerow([r"J\-11 JAPANESE FOOD Singapore", "1", "6"])
        writer.writerow([r"J\-22 RAMEN BAR Singapore", "1", "6"])

    assert regroup.regroup_cat_file(path) == 2

    after = CatRules(path).load()
    assert all(not rule.matches("J-33 SOMETHING ELSE Singapore") for rule in after.rules)


def test_a_shared_brand_does_not_license_a_one_letter_prefix(data_dir):
    """Regression: `Acme Coffee` + `ACME COFFEE` share only `A`, and fused to `A.*`."""
    path = data_dir / "categories" / "cat.csv"
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["regex", "category", "card"])
        writer.writerow(["Acme Coffee 65-31051486", "1", "6"])
        writer.writerow(["ACME COFFEE HOUSE", "1", "6"])

    assert regroup.regroup_cat_file(path) == 2

    after = CatRules(path).load()
    assert all(not rule.matches("ANOTHER MERCHANT") for rule in after.rules)


def test_sort_brands_orders_by_normalized_name(data_dir):
    path = data_dir / "categories" / "brands.csv"
    path.write_text("brand\nZeta\nacme\nMid Point\n")
    assert regroup.sort_brands_file(path) == 3
    assert brands.read_brands(path) == ["acme", "Mid Point", "Zeta"]


def test_suggest_brands_surfaces_repeated_tokens(data_dir):
    path = data_dir / "categories" / "cat.csv"
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["regex", "category", "card"])
        for branch in ("alpha", "beta", "gamma"):
            writer.writerow([f"KOPITIAM {branch}", "1", "6"])

    suggestions = regroup.suggest_brands(min_keys=3, cat_path=path)
    assert "KOPITIAM" in {token for _, token, _ in suggestions}
