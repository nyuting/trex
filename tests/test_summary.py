"""Aggregation across parsed statements: year filtering, FX, shared costs, totals."""

from __future__ import annotations

import logging
from datetime import datetime

import pytest

from trex import summary
from trex.config import EXCHANGE_RATE_USD2SGD
from trex.models import Statement, Transaction
from trex.serialize import write_parsed_csv


def write_statement(parsed_dir, name, issuer, statement_date, expenses):
    """Write a parsed CSV the summary can read."""
    write_parsed_csv(
        Statement(name=name, issuer=issuer, statement_date=statement_date, expenses=list(expenses)),
        parsed_dir / f"{name}.csv",
    )


@pytest.fixture
def parsed_dir(tmp_path):
    directory = tmp_path / "parsed"
    directory.mkdir()
    return directory


def test_costs_land_in_the_month_they_were_made(parsed_dir):
    write_statement(
        parsed_dir,
        "UOB01",
        "UOB",
        datetime(2026, 3, 31),
        [
            Transaction(datetime(2026, 3, 5), 100.0, "MARCH BUY", 1, "UOB-ONE"),
            Transaction(datetime(2026, 2, 20), 50.0, "FEB BUY", 1, "UOB-ONE"),
        ],
    )
    built = summary.build_summary(2026, "summary2026.csv", parsed_dir)
    assert built.totals[1][2] == pytest.approx(100.0)  # March
    assert built.totals[1][1] == pytest.approx(50.0)  # February


def test_a_december_row_on_a_january_statement_counts_in_the_previous_year(parsed_dir):
    write_statement(
        parsed_dir,
        "UOB01",
        "UOB",
        datetime(2026, 1, 31),
        [
            Transaction(datetime(2025, 12, 28), 40.0, "DEC BUY", 1, "UOB-ONE"),
            Transaction(datetime(2026, 1, 10), 60.0, "JAN BUY", 1, "UOB-ONE"),
        ],
    )
    assert summary.build_summary(2026, "s.csv", parsed_dir).grand_total() == pytest.approx(60.0)
    assert summary.build_summary(2025, "s.csv", parsed_dir).grand_total() == pytest.approx(40.0)


def test_chase_spending_is_converted_to_sgd(parsed_dir):
    write_statement(
        parsed_dir,
        "Chase01",
        "Chase",
        datetime(2026, 1, 31),
        [Transaction(datetime(2026, 1, 5), 100.0, "US HOTEL", 9, "CHASE")],
    )
    built = summary.build_summary(2026, "s.csv", parsed_dir)
    assert built.grand_total() == pytest.approx(100.0 * EXCHANGE_RATE_USD2SGD)


def test_a_shared_cost_is_split_evenly_and_still_totals_correctly(parsed_dir):
    write_statement(
        parsed_dir,
        "UOB01",
        "UOB",
        datetime(2026, 1, 31),
        [Transaction(datetime(2026, 1, 5), 90.0, "SHARED", (1, 2, 3), "UOB-ONE")],
    )
    built = summary.build_summary(2026, "s.csv", parsed_dir)
    assert built.row_total(1) == pytest.approx(30.0)
    assert built.row_total(2) == pytest.approx(30.0)
    assert built.grand_total() == pytest.approx(90.0)


def test_uncategorized_rows_are_left_out_but_tallied(parsed_dir):
    write_statement(
        parsed_dir,
        "UOB01",
        "UOB",
        datetime(2026, 1, 31),
        [
            Transaction(datetime(2026, 1, 5), 10.0, "KNOWN", 1, "UOB-ONE"),
            Transaction(datetime(2026, 1, 6), 99.0, "MYSTERY", None, None),
        ],
    )
    built = summary.build_summary(2026, "s.csv", parsed_dir)
    assert built.grand_total() == pytest.approx(10.0)
    assert built.uncategorized_total() == pytest.approx(99.0)
    assert built.uncategorized_rows == 1
    assert built.uncategorized[0] == pytest.approx(99.0)  # January


def test_a_category_no_longer_in_categories_counts_as_uncategorized(parsed_dir):
    write_statement(
        parsed_dir,
        "UOB01",
        "UOB",
        datetime(2026, 1, 31),
        [Transaction(datetime(2026, 1, 5), 25.0, "RETIRED CATEGORY", 99, "UOB-ONE")],
    )
    built = summary.build_summary(2026, "s.csv", parsed_dir)
    assert built.grand_total() == pytest.approx(0.0)
    assert built.uncategorized_total() == pytest.approx(25.0)


def test_uncategorized_spending_is_warned_about(parsed_dir, caplog):
    write_statement(
        parsed_dir,
        "UOB01",
        "UOB",
        datetime(2026, 1, 31),
        [Transaction(datetime(2026, 1, 6), 99.0, "MYSTERY", None, None)],
    )
    with caplog.at_level(logging.WARNING):
        summary.summarize(year=2026, parsed_dir=parsed_dir)
    assert "99.00" in caplog.text
    assert "EXCLUDED" in caplog.text


def test_find_uncategorized_returns_the_rows_to_fix(parsed_dir):
    write_statement(
        parsed_dir,
        "UOB01",
        "UOB",
        datetime(2026, 1, 31),
        [
            Transaction(datetime(2026, 1, 5), 10.0, "KNOWN", 1, "UOB-ONE"),
            Transaction(datetime(2026, 1, 6), 99.0, "MYSTERY", None, None),
        ],
    )
    rows = summary.find_uncategorized(year=2026, summary_name="s.csv", parsed_dir=parsed_dir)
    assert [row.cost for row in rows] == [99.0]


def test_pending_updates_and_the_summary_itself_are_not_counted(parsed_dir):
    write_statement(
        parsed_dir,
        "UOB01",
        "UOB",
        datetime(2026, 1, 31),
        [Transaction(datetime(2026, 1, 5), 10.0, "REAL", 1, "UOB-ONE")],
    )
    write_statement(
        parsed_dir,
        "UOB01-update",
        "UOB",
        datetime(2026, 1, 31),
        [Transaction(datetime(2026, 1, 5), 999.0, "DRAFT", 1, "UOB-ONE")],
    )
    summary.summarize(year=2026, parsed_dir=parsed_dir)
    # summarizing twice must not double-count the summary file it just wrote
    assert summary.build_summary(
        2026, "summary2026.csv", parsed_dir
    ).grand_total() == pytest.approx(10.0)


def test_a_stray_copy_of_a_statement_is_reported(parsed_dir, caplog):
    expenses = [
        Transaction(datetime(2026, 1, 5), 10.0, "A", 1, "UOB-ONE"),
        Transaction(datetime(2026, 1, 6), 20.0, "B", 2, "UOB-ONE"),
    ]
    write_statement(parsed_dir, "UOB01", "UOB", datetime(2026, 1, 31), expenses)
    write_statement(parsed_dir, "UOB01 copy", "UOB", datetime(2026, 1, 31), expenses)

    rows = summary.iter_parsed_rows(2026, "s.csv", parsed_dir)
    assert summary.find_duplicated_statements(rows) == [("UOB01 copy.csv", "UOB01.csv", 2, 30.0)]

    with caplog.at_level(logging.WARNING):
        summary.summarize(year=2026, parsed_dir=parsed_dir)
    assert "stray copy" in caplog.text
    # the copy really is double counted; the warning is the only thing that says so
    assert summary.build_summary(2026, "s.csv", parsed_dir).grand_total() == pytest.approx(60.0)


def test_distinct_statements_are_not_reported_as_copies(parsed_dir, caplog):
    write_statement(
        parsed_dir,
        "UOB01",
        "UOB",
        datetime(2026, 1, 31),
        [Transaction(datetime(2026, 1, 5), 10.0, "A", 1, "UOB-ONE")],
    )
    write_statement(
        parsed_dir,
        "UOB02",
        "UOB",
        datetime(2026, 2, 28),
        [Transaction(datetime(2026, 2, 5), 10.0, "A", 1, "UOB-ONE")],
    )
    with caplog.at_level(logging.WARNING):
        summary.summarize(year=2026, parsed_dir=parsed_dir)
    assert "stray copy" not in caplog.text


def test_a_repeat_purchase_within_one_statement_is_not_a_copy(parsed_dir, caplog):
    write_statement(
        parsed_dir,
        "UOB01",
        "UOB",
        datetime(2026, 1, 31),
        [
            Transaction(datetime(2026, 1, 5), 4.5, "SAME COFFEE", 1, "UOB-ONE"),
            Transaction(datetime(2026, 1, 5), 4.5, "SAME COFFEE", 1, "UOB-ONE"),
        ],
    )
    with caplog.at_level(logging.WARNING):
        summary.summarize(year=2026, parsed_dir=parsed_dir)
    assert "stray copy" not in caplog.text
    assert summary.build_summary(2026, "s.csv", parsed_dir).grand_total() == pytest.approx(9.0)


def test_a_partial_copy_reports_only_the_shared_rows(parsed_dir):
    shared = Transaction(datetime(2026, 1, 5), 10.0, "SHARED", 1, "UOB-ONE")
    write_statement(parsed_dir, "UOB01", "UOB", datetime(2026, 1, 31), [shared])
    write_statement(
        parsed_dir,
        "UOB01-redownload",
        "UOB",
        datetime(2026, 1, 31),
        [shared, Transaction(datetime(2026, 1, 9), 7.0, "LATE ARRIVAL", 1, "UOB-ONE")],
    )
    rows = summary.iter_parsed_rows(2026, "s.csv", parsed_dir)
    assert summary.find_duplicated_statements(rows) == [
        ("UOB01-redownload.csv", "UOB01.csv", 1, 10.0)
    ]


# --- output -------------------------------------------------------------


def test_summarize_writes_a_csv_whose_totals_reconcile(parsed_dir):
    write_statement(
        parsed_dir,
        "UOB01",
        "UOB",
        datetime(2026, 1, 31),
        [
            Transaction(datetime(2026, 1, 5), 10.0, "A", 1, "UOB-ONE"),
            Transaction(datetime(2026, 1, 6), 20.0, "B", 2, "UOB-ONE"),
        ],
    )
    path = summary.summarize(year=2026, parsed_dir=parsed_dir)
    assert path.name == "summary2026.csv"
    assert summary.read_summary_total(path) == pytest.approx(30.0)

    recomputed, stated, difference = summary.check_summary_total(year=2026, parsed_dir=parsed_dir)
    assert recomputed == pytest.approx(stated)
    assert abs(difference) < 0.01


def test_check_detects_a_summary_that_no_longer_matches(parsed_dir):
    write_statement(
        parsed_dir,
        "UOB01",
        "UOB",
        datetime(2026, 1, 31),
        [Transaction(datetime(2026, 1, 5), 10.0, "A", 1, "UOB-ONE")],
    )
    path = summary.summarize(year=2026, parsed_dir=parsed_dir)
    path.write_text(path.read_text().replace("10.00\n", "10.00").replace(",10.00", ",99.00"))

    _, _, difference = summary.check_summary_total(year=2026, parsed_dir=parsed_dir)
    assert abs(difference) >= 0.01


def test_a_clean_year_writes_no_uncategorized_row(parsed_dir):
    write_statement(
        parsed_dir,
        "UOB01",
        "UOB",
        datetime(2026, 1, 31),
        [Transaction(datetime(2026, 1, 5), 10.0, "A", 1, "UOB-ONE")],
    )
    text = summary.summarize(year=2026, parsed_dir=parsed_dir).read_text()
    assert summary.UNCATEGORIZED_LABEL not in text
    assert text.rstrip().splitlines()[-1].startswith("Total,")


def test_uncategorized_spending_is_written_below_the_total(parsed_dir):
    write_statement(
        parsed_dir,
        "UOB01",
        "UOB",
        datetime(2026, 1, 31),
        [
            Transaction(datetime(2026, 1, 5), 10.0, "A", 1, "UOB-ONE"),
            Transaction(datetime(2026, 1, 6), 99.0, "MYSTERY", None, None),
        ],
    )
    path = summary.summarize(year=2026, parsed_dir=parsed_dir)
    lines = path.read_text().rstrip().splitlines()
    assert lines[-2].startswith("Total,")
    assert lines[-1] == f"{summary.UNCATEGORIZED_LABEL},99.00" + ",0.00" * 11 + ",99.00"
    # the total itself still excludes it, and check still reconciles against it
    assert summary.read_summary_total(path) == pytest.approx(10.0)
    recomputed, stated, difference = summary.check_summary_total(year=2026, parsed_dir=parsed_dir)
    assert recomputed == pytest.approx(stated)
    assert abs(difference) < 0.01


def test_a_summary_without_a_total_row_is_rejected(tmp_path):
    path = tmp_path / "summary2026.csv"
    path.write_text("category,01,Total\n1 DINING,1.00,1.00\n")
    with pytest.raises(ValueError, match="no Total row"):
        summary.read_summary_total(path)


def test_the_table_has_a_row_per_category_plus_a_total_row(parsed_dir):
    built = summary.build_summary(2026, "s.csv", parsed_dir)
    lines = summary.format_summary_table(built)
    assert lines[0] == "2026"
    assert len(lines) == 2 + len(built.totals) + 1  # year, header, categories, Total
    assert lines[-1].startswith("Total")


def test_the_table_gains_an_uncategorized_line_only_when_needed(parsed_dir):
    write_statement(
        parsed_dir,
        "UOB01",
        "UOB",
        datetime(2026, 1, 31),
        [Transaction(datetime(2026, 1, 6), 99.0, "MYSTERY", None, None)],
    )
    lines = summary.format_summary_table(summary.build_summary(2026, "s.csv", parsed_dir))
    assert lines[-2].startswith("Total")
    assert lines[-1].startswith(summary.UNCATEGORIZED_LABEL)
    assert lines[-1].rstrip().endswith("99.00")


def test_empty_cells_are_blank_so_active_months_stand_out(parsed_dir):
    write_statement(
        parsed_dir,
        "UOB01",
        "UOB",
        datetime(2026, 1, 31),
        [Transaction(datetime(2026, 1, 5), 10.0, "A", 1, "UOB-ONE")],
    )
    dining_line = summary.format_summary_table(summary.build_summary(2026, "s.csv", parsed_dir))[2]
    # January and the row total; the other eleven months render as blank padding
    assert dining_line.count("10.00") == 2
    assert dining_line.count(".") == 2
