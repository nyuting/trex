"""Row readers and whole-file parsing, driven by fixture extracted CSVs."""

from __future__ import annotations

from datetime import datetime

import pytest

from trex.constants import ISSUER_BY_PREFIX
from trex.extract import EXTRACTORS
from trex.models import CardBalance, Credit, RewardsBalance, Statement, Transaction, to_cents
from trex.parse import balance, carry_over_manual_categories, files, rows
from trex.serialize import write_parsed_csv

JAN_31 = datetime(2026, 1, 31)


# --- issuer registry ---------------------------------------------------


@pytest.mark.parametrize("issuer", sorted(set(ISSUER_BY_PREFIX.values())))
def test_every_detectable_issuer_has_an_extractor_and_a_parser(issuer):
    assert issuer in EXTRACTORS
    assert issuer in files.PARSERS


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


@pytest.mark.parametrize(
    "cell, expected",
    [("1,234.56", 1234.56), ("12.34-", -12.34), ("0.00", 0.0), (" 5.00 ", 5.0)],
)
def test_points_are_read_with_a_trailing_minus_as_negative(cell, expected):
    assert rows.parse_points(cell) == expected


def test_unparsable_points_raise():
    with pytest.raises(ValueError):
        rows.parse_points("NA")


# --- cents --------------------------------------------------------------


def test_to_cents_absorbs_float_error():
    assert to_cents(0.1 + 0.2) == 30
    assert to_cents(sum([0.10] * 100)) == 1000
    assert to_cents(-12.345000001) == -1235
    assert to_cents(None) is None


def test_many_small_rows_balance_to_the_exact_cent(data_dir):
    write_extracted(
        data_dir,
        "UOB09",
        "UOB ONE CARD",
        '"",,PREVIOUS BALANCE,0.10',
        *["04 JAN,03 JAN,ACME COFFEE HOUSE,0.10"] * 100,
        "TOTAL BALANCE FOR UOB ONE CARD,10.10",
    )
    assert balance.check_card_totals(files.parse_extracted_csv("UOB09")) == []


# --- year inference -----------------------------------------------------


def test_a_transaction_before_the_statement_date_keeps_that_year():
    assert rows.date_within_statement_year(datetime(1900, 1, 5), JAN_31) == datetime(2026, 1, 5)


def test_a_december_transaction_on_a_january_statement_rolls_back_a_year():
    """The rollover case: statement rows carry no year of their own."""
    assert rows.date_within_statement_year(datetime(1900, 12, 28), JAN_31) == datetime(2025, 12, 28)


def test_a_transaction_on_the_statement_date_itself_is_not_rolled_back():
    assert rows.date_within_statement_year(datetime(1900, 1, 31), JAN_31) == JAN_31


def test_without_a_statement_date_the_current_year_is_assumed():
    assert rows.date_within_statement_year(datetime(1900, 6, 1), None).year == datetime.now().year


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


def test_a_chase_row_reads_a_cost_with_thousands_separators():
    row = rows.parse_chase_row(["01/03", "ACME AIR", "1,088.23"], JAN_31)
    assert row.cost == 1088.23


def test_a_negative_chase_row_is_a_credit():
    row = rows.parse_chase_row(["01/05", "HOTEL CREDIT", "-250.00"], JAN_31)
    assert (row.cost, row.is_credit) == (250.00, True)


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
        (rows.parse_chase_row, ["02/29", "ACME", "18.40"]),
        (rows.parse_paylah_row, ["29 Feb", "ACME", "18.40", "DB"]),
        (rows.parse_uob_row, ["02 Mar", "29 Feb", "ACME", "18.40"]),
    ],
)
def test_a_leap_day_row_is_read_in_a_leap_year(reader, fields):
    assert reader(fields, datetime(2028, 3, 14)).date == datetime(2028, 2, 29)


def test_a_leap_day_row_in_a_non_leap_year_returns_none():
    assert rows.parse_chase_row(["02/29", "ACME", "18.40"], datetime(2027, 3, 14)) is None


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


def test_uob_transactions_are_attributed_to_their_card_section(data_dir, rules):
    statement = files.parse_extracted_csv("UOB01", rules=rules)
    assert statement.statement_date == datetime(2026, 1, 31)
    assert {t.card for t in statement.expenses} == {"UOB-ONE", "UOB-VISA"}
    assert [t.remark for t in statement.expenses if t.card == "UOB-VISA"] == ["CITY RAIL TOP UP"]


def write_extracted(data_dir, name: str, *lines: str) -> None:
    text = "\n".join(["Statement Date,31 JAN 2026", *lines]) + "\n"
    (data_dir / "extracted" / f"{name}.csv").write_text(text)


def test_uob_rows_before_any_card_section_are_ignored(data_dir, rules):
    write_extracted(
        data_dir,
        "UOB09",
        "03 JAN,02 JAN,STRAY ROW,5.00",
        "UOB ONE CARD",
        "04 JAN,03 JAN,ACME COFFEE HOUSE,18.40",
    )
    statement = files.parse_extracted_csv("UOB09", rules=rules)
    assert [t.remark for t in statement.expenses] == ["ACME COFFEE HOUSE"]


def test_uob_rows_after_a_section_total_are_not_credited_to_that_card(data_dir):
    write_extracted(
        data_dir,
        "UOB09",
        "UOB ONE CARD",
        "04 JAN,03 JAN,ACME COFFEE HOUSE,18.40",
        "TOTAL BALANCE FOR UOB ONE CARD,18.40",
        "05 JAN,04 JAN,STRAY ROW,5.00",
    )
    statement = files.parse_extracted_csv("UOB09")
    assert [t.remark for t in statement.expenses] == ["ACME COFFEE HOUSE"]


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


# --- balance check ------------------------------------------------------


def test_uob_card_balances_are_read_per_card(data_dir):
    statement = files.parse_extracted_csv("UOB01")
    assert statement.balances == {
        "UOB-ONE": CardBalance("UOB-ONE", previous=1000.0, stated_total=230.59),
        "UOB-VISA": CardBalance("UOB-VISA", previous=0.0, stated_total=12.0),
    }


def test_a_uob_statement_that_adds_up_passes_the_balance_check(data_dir):
    assert balance.check_card_totals(files.parse_extracted_csv("UOB01")) == []


def test_a_lost_row_fails_the_balance_check_for_its_card(data_dir):
    statement = files.parse_extracted_csv("UOB01")
    statement.expenses = [t for t in statement.expenses if t.remark != "SUNDRY SHOP 88"]
    [mismatch] = balance.check_card_totals(statement)
    assert mismatch.card == "UOB-ONE"
    assert mismatch.difference == pytest.approx(63.75)


def test_a_credit_balance_is_read_as_negative(data_dir):
    write_extracted(
        data_dir,
        "UOB09",
        "UOB ONE CARD",
        '"",,PREVIOUS BALANCE,50.00CR',
        "04 JAN,03 JAN,ACME COFFEE HOUSE,18.40",
        "TOTAL BALANCE FOR UOB ONE CARD,31.60CR",
    )
    statement = files.parse_extracted_csv("UOB09")
    assert statement.balances["UOB-ONE"] == CardBalance("UOB-ONE", -50.0, -31.6)
    assert balance.check_card_totals(statement) == []


def test_a_card_missing_its_total_line_fails_the_balance_check(data_dir):
    write_extracted(
        data_dir,
        "UOB09",
        "UOB ONE CARD",
        '"",,PREVIOUS BALANCE,0.00',
        "04 JAN,03 JAN,ACME COFFEE HOUSE,18.40",
    )
    [mismatch] = balance.check_card_totals(files.parse_extracted_csv("UOB09"))
    assert mismatch.stated_total is None
    assert "no TOTAL BALANCE" in mismatch.describe()


def test_an_unknown_card_header_fails_the_balance_check_instead_of_losing_rows(data_dir):
    write_extracted(
        data_dir,
        "UOB09",
        "UOB ONE CARD",
        '"",,PREVIOUS BALANCE,0.00',
        "TOTAL BALANCE FOR UOB ONE CARD,0.00",
        "MYSTERY GOLD CARD",
        '"",,PREVIOUS BALANCE,0.00',
        "04 JAN,03 JAN,ACME COFFEE HOUSE,18.40",
        "TOTAL BALANCE FOR MYSTERY GOLD CARD,18.40",
    )
    [mismatch] = balance.check_card_totals(files.parse_extracted_csv("UOB09"))
    assert mismatch.card == "MYSTERY GOLD CARD"
    assert mismatch.difference == pytest.approx(18.40)


def write_chase_extracted(data_dir, *lines: str) -> None:
    text = "\n".join(["12/15/25 - 01/14/26", *lines]) + "\n"
    (data_dir / "extracted" / "Chase09.csv").write_text(text)


CHASE_ACTIVITY = (
    "01/05,AUTOMATIC PAYMENT - THANK YOU,-500.00",
    "01/06,ACME COFFEE,18.40",
    "01/07,GREENMART,96.30",
)


def test_chase_balances_are_read_from_the_account_summary(data_dir):
    write_chase_extracted(
        data_dir,
        "Previous Balance,500.00",
        '"Payment, Credits",-500.00',
        "Purchases,114.70",
        "New Balance,114.70",
        *CHASE_ACTIVITY,
    )
    statement = files.parse_extracted_csv("Chase09")
    assert statement.balances == {"CHASE": CardBalance("CHASE", 500.0, 114.7)}
    assert len(statement.expenses) == 2
    assert balance.check_card_totals(statement) == []


def test_a_lost_chase_row_fails_the_balance_check(data_dir):
    write_chase_extracted(
        data_dir, "Previous Balance,500.00", "New Balance,114.70", *CHASE_ACTIVITY[:2]
    )
    [mismatch] = balance.check_card_totals(files.parse_extracted_csv("Chase09"))
    assert mismatch.card == "CHASE"
    assert mismatch.difference == pytest.approx(96.30)


def test_a_chase_csv_without_an_account_summary_still_parses(data_dir):
    write_chase_extracted(data_dir, *CHASE_ACTIVITY)
    statement = files.parse_extracted_csv("Chase09")
    assert statement.balances == {}
    assert len(statement.expenses) == 2


def test_paylah_wallet_balances_read_as_credit_and_pass_the_check(data_dir):
    statement = files.parse_extracted_csv("PLG01")
    assert statement.balances == {"PLG": CardBalance("PLG", -100.0, -20.3)}
    assert balance.check_card_totals(statement) == []


def test_a_lost_paylah_row_fails_the_balance_check(data_dir):
    statement = files.parse_extracted_csv("PLG01")
    statement.expenses = [t for t in statement.expenses if t.remark != "GREENMART SUPERMARKET"]
    [mismatch] = balance.check_card_totals(statement)
    assert mismatch.card == "PLG"
    assert mismatch.difference == pytest.approx(96.30)


def test_a_paylah_csv_without_balances_still_parses(data_dir):
    path = data_dir / "extracted" / "PLG01.csv"
    kept = [line for line in path.read_text().splitlines() if "BALANCE" not in line]
    path.write_text("\n".join(kept) + "\n")
    statement = files.parse_extracted_csv("PLG01")
    assert statement.balances == {}
    assert len(statement.expenses) == 2


def test_statements_without_printed_balances_skip_the_check():
    statement = Statement("Chase01", "Chase")
    statement.expenses.append(Transaction(JAN_31, 10.0, "ACME", card="CHASE"))
    statement.credits.append(Credit(JAN_31, 10.0, "AUTOMATIC PAYMENT", "payment", "CHASE"))
    assert balance.check_card_totals(statement) == []


def test_a_zero_amount_row_is_kept_and_balances(data_dir):
    write_extracted(
        data_dir,
        "UOB09",
        "UOB ONE CARD",
        '"",,PREVIOUS BALANCE,0.00',
        "04 JAN,03 JAN,FREE TRIAL,0.00",
        "TOTAL BALANCE FOR UOB ONE CARD,0.00",
    )
    statement = files.parse_extracted_csv("UOB09")
    assert [(t.remark, t.cost) for t in statement.expenses] == [("FREE TRIAL", 0.0)]
    assert balance.check_card_totals(statement) == []


def test_a_uob_card_paid_beyond_its_spending_ends_in_credit(data_dir):
    write_extracted(
        data_dir,
        "UOB09",
        "UOB ONE CARD",
        '"",,PREVIOUS BALANCE,100.00',
        "15 JAN,14 JAN,PAYMT THRU E-BANK/HOMEB/CYBERB,150.00CR",
        "04 JAN,03 JAN,ACME COFFEE HOUSE,20.00",
        "TOTAL BALANCE FOR UOB ONE CARD,30.00CR",
    )
    statement = files.parse_extracted_csv("UOB09")
    assert statement.balances["UOB-ONE"].stated_total == -30.0
    assert balance.check_card_totals(statement) == []


def test_a_chase_card_paid_beyond_its_spending_ends_in_credit(data_dir):
    write_chase_extracted(
        data_dir,
        "Previous Balance,100.00",
        "New Balance,-30.00",
        "01/05,AUTOMATIC PAYMENT - THANK YOU,-150.00",
        "01/06,ACME COFFEE,20.00",
    )
    statement = files.parse_extracted_csv("Chase09")
    assert statement.balances["CHASE"].stated_total == -30.0
    assert balance.check_card_totals(statement) == []


def test_parsing_the_same_statement_twice_writes_the_same_bytes(data_dir, tmp_path):
    from trex.categorize.rules import CatRules

    first, second = tmp_path / "first.csv", tmp_path / "second.csv"
    write_parsed_csv(files.parse_extracted_csv("UOB01", rules=CatRules().load()), first)
    write_parsed_csv(files.parse_extracted_csv("UOB01", rules=CatRules().load()), second)
    assert first.read_bytes() == second.read_bytes()


# --- UNI$ rewards -------------------------------------------------------


def test_uob_uni_figures_are_read_after_the_card_sections(data_dir):
    statement = files.parse_extracted_csv("UOB01")
    assert statement.rewards == RewardsBalance(12000.0, 250.0, 0.0, -10.0, 12240.0)
    assert balance.check_card_totals(statement) == []


def test_uni_figures_that_do_not_add_up_fail_the_balance_check(data_dir):
    path = data_dir / "extracted" / "UOB01.csv"
    path.write_text(path.read_text().replace('"12,240.00"', '"12,250.00"'))
    [mismatch] = balance.check_card_totals(files.parse_extracted_csv("UOB01"))
    assert mismatch.card == "UNI$"
    assert mismatch.difference == pytest.approx(10.0)


def test_an_unreadable_uni_figure_fails_the_balance_check(data_dir):
    path = data_dir / "extracted" / "UOB01.csv"
    path.write_text(path.read_text().replace("250.00,", "NA,"))
    statement = files.parse_extracted_csv("UOB01")
    assert statement.rewards.earned is None
    assert [m.card for m in balance.check_card_totals(statement)] == ["UNI$"]


def test_a_uob_statement_without_a_uni_row_has_no_rewards(data_dir):
    path = data_dir / "extracted" / "UOB01.csv"
    kept = [line for line in path.read_text().splitlines() if not line.startswith("UNI$")]
    path.write_text("\n".join(kept) + "\n")
    statement = files.parse_extracted_csv("UOB01")
    assert statement.rewards is None
    assert balance.check_card_totals(statement) == []


def test_a_missing_extracted_csv_is_reported():
    with pytest.raises(FileNotFoundError, match="no extracted CSV"):
        files.parse_extracted_csv("NOPE99")


# --- keeping manually narrowed choices across a re-parse ----------------


def _paylah(*expenses):
    return Statement("PLY0102", "Paylah", datetime(2026, 2, 28), list(expenses))


def _transfer(category):
    return Transaction(datetime(2026, 1, 24), 55.0, "SEND MONEY TO 91234567", category, "PLY")


def test_a_narrowed_multi_category_row_keeps_its_choice(tmp_path):
    path = tmp_path / "PLY0102.csv"
    write_parsed_csv(_paylah(_transfer(1)), path)
    statement = _paylah(_transfer((1, 10)))
    assert carry_over_manual_categories(statement, path) == 1
    assert statement.expenses[0].category == 1


def test_without_an_earlier_file_the_rules_stand(tmp_path):
    statement = _paylah(_transfer((1, 10)))
    assert carry_over_manual_categories(statement, tmp_path / "PLY0102.csv") == 0
    assert statement.expenses[0].category == (1, 10)


def test_a_single_earlier_category_outside_the_rules_is_kept(tmp_path):
    path = tmp_path / "PLY0102.csv"
    write_parsed_csv(_paylah(_transfer(8)), path)
    statement = _paylah(_transfer((1, 3)))
    assert carry_over_manual_categories(statement, path) == 1
    assert statement.expenses[0].category == 8


@pytest.mark.parametrize("earlier", [(2, 3), (1, 10), None])
def test_an_earlier_multi_category_or_empty_cell_is_not_kept(tmp_path, earlier):
    path = tmp_path / "PLY0102.csv"
    write_parsed_csv(_paylah(_transfer(earlier)), path)
    statement = _paylah(_transfer((1, 10)))
    assert carry_over_manual_categories(statement, path) == 0
    assert statement.expenses[0].category == (1, 10)
