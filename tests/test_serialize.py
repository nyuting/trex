"""The parsed-CSV text format must survive a write/read round trip unchanged."""

from __future__ import annotations

from datetime import datetime

import pytest

from trex import serialize
from trex.models import Credit, Statement, Transaction

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
    "cost, remark, category, source",
    [
        (12.34, "SIMPLE CAFE", 1, "UOB-ONE"),
        (1234.56, "BIG TICKET", 9, "CHASE"),
        (0.05, "TINY", None, None),
        (7.0, "REMARK  WITH  DOUBLE  SPACES", 2, "PLG"),
        (7.0, "12/25  9.99  1  UOB-ONE  LOOKS LIKE A ROW", 3, "PLY"),
        (7.0, "TRAILING SPACE ", 4, "UOB-VISA"),
    ],
)
def test_expense_row_round_trip(cost, remark, category, source):
    line = serialize.format_expense_row(1, JAN_02, cost, remark, category, source)
    parsed = serialize.parse_expense_row(line)
    assert parsed is not None
    assert parsed.cost == pytest.approx(cost)
    assert parsed.date == "01/02"
    assert parsed.source == (source or "")
    assert parsed.category_ids == ([category] if category else [])
    assert parsed.remark == remark


def test_multi_category_round_trip():
    line = serialize.format_expense_row(3, JAN_02, 20.0, "SHARED", "1,4", "CHASE")
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
    rows = serialize.read_parsed_csv(path)
    assert len(rows) == len(expenses)
    assert {row.remark for row in rows} == {t.remark for t in expenses}
    assert sum(row.cost for row in rows) == pytest.approx(total)


def test_uncategorized_rows_are_readable_with_empty_category_ids(tmp_path):
    path = tmp_path / "TEST01.csv"
    serialize.write_parsed_csv(
        make_statement([Transaction(JAN_02, 5.0, "MYSTERY", None, None)]), path
    )
    (row,) = serialize.read_parsed_csv(path)
    assert row.category_ids == []
    assert row.source == ""


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
    assert [row.remark for row in serialize.read_parsed_csv(path)] == ["X"]


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
