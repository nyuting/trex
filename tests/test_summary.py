"""Aggregation across parsed statements: year filtering, FX, multi-category rows, totals."""

from __future__ import annotations

import logging
from datetime import datetime

import pytest

from trex import summary
from trex.config import EXCHANGE_RATE_USD2SGD
from trex.constants import HEALTHCARE_CATEGORY_ID
from trex.models import Credit, Statement, Transaction
from trex.serialize import read_parsed_credits, read_parsed_expenses, write_parsed_csv

JAN_31 = datetime(2026, 1, 31)


def buy(month, day, cost, remark="A", category=1, card="UOB-ONE", year=2026) -> Transaction:
    """Return an expense dated `year`-`month`-`day`."""
    return Transaction(datetime(year, month, day), cost, remark, category, card)


def write_statement(
    parsed_dir, *expenses, name="UOB01", issuer="UOB", statement_date=JAN_31, credits=()
):
    """Write a parsed CSV the summary can read."""
    write_parsed_csv(
        Statement(
            name=name,
            issuer=issuer,
            statement_date=statement_date,
            expenses=list(expenses),
            credits=list(credits),
        ),
        parsed_dir / f"{name}.csv",
    )


def write_chase(parsed_dir, *expenses, credits=()):
    write_statement(parsed_dir, *expenses, name="Chase01", issuer="Chase", credits=credits)


@pytest.fixture
def parsed_dir(tmp_path, monkeypatch):
    """A parsed/ directory in a throwaway data tree, so summaries land in tmp_path/summary."""
    monkeypatch.setenv("TREX_DATA_DIR", str(tmp_path))
    directory = tmp_path / "parsed"
    directory.mkdir()
    return directory


@pytest.fixture
def summary_dir(parsed_dir):
    return parsed_dir.parent / "summary"


def build(parsed_dir, year=2026) -> summary.Summary:
    return summary.build_summary(year, "summary2026.csv", parsed_dir)


def test_costs_land_in_the_month_they_were_made(parsed_dir):
    write_statement(
        parsed_dir,
        buy(3, 5, 100.0, "MARCH BUY"),
        buy(2, 20, 50.0, "FEB BUY"),
        statement_date=datetime(2026, 3, 31),
    )
    built = build(parsed_dir)
    assert built.totals[1][2] == pytest.approx(100.0)  # March
    assert built.totals[1][1] == pytest.approx(50.0)  # February


def test_a_december_row_on_a_january_statement_counts_in_the_previous_year(parsed_dir):
    write_statement(parsed_dir, buy(12, 28, 40.0, "DEC BUY", year=2025), buy(1, 10, 60.0))
    assert build(parsed_dir).grand_total() == pytest.approx(60.0)
    assert build(parsed_dir, 2025).grand_total() == pytest.approx(40.0)


def test_chase_spending_is_converted_to_sgd(parsed_dir):
    write_chase(parsed_dir, buy(1, 5, 100.0, "US HOTEL", 9, "CHASE"))
    assert build(parsed_dir).grand_total() == pytest.approx(100.0 * EXCHANGE_RATE_USD2SGD)


def test_a_multi_category_row_is_left_out_but_tallied(parsed_dir):
    write_statement(parsed_dir, buy(1, 5, 10.0), buy(1, 6, 90.0, "SHARED", (1, 2, 3)))
    built = build(parsed_dir)
    assert built.row_total(1) == pytest.approx(10.0)
    assert built.row_total(2) == pytest.approx(0.0)
    assert built.grand_total() == pytest.approx(10.0)
    assert built.multi_category_total() == pytest.approx(90.0)
    assert built.multi_category_rows == 1
    assert built.multi_category[0] == pytest.approx(90.0)  # January


def test_multi_category_spending_is_warned_about_per_file(parsed_dir, caplog):
    write_statement(
        parsed_dir,
        buy(1, 24, 55.0, "SHARED", (1, 10), "PLY"),
        name="PLY0102",
        issuer="Paylah",
    )
    with caplog.at_level(logging.WARNING):
        summary.summarize(year=2026, parsed_dir=parsed_dir)
    assert "PLY0102.csv has 1 multi-category rows (55.00 SGD)" in caplog.text
    assert "EXCLUDED" in caplog.text


def test_the_summary_csv_gets_a_multi_category_row_only_when_needed(parsed_dir):
    write_statement(parsed_dir, buy(1, 5, 10.0))
    path = summary.summarize(year=2026, parsed_dir=parsed_dir)
    assert "Multi-category" not in path.read_text()

    write_statement(
        parsed_dir,
        buy(2, 5, 30.0, "SHARED", (1, 2)),
        name="UOB02",
        statement_date=datetime(2026, 2, 28),
    )
    path = summary.summarize(year=2026, parsed_dir=parsed_dir)
    last = path.read_text().splitlines()[-1].split(",")
    assert last[0] == "Multi-category"
    assert last[2] == "30.00"
    assert last[-1] == "30.00"
    assert summary.read_summary_total(path) == pytest.approx(10.0)


def test_find_multi_category_returns_the_rows_to_narrow(parsed_dir):
    write_statement(parsed_dir, buy(1, 5, 10.0), buy(1, 6, 30.0, "SHARED", (1, 2)))
    found = summary.find_multi_category(2026, parsed_dir=parsed_dir)
    assert [row.remark for row in found] == ["SHARED"]


def test_uncategorized_rows_are_left_out_but_tallied(parsed_dir):
    write_statement(parsed_dir, buy(1, 5, 10.0), buy(1, 6, 99.0, "MYSTERY", None, None))
    built = build(parsed_dir)
    assert built.grand_total() == pytest.approx(10.0)
    assert built.uncategorized_total() == pytest.approx(99.0)
    assert built.uncategorized_rows == 1
    assert built.uncategorized[0] == pytest.approx(99.0)  # January


def test_a_category_no_longer_in_categories_counts_as_uncategorized(parsed_dir):
    write_statement(parsed_dir, buy(1, 5, 25.0, "RETIRED CATEGORY", 99))
    built = build(parsed_dir)
    assert built.grand_total() == pytest.approx(0.0)
    assert built.uncategorized_total() == pytest.approx(25.0)


def test_uncategorized_spending_is_warned_about(parsed_dir, caplog):
    write_statement(parsed_dir, buy(1, 6, 99.0, "MYSTERY", None, None))
    with caplog.at_level(logging.WARNING):
        summary.summarize(year=2026, parsed_dir=parsed_dir)
    assert "99.00" in caplog.text
    assert "EXCLUDED" in caplog.text


def test_find_uncategorized_returns_the_rows_to_fix(parsed_dir):
    write_statement(parsed_dir, buy(1, 5, 10.0), buy(1, 6, 99.0, "MYSTERY", None, None))
    rows = summary.find_uncategorized(year=2026, summary_name="s.csv", parsed_dir=parsed_dir)
    assert [row.cost for row in rows] == [99.0]


def test_summarizing_again_does_not_count_its_own_output(parsed_dir):
    """Neither the summary nor the month files it wrote are read back as statements."""
    write_statement(parsed_dir, buy(1, 5, 10.0))
    first = summary.read_summary_total(summary.summarize(year=2026, parsed_dir=parsed_dir))
    second = summary.read_summary_total(summary.summarize(year=2026, parsed_dir=parsed_dir))
    assert first == second == pytest.approx(10.0)
    assert build(parsed_dir).grand_total() == pytest.approx(10.0)


def test_a_stray_copy_of_a_statement_is_reported(parsed_dir, caplog):
    expenses = [buy(1, 5, 10.0, "A", 1), buy(1, 6, 20.0, "B", 2)]
    write_statement(parsed_dir, *expenses)
    write_statement(parsed_dir, *expenses, name="UOB01 copy")

    rows = summary.read_year_expenses(2026, "s.csv", parsed_dir)
    assert summary.find_duplicated_statements(rows) == [("UOB01 copy.csv", "UOB01.csv", 2, 30.0)]

    with caplog.at_level(logging.WARNING):
        summary.summarize(year=2026, parsed_dir=parsed_dir)
    assert "stray copy" in caplog.text
    # the copy really is double counted; the warning is the only thing that says so
    assert build(parsed_dir).grand_total() == pytest.approx(60.0)


def test_distinct_statements_are_not_reported_as_copies(parsed_dir, caplog):
    write_statement(parsed_dir, buy(1, 5, 10.0))
    write_statement(parsed_dir, buy(2, 5, 10.0), name="UOB02", statement_date=datetime(2026, 2, 28))
    with caplog.at_level(logging.WARNING):
        summary.summarize(year=2026, parsed_dir=parsed_dir)
    assert "stray copy" not in caplog.text


def test_a_repeat_purchase_within_one_statement_is_not_a_copy(parsed_dir, caplog):
    write_statement(parsed_dir, buy(1, 5, 4.5, "SAME COFFEE"), buy(1, 5, 4.5, "SAME COFFEE"))
    with caplog.at_level(logging.WARNING):
        summary.summarize(year=2026, parsed_dir=parsed_dir)
    assert "stray copy" not in caplog.text
    assert build(parsed_dir).grand_total() == pytest.approx(9.0)


def test_a_partial_copy_reports_only_the_shared_rows(parsed_dir):
    shared = buy(1, 5, 10.0, "SHARED")
    write_statement(parsed_dir, shared)
    write_statement(parsed_dir, shared, buy(1, 9, 7.0, "LATE ARRIVAL"), name="UOB01-redownload")
    rows = summary.read_year_expenses(2026, "s.csv", parsed_dir)
    assert summary.find_duplicated_statements(rows) == [
        ("UOB01-redownload.csv", "UOB01.csv", 1, 10.0)
    ]


# --- output -------------------------------------------------------------


def test_summarize_writes_a_csv_whose_totals_reconcile(parsed_dir):
    write_statement(parsed_dir, buy(1, 5, 10.0, "A", 1), buy(1, 6, 20.0, "B", 2))
    path = summary.summarize(year=2026, parsed_dir=parsed_dir)
    assert path == parsed_dir.parent / "summary" / "summary2026.csv"
    assert summary.read_summary_total(path) == pytest.approx(30.0)

    recomputed, stated, difference = summary.check_summary_total(year=2026, parsed_dir=parsed_dir)
    assert recomputed == pytest.approx(stated)
    assert abs(difference) < 0.01


def test_check_detects_a_summary_that_no_longer_matches(parsed_dir):
    write_statement(parsed_dir, buy(1, 5, 10.0))
    path = summary.summarize(year=2026, parsed_dir=parsed_dir)
    *body, total_row = path.read_text().splitlines()
    assert total_row.startswith("Total,")
    path.write_text("\n".join([*body, total_row.rsplit(",", 1)[0] + ",99.00"]) + "\n")

    _, _, difference = summary.check_summary_total(year=2026, parsed_dir=parsed_dir)
    assert abs(difference) >= 0.01


def test_a_clean_year_writes_no_uncategorized_row(parsed_dir):
    write_statement(parsed_dir, buy(1, 5, 10.0))
    text = summary.summarize(year=2026, parsed_dir=parsed_dir).read_text()
    assert summary.UNCATEGORIZED_ROW_LABEL not in text
    assert text.rstrip().splitlines()[-1].startswith("Total,")


def test_uncategorized_spending_is_written_below_the_total(parsed_dir):
    write_statement(parsed_dir, buy(1, 5, 10.0), buy(1, 6, 99.0, "MYSTERY", None, None))
    path = summary.summarize(year=2026, parsed_dir=parsed_dir)
    lines = path.read_text().rstrip().splitlines()
    assert lines[-2].startswith("Total,")
    assert lines[-1] == f"{summary.UNCATEGORIZED_ROW_LABEL},99.00" + ",0.00" * 11 + ",99.00"
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
    built = build(parsed_dir)
    lines = summary.format_summary_table(built)
    assert lines[0] == "2026"
    assert len(lines) == 2 + len(built.totals) + 1  # year, header, categories, Total
    assert lines[-1].startswith("Total")


def test_the_table_gains_an_uncategorized_line_only_when_needed(parsed_dir):
    write_statement(parsed_dir, buy(1, 6, 99.0, "MYSTERY", None, None))
    lines = summary.format_summary_table(build(parsed_dir))
    assert lines[-2].startswith("Total")
    assert lines[-1].startswith(summary.UNCATEGORIZED_ROW_LABEL)
    assert lines[-1].rstrip().endswith("99.00")


def test_empty_cells_are_blank_so_active_months_stand_out(parsed_dir):
    write_statement(parsed_dir, buy(1, 5, 10.0))
    dining_line = summary.format_summary_table(build(parsed_dir))[2]
    # January and the row total; the other eleven months render as blank padding
    assert dining_line.count("10.00") == 2
    assert dining_line.count(".") == 2


def test_table_and_csv_list_the_same_rows(parsed_dir, tmp_path):
    write_statement(parsed_dir, buy(1, 5, 10.0), buy(1, 6, 99.0, "MYSTERY", None, None))
    built = build(parsed_dir)
    path = tmp_path / "s.csv"
    summary.write_summary_csv(built, path)
    csv_labels = [line.split(",")[0] for line in path.read_text().splitlines()[1:]]
    table = summary.format_summary_table(built)[2:]
    assert [line[: len(label)] for line, label in zip(table, csv_labels, strict=True)] == csv_labels
    # zero months are blank in category rows but printed in the Total row
    total_line = next(line for line in table if line.startswith("Total"))
    assert "0.00" in total_line.split()[2:]
    assert summary.UNCATEGORIZED_ROW_LABEL in csv_labels


# --- per-month summaries ------------------------------------------------


def month_path(summary_dir, month):
    return summary_dir / summary.monthly_summary_name(2026, month)


def read_month(summary_dir, month):
    """Return the expense rows of one 2026 month's file, [] if it has none."""
    path = month_path(summary_dir, month)
    return read_parsed_expenses(path) if path.exists() else []


def read_month_credits(summary_dir, month):
    """Return the credit rows of one 2026 month's file, [] if it has none."""
    path = month_path(summary_dir, month)
    return read_parsed_credits(path) if path.exists() else []


def test_the_month_files_are_named_by_year_and_month():
    assert summary.monthly_summary_name(2026, 1) == "summary2026_01jan.csv"
    assert summary.monthly_summary_name(2026, 8) == "summary2026_08aug.csv"


def test_a_month_combines_every_card_and_splits_by_calendar_month(parsed_dir, summary_dir):
    write_statement(
        parsed_dir,
        buy(1, 20, 10.0, "UOB JAN"),
        buy(2, 5, 20.0, "UOB FEB", 2),
        name="UOB02",
        statement_date=datetime(2026, 2, 10),
    )
    write_statement(
        parsed_dir, buy(1, 3, 5.0, "PAYLAH JAN", card="PLG"), name="PLG01", issuer="PayLah"
    )
    summary.summarize(year=2026, parsed_dir=parsed_dir)

    assert sorted(t.remark for t in read_month(summary_dir, 1)) == ["PAYLAH JAN", "UOB JAN"]
    assert [t.remark for t in read_month(summary_dir, 2)] == ["UOB FEB"]
    assert not month_path(summary_dir, 3).exists()


def test_a_december_row_on_a_january_statement_is_not_in_this_january(parsed_dir, summary_dir):
    write_statement(
        parsed_dir, buy(12, 28, 40.0, "DEC BUY", year=2025), buy(1, 10, 60.0, "JAN BUY")
    )
    summary.summarize(year=2026, parsed_dir=parsed_dir)
    assert [t.remark for t in read_month(summary_dir, 1)] == ["JAN BUY"]
    assert not month_path(summary_dir, 12).exists()


def test_a_month_converts_chase_to_sgd_and_keeps_the_usd_amount(parsed_dir, summary_dir):
    write_chase(
        parsed_dir,
        buy(1, 5, 100.0, "US HOTEL", 9, "CHASE"),
        credits=[Credit(datetime(2026, 1, 9), 10.0, "HOTEL REFUND", "refund", "CHASE")],
    )
    summary.summarize(year=2026, parsed_dir=parsed_dir)
    [expense] = read_month(summary_dir, 1)
    assert expense.cost == pytest.approx(round(100.0 * EXCHANGE_RATE_USD2SGD, 2))
    assert expense.remark == "US HOTEL [USD 100.00]"
    [credit] = read_month_credits(summary_dir, 1)
    assert credit.cost == pytest.approx(round(10.0 * EXCHANGE_RATE_USD2SGD, 2))
    assert credit.remark == "HOTEL REFUND [USD 10.00]"


def test_every_credit_lands_in_the_month_it_was_dated(parsed_dir, summary_dir):
    write_statement(
        parsed_dir,
        buy(2, 1, 20.0),
        name="UOB02",
        statement_date=datetime(2026, 2, 10),
        credits=[
            Credit(datetime(2026, 1, 25), 500.0, "PAYMT THRU E-BANK", "payment", "UOB-ONE"),
            Credit(datetime(2026, 2, 3), 7.5, "CASHBACK", "rebate", "UOB-ONE"),
        ],
    )
    summary.summarize(year=2026, parsed_dir=parsed_dir)
    assert [(c.remark, c.kind, c.cost) for c in read_month_credits(summary_dir, 1)] == [
        ("PAYMT THRU E-BANK", "payment", 500.0)
    ]
    assert read_month(summary_dir, 1) == []
    assert [(c.remark, c.kind) for c in read_month_credits(summary_dir, 2)] == [
        ("CASHBACK", "rebate")
    ]


def test_a_month_total_matches_the_summary_column_plus_what_it_excludes(parsed_dir, summary_dir):
    write_statement(
        parsed_dir,
        buy(1, 5, 10.0),
        buy(1, 6, 30.0, "SHARED", (1, 2)),
        buy(1, 7, 99.0, "MYSTERY", None, None),
    )
    write_chase(parsed_dir, buy(1, 8, 50.0, "US", 9, "CHASE"))
    summary.summarize(year=2026, parsed_dir=parsed_dir)
    built = build(parsed_dir)
    expected = built.month_total(0) + built.uncategorized[0] + built.multi_category[0]
    assert sum(row.cost for row in read_month(summary_dir, 1)) == pytest.approx(expected, abs=0.01)


def test_a_month_that_empties_loses_its_file(parsed_dir, summary_dir):
    february = datetime(2026, 2, 10)
    write_statement(parsed_dir, buy(1, 5, 10.0), buy(2, 5, 20.0, "B"), statement_date=february)
    summary.summarize(year=2026, parsed_dir=parsed_dir)
    assert month_path(summary_dir, 1).exists()

    write_statement(parsed_dir, buy(2, 5, 20.0, "B"), statement_date=february)
    summary.summarize(year=2026, parsed_dir=parsed_dir)
    assert not month_path(summary_dir, 1).exists()
    assert (summary_dir / "summary2026.csv").exists()
    assert month_path(summary_dir, 2).exists()


# --- healthcare listing -------------------------------------------------


def healthcare_path(summary_dir):
    return summary_dir / summary.category_listing_name(2026, HEALTHCARE_CATEGORY_ID)


def read_healthcare(summary_dir):
    """Return the expense rows of the 2026 healthcare listing, [] if there is none."""
    path = healthcare_path(summary_dir)
    return read_parsed_expenses(path) if path.exists() else []


def test_the_healthcare_file_is_named_by_year():
    assert summary.category_listing_name(2026, HEALTHCARE_CATEGORY_ID) == "healthcare2026.csv"


def test_the_healthcare_listing_holds_every_card_and_month_and_nothing_else(
    parsed_dir, summary_dir
):
    write_statement(
        parsed_dir,
        buy(1, 20, 80.0, "CLINIC JAN", 8),
        buy(2, 5, 20.0, "LUNCH", 1),
        name="UOB02",
        statement_date=datetime(2026, 2, 10),
    )
    write_statement(
        parsed_dir,
        buy(3, 3, 45.0, "PHARMACY MAR", 8, "PLG"),
        name="PLG03",
        issuer="PayLah",
        statement_date=datetime(2026, 3, 31),
    )
    summary.summarize(year=2026, parsed_dir=parsed_dir)

    rows = read_healthcare(summary_dir)
    assert sorted((t.remark, t.date, t.card) for t in rows) == [
        ("CLINIC JAN", "01/20", "UOB-ONE"),
        ("PHARMACY MAR", "03/03", "PLG"),
    ]
    built = build(parsed_dir)
    assert sum(t.cost for t in rows) == pytest.approx(built.row_total(HEALTHCARE_CATEGORY_ID))


def test_a_shared_healthcare_row_is_listed_at_its_full_cost(parsed_dir, summary_dir):
    write_statement(parsed_dir, buy(1, 6, 30.0, "PAEDIATRICIAN", (4, 8)))
    summary.summarize(year=2026, parsed_dir=parsed_dir)
    [row] = read_healthcare(summary_dir)
    assert (row.cost, row.category_ids) == (30.0, [4, 8])


def test_the_healthcare_listing_converts_chase_and_keeps_the_usd_amount(parsed_dir, summary_dir):
    write_chase(parsed_dir, buy(1, 5, 100.0, "US CLINIC", 8, "CHASE"))
    summary.summarize(year=2026, parsed_dir=parsed_dir)
    [row] = read_healthcare(summary_dir)
    assert row.cost == pytest.approx(round(100.0 * EXCHANGE_RATE_USD2SGD, 2))
    assert row.remark == "US CLINIC [USD 100.00]"


def test_the_healthcare_listing_leaves_out_other_years(parsed_dir, summary_dir):
    write_statement(
        parsed_dir,
        buy(12, 28, 40.0, "DEC CLINIC", 8, year=2025),
        buy(1, 10, 60.0, "JAN CLINIC", 8),
    )
    summary.summarize(year=2026, parsed_dir=parsed_dir)
    assert [t.remark for t in read_healthcare(summary_dir)] == ["JAN CLINIC"]


def test_a_year_without_healthcare_writes_no_listing_and_drops_a_stale_one(parsed_dir, summary_dir):
    write_statement(parsed_dir, buy(1, 5, 10.0, "CLINIC", 8))
    summary.summarize(year=2026, parsed_dir=parsed_dir)
    assert healthcare_path(summary_dir).exists()

    write_statement(parsed_dir, buy(1, 5, 10.0, "LUNCH", 1))
    summary.summarize(year=2026, parsed_dir=parsed_dir)
    assert not healthcare_path(summary_dir).exists()
