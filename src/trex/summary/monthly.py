"""The per-month files: one combined, parsed-CSV-format statement per month, in SGD."""

from __future__ import annotations

import calendar
from datetime import datetime
from pathlib import Path

from trex.log import get_logger
from trex.models import Credit, Statement
from trex.serialize import write_parsed_csv
from trex.summary.rows import SummaryRow, as_sgd_transaction, remark_in_sgd, to_sgd
from trex.summary.table import MONTHS_IN_YEAR

logger = get_logger(__name__)

MONTH_ABBREVIATIONS = tuple(calendar.month_abbr[month].lower() for month in range(1, 13))


def month_summary_name(year: int, month: int) -> str:
    """Return the per-month summary filename, e.g. ``summary2026_01jan.csv``."""
    return f"summary{year}_{month:02d}{MONTH_ABBREVIATIONS[month - 1]}.csv"


def write_month_summaries(
    year: int, rows: list[SummaryRow], credits: list[Credit], summary_dir: Path
) -> list[Path]:
    """Write ``summary<year>_<NNmon>.csv`` for each month with activity; return the paths.

    Each is a parsed-CSV-format statement spanning every card. A month file left
    over from an earlier run whose month now has no rows is deleted, so the
    directory never shows spending that is no longer there.
    """
    summary_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for statement in build_month_statements(year, rows, credits).values():
        path = summary_dir / f"{statement.name}.csv"
        write_parsed_csv(statement, path)
        written.append(path)
    all_months = {month_summary_name(year, month) for month in range(1, MONTHS_IN_YEAR + 1)}
    for path in summary_dir.glob(f"summary{year}_*.csv"):
        if path.name in all_months and path not in written:
            path.unlink()
            logger.info("removed %s, its month no longer has any rows", path)
    return written


def build_month_statements(
    year: int, rows: list[SummaryRow], credits: list[Credit]
) -> dict[int, Statement]:
    """Return month -> one Statement combining every card's expenses and credits.

    Amounts are converted to SGD so a month's subtotals don't mix currencies; a
    converted row keeps its original amount in the remark. Categories unknown to
    `CATEGORIES` become uncategorized, as in the summary, so each month's grand
    total equals that month's Total plus Uncategorized and Multi-category there.
    """
    months: dict[int, Statement] = {}

    def month_statement(month: int) -> Statement:
        if month not in months:
            name = month_summary_name(year, month).removesuffix(".csv")
            last_day = calendar.monthrange(year, month)[1]
            months[month] = Statement(name, "", datetime(year, month, last_day))
        return months[month]

    for row in rows:
        month_statement(row.month).expenses.append(as_sgd_transaction(year, row))
    for credit in sorted(credits, key=lambda c: (c.card or "", c.date)):
        card = credit.card or ""
        cost = to_sgd(credit.cost, card)
        remark = remark_in_sgd(credit.remark, credit.cost, card)
        month_statement(credit.date.month).credits.append(
            Credit(credit.date, cost, remark, credit.kind, credit.card)
        )
    return dict(sorted(months.items()))
