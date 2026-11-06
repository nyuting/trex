"""Parsed statements written for the summary and healthcare tests."""

from __future__ import annotations

from datetime import datetime

from trex import summary
from trex.models import Statement, Transaction
from trex.serialize import write_parsed_csv

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


def build(parsed_dir, year=2026) -> summary.YearSummary:
    return summary.build_year_summary(year, summary.read_year_expenses(year, parsed_dir))
