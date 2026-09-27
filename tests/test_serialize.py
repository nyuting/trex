"""The parsed-CSV text format must survive a write/read round trip unchanged."""

from __future__ import annotations

from datetime import datetime

import pytest

from trex import serialize
from trex.models import CardBalance, Credit, RewardsBalance, Statement, Transaction

JAN_02 = datetime(2026, 1, 2)
JAN_15 = datetime(2026, 1, 15)


def make_statement(expenses, credits=(), name="TEST01"):
    return Statement(
        name=name,
        issuer="UOB",
        statement_date=datetime(2026, 1, 31),
        expenses=list(expenses),
        credits=list(credits),
    )


@pytest.mark.parametrize(
    "cost, remark, category, card",
    [
        (12.34, "SIMPLE CAFE", 1, "UOB-ONE"),
        (1234.56, "BIG TICKET", 9, "CHASE"),
        (0.05, "TINY", None, None),
        (7.0, "REMARK  WITH  DOUBLE  SPACES", 2, "PLG"),
        (7.0, "12/25  9.99  1  UOB-ONE  LOOKS LIKE A ROW", 3, "PLY"),
        (7.0, "TRAILING SPACE ", 4, "UOB-VISA"),
    ],
)
def test_expense_row_round_trip(cost, remark, category, card):
    line = serialize.format_expense_row(1, JAN_02, cost, remark, category, card)
    parsed = serialize.parse_expense_row(line)
    assert parsed is not None
    assert parsed.cost == pytest.approx(cost)
    assert parsed.date == "01/02"
    assert parsed.card == (card or "")
    assert parsed.category_ids == ([category] if category else [])
    assert parsed.remark == remark


def test_multi_category_round_trip():
    line = serialize.format_expense_row(3, JAN_02, 20.0, "SHARED", (1, 4), "CHASE")
    parsed = serialize.parse_expense_row(line)
    assert parsed.category_ids == [1, 4]


def test_non_expense_lines_are_rejected():
    assert serialize.parse_expense_row("1 DINING") is None
    assert serialize.parse_expense_row("") is None
    assert serialize.parse_expense_row(serialize.format_total_row(10.0, "1", "DINING")) is None


def test_write_then_read_recovers_every_expense(tmp_path):
    expenses = [
        Transaction(JAN_02, 10.0, "CAFE A", 1, "UOB-ONE"),
        Transaction(JAN_15, 25.5, "MARKET B", 2, "UOB-ONE"),
        Transaction(JAN_15, 5.0, "MYSTERY C", None, None),
        Transaction(JAN_02, 40.0, "SPLIT D", (1, 7), "CHASE"),
    ]
    path = tmp_path / "TEST01.csv"
    total = serialize.write_parsed_csv(make_statement(expenses), path)

    assert total == pytest.approx(80.5)
    rows = serialize.read_parsed_expenses(path)
    assert len(rows) == len(expenses)
    assert {row.remark for row in rows} == {t.remark for t in expenses}
    assert sum(row.cost for row in rows) == pytest.approx(total)


def test_uncategorized_rows_are_readable_with_empty_category_ids(tmp_path):
    path = tmp_path / "TEST01.csv"
    serialize.write_parsed_csv(
        make_statement([Transaction(JAN_02, 5.0, "MYSTERY", None, None)]), path
    )
    (row,) = serialize.read_parsed_expenses(path)
    assert row.category_ids == []
    assert row.card == ""


def test_statement_date_header_round_trips(tmp_path):
    path = tmp_path / "TEST01.csv"
    serialize.write_parsed_csv(make_statement([Transaction(JAN_02, 5.0, "X", 1, "CHASE")]), path)
    assert serialize.read_parsed_statement_date(path) == datetime(2026, 1, 31)


def test_credits_are_written_as_columns_not_expense_rows(tmp_path):
    path = tmp_path / "TEST01.csv"
    statement = make_statement(
        [Transaction(JAN_02, 5.0, "X", 1, "CHASE")],
        credits=[Credit(JAN_15, 100.0, "PAYMENT RECEIVED", "payment", "UOB-ONE")],
    )
    total = serialize.write_parsed_csv(statement, path)

    assert total == pytest.approx(5.0), "credits must not count toward the expense total"
    assert "-100.00" in path.read_text()
    assert [row.remark for row in serialize.read_parsed_expenses(path)] == ["X"]


def test_sections_are_ordered_by_category_with_uncategorized_last(tmp_path):
    path = tmp_path / "TEST01.csv"
    serialize.write_parsed_csv(
        make_statement(
            [
                Transaction(JAN_02, 1.0, "C", None, None),
                Transaction(JAN_02, 1.0, "B", 3, "CHASE"),
                Transaction(JAN_02, 1.0, "A", 1, "CHASE"),
                Transaction(JAN_02, 1.0, "D", (1, 2), "CHASE"),
            ]
        ),
        path,
    )
    text = path.read_text()
    assert text.index("1 DINING") < text.index("3 TRANSPORT") < text.index("uncategorized")
    assert text.index("uncategorized") < text.index("multi-category")


def test_golden_file_reproduces_byte_for_byte(tmp_path, data_dir):
    """A committed parsed CSV read back and rewritten must be unchanged."""
    from trex.reconcile import read_parsed_statement

    golden = data_dir / "parsed" / "UOB01.csv"
    rewritten = tmp_path / "UOB01.csv"
    serialize.write_parsed_csv(read_parsed_statement(golden), rewritten)
    assert rewritten.read_text() == golden.read_text()


# --- per-card balances --------------------------------------------------


def balanced_statement() -> Statement:
    statement = make_statement(
        [Transaction(JAN_02, 40.0, "CAFE", 1, "UOB-ONE")],
        [Credit(JAN_15, 100.0, "PAYMT", "payment", "UOB-ONE")],
    )
    statement.balances["UOB-ONE"] = CardBalance("UOB-ONE", previous=100.0, stated_total=40.0)
    return statement


def test_credits_are_grouped_per_card_with_that_cards_balance(tmp_path):
    path = tmp_path / "TEST01.csv"
    serialize.write_parsed_csv(balanced_statement(), path)
    lines = path.read_text().splitlines()
    start = lines.index(",100.00,previous balance,,UOB-ONE")
    assert lines[start : start + 6] == [
        ",100.00,previous balance,,UOB-ONE",
        "01/15,-100.00,PAYMT,payment,UOB-ONE",
        ",-100.00,credits,total,UOB-ONE",
        ",40.00,spending,total,UOB-ONE",
        ",40.00,balance,total,UOB-ONE",
        ",40.00,statement balance,ok,UOB-ONE",
    ]


def test_a_card_that_does_not_add_up_says_how_far_off_it_is(tmp_path):
    statement = balanced_statement()
    statement.balances["UOB-ONE"].stated_total = 52.5
    path = tmp_path / "TEST01.csv"
    serialize.write_parsed_csv(statement, path)
    assert ",52.50,statement balance,off 12.50,UOB-ONE" in path.read_text()


def test_printed_balances_are_read_back(tmp_path):
    path = tmp_path / "TEST01.csv"
    serialize.write_parsed_csv(balanced_statement(), path)
    assert serialize.read_card_balances(path) == {
        "UOB-ONE": CardBalance("UOB-ONE", previous=100.0, stated_total=40.0)
    }


def test_a_statement_without_balances_keeps_the_flat_credits_block(tmp_path):
    statement = make_statement([], [Credit(JAN_15, 5.0, "REFUND", "refund", "CHASE")])
    path = tmp_path / "TEST01.csv"
    serialize.write_parsed_csv(statement, path)
    text = path.read_text()
    assert "01/15,-5.00,REFUND,refund,CHASE" in text
    assert "balance" not in text
    assert serialize.read_card_balances(path) == {}


# --- UNI$ rewards -------------------------------------------------------


def test_the_uni_block_follows_the_card_balances_and_sums_down_its_column(tmp_path):
    statement = balanced_statement()
    statement.rewards = RewardsBalance(1000.0, 50.0, 200.0, -5.0, 845.0)
    path = tmp_path / "TEST01.csv"
    serialize.write_parsed_csv(statement, path)
    lines = path.read_text().splitlines()
    start = lines.index(",1000.00,UNI$ previous,,")
    assert start > lines.index(",40.00,statement balance,ok,UOB-ONE")
    assert lines[start : start + 7] == [
        ",1000.00,UNI$ previous,,",
        ",50.00,UNI$ earned,,",
        ",-200.00,UNI$ used,,",
        ",-5.00,UNI$ adjustment,,",
        ",845.00,UNI$ balance,total,",
        ",845.00,UNI$ statement balance,ok,",
        "",
    ]


def test_uni_figures_are_read_back_and_do_not_leak_into_other_readers(tmp_path):
    statement = balanced_statement()
    statement.rewards = RewardsBalance(1000.0, 50.0, 0.0, 0.0, 1050.0)
    path = tmp_path / "TEST01.csv"
    serialize.write_parsed_csv(statement, path)
    assert serialize.read_rewards(path) == statement.rewards
    assert ",0.00,UNI$ used,," in path.read_text()  # not "-0.00"
    assert list(serialize.read_card_balances(path)) == ["UOB-ONE"]
    assert len(serialize.read_parsed_credits(path)) == 1
    assert len(serialize.read_parsed_expenses(path)) == 1


def test_uni_figures_that_do_not_add_up_say_how_far_off(tmp_path):
    statement = make_statement([])
    statement.rewards = RewardsBalance(1000.0, 50.0, 0.0, 0.0, 1040.0)
    path = tmp_path / "TEST01.csv"
    serialize.write_parsed_csv(statement, path)
    assert ",1040.00,UNI$ statement balance,off -10.00," in path.read_text()


def test_an_unread_uni_figure_is_written_blank_and_marked_missing(tmp_path):
    statement = make_statement([])
    statement.rewards = RewardsBalance(1000.0, None, 0.0, 0.0, 1050.0)
    path = tmp_path / "TEST01.csv"
    serialize.write_parsed_csv(statement, path)
    text = path.read_text()
    assert ",,UNI$ earned,," in text
    assert "UNI$ statement balance,missing," in text
    assert serialize.read_rewards(path) == statement.rewards


def test_a_statement_without_rewards_writes_no_uni_block(tmp_path):
    path = tmp_path / "TEST01.csv"
    serialize.write_parsed_csv(balanced_statement(), path)
    assert "UNI$" not in path.read_text()
    assert serialize.read_rewards(path) is None


# --- Chase end to end ---------------------------------------------------


def test_a_chase_statement_survives_parse_write_read_write(tmp_path, data_dir, rules):
    """Chase has no committed parsed fixture, so its round trip is checked from extracted rows."""
    from trex.parse.files import parse_extracted_csv
    from trex.reconcile import read_parsed_statement

    (data_dir / "extracted" / "Chase09.csv").write_text(
        "\n".join(
            [
                "12/15/25 - 01/14/26",
                "Previous Balance,500.00",
                "New Balance,114.70",
                "01/05,AUTOMATIC PAYMENT - THANK YOU,-500.00",
                "01/06,ACME COFFEE,18.40",
                "01/07,GREENMART,96.30",
            ]
        )
        + "\n"
    )
    first = tmp_path / "Chase09.csv"
    serialize.write_parsed_csv(parse_extracted_csv("Chase09", rules=rules), first)
    assert ",114.70,statement balance,ok,CHASE" in first.read_text()
    second = tmp_path / "again" / "Chase09.csv"
    serialize.write_parsed_csv(read_parsed_statement(first), second)
    assert second.read_text() == first.read_text()
