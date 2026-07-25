"""Row readers and whole-file parsing, driven by fixture extracted CSVs."""

from __future__ import annotations

from datetime import datetime

import pytest

from trex.categorize.rules import CatRules
from trex.parse import files, rows

JAN_31 = datetime(2026, 1, 31)


# --- issuer detection ---------------------------------------------------


@pytest.mark.parametrize(
    "name, issuer",
    [
        ("Chase01", "Chase"),
        ("CHASE01", "Chase"),
        ("PLG05", "Paylah"),
        ("PLY0405", "Paylah"),
        ("UOB05", "UOB"),
    ],
)
def test_issuer_is_detected_from_the_short_name(name, issuer):
    assert rows.detect_issuer(name) == issuer


def test_unknown_short_name_is_rejected():
    with pytest.raises(ValueError, match="unknown statement source"):
        rows.detect_issuer("Barclays01")


# --- costs --------------------------------------------------------------


@pytest.mark.parametrize(
    "cell, expected",
    [
        ("12.34", (12.34, False)),
        ("1,234.56", (1234.56, False)),
        ("50.00CR", (50.0, True)),
        ("-12.00", (-12.0, False)),
    ],
)
def test_costs_are_read_with_their_credit_flag(cell, expected):
    assert rows.parse_cost(cell) == expected


def test_an_unparsable_cost_raises():
    with pytest.raises(ValueError):
        rows.parse_cost("not money")


# --- year inference -----------------------------------------------------


def test_a_transaction_before_the_statement_date_keeps_that_year():
    assert rows.adjust_year(datetime(1900, 1, 5), JAN_31) == datetime(2026, 1, 5)


def test_a_december_transaction_on_a_january_statement_rolls_back_a_year():
    """The rollover case: statement rows carry no year of their own."""
    assert rows.adjust_year(datetime(1900, 12, 28), JAN_31) == datetime(2025, 12, 28)


def test_a_transaction_on_the_statement_date_itself_is_not_rolled_back():
    assert rows.adjust_year(datetime(1900, 1, 31), JAN_31) == JAN_31


def test_without_a_statement_date_the_current_year_is_assumed():
    assert rows.adjust_year(datetime(1900, 6, 1), None).year == datetime.now().year


# --- statement dates ----------------------------------------------------


@pytest.mark.parametrize(
    "first_row, issuer, expected",
    [
        (["Statement Date", "31 JAN 2026"], "UOB", datetime(2026, 1, 31)),
        (["12/15/25 - 01/14/26"], "Chase", datetime(2026, 1, 14)),
        (["31 Jan 2026"], "Paylah", datetime(2026, 1, 31)),
    ],
)
def test_each_issuer_states_its_statement_date_differently(first_row, issuer, expected):
    assert rows.parse_statement_date(first_row, issuer) == expected


@pytest.mark.parametrize(
    "first_row, issuer",
    [(None, "UOB"), ([], "UOB"), (["not a date"], "Paylah"), (["01/03,ACME,5.00"], "Chase")],
)
def test_a_missing_statement_date_is_none_not_an_error(first_row, issuer):
    assert rows.parse_statement_date(first_row, issuer) is None


# --- per-issuer rows ----------------------------------------------------


def test_a_chase_row_reads_date_merchant_and_cost():
    row = rows.parse_chase_row(["01/03", "ACME COFFEE", "18.40"], JAN_31)
    assert (row.date, row.cost, row.remark, row.is_credit) == (
        datetime(2026, 1, 3),
        18.40,
        "ACME COFFEE",
        False,
    )


def test_a_paylah_row_reads_its_credit_indicator():
    debit = rows.parse_paylah_row(["03 Jan", "ACME", "18.40", "DB"], JAN_31)
    credit = rows.parse_paylah_row(["03 Jan", "REFUND", "18.40", "CR"], JAN_31)
    assert not debit.is_credit
    assert credit.is_credit


def test_a_uob_row_uses_the_transaction_date_not_the_posting_date():
    row = rows.parse_uob_row(["05 Jan", "03 Jan", "ACME", "18.40"], JAN_31)
    assert row.date == datetime(2026, 1, 3)


@pytest.mark.parametrize(
    "reader, fields",
    [
        (rows.parse_chase_row, ["01/03", "ACME"]),
        (rows.parse_chase_row, ["nope", "ACME", "18.40"]),
        (rows.parse_paylah_row, ["03 Jan", "ACME", "18.40"]),
        (rows.parse_uob_row, ["05 Jan", "03 Jan", "ACME", "not money"]),
    ],
)
def test_structural_and_malformed_rows_return_none(reader, fields):
    assert reader(fields, JAN_31) is None


# --- whole files --------------------------------------------------------


@pytest.fixture
def rules(data_dir) -> CatRules:
    return CatRules().load()


def test_uob_transactions_are_attributed_to_their_card_section(data_dir, rules):
    statement = files.parse_extracted_csv("UOB01", rules=rules)
    assert statement.statement_date == datetime(2026, 1, 31)
    assert {t.source for t in statement.expenses} == {"UOB-ONE", "UOB-VISA"}
    assert [t.remark for t in statement.expenses if t.source == "UOB-VISA"] == ["CITY RAIL TOP UP"]


def test_uob_rows_before_any_card_section_are_ignored(data_dir, rules):
    statement = files.parse_extracted_csv("UOB01", rules=rules)
    assert all(t.source is not None for t in statement.expenses)


def test_uob_credits_are_split_from_expenses(data_dir, rules):
    statement = files.parse_extracted_csv("UOB01", rules=rules)
    assert [c.remark for c in statement.credits] == ["PAYMT THRU E-BANK/HOMEB/CYBERB"]
    assert [c.kind for c in statement.credits] == ["payment"]
    assert "PAYMT" not in " ".join(t.remark for t in statement.expenses)


def test_parsing_applies_the_rule_set(data_dir, rules):
    statement = files.parse_extracted_csv("UOB01", rules=rules)
    by_remark = {t.remark: t.category for t in statement.expenses}
    assert by_remark["ACME COFFEE HOUSE"] == 1
    assert by_remark["CITY RAIL TOP UP"] == 3


def test_parsing_without_rules_leaves_everything_uncategorized(data_dir):
    statement = files.parse_extracted_csv("UOB01", rules=None)
    assert all(t.is_uncategorized for t in statement.expenses)


def test_paylah_wallet_topups_roll_up_into_one_payment(data_dir, rules):
    statement = files.parse_extracted_csv("PLG01", rules=rules)
    payments = [c for c in statement.credits if c.kind == "payment"]
    assert len(payments) == 1
    assert "(2x)" in payments[0].remark
    assert payments[0].cost == pytest.approx(30.0)


def test_paylah_refunds_stay_separate_from_topups(data_dir, rules):
    statement = files.parse_extracted_csv("PLG01", rules=rules)
    assert [c.remark for c in statement.credits if c.kind == "refund"] == ["REFUND FROM SHOP"]


def test_a_missing_extracted_csv_is_reported():
    with pytest.raises(FileNotFoundError, match="no extracted CSV"):
        files.parse_extracted_csv("NOPE99")
