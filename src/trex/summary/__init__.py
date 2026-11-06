"""Stage 3: consolidate every parsed statement into a category x month table.

`summarize` also writes one combined statement per month, and picks the medical
charges that `trex.healthcare` lists. `totals` guards each statement's recorded
spending total.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from trex.config import get_not_medical_file, get_summary_dir
from trex.constants import HEALTHCARE_CATEGORY_ID
from trex.healthcare import (
    DEFAULT_WINDOW_DAYS,
    healthcare_listing_name,
    read_remark_regex,
    update_healthcare_listing,
)
from trex.log import get_logger
from trex.summary.checks import (
    check_year_summary_total,
    find_duplicated_statements,
    is_medical_elsewhere,
    warn_if_duplicated,
    warn_if_medical_elsewhere,
    warn_if_multi_category,
    warn_if_uncategorized,
)
from trex.summary.monthly import month_summary_name, write_month_summaries
from trex.summary.rows import (
    SummaryRow,
    as_sgd_transaction,
    read_dated_expenses,
    read_year_credits,
    read_year_expenses,
    to_sgd,
)
from trex.summary.table import (
    MONTHS_IN_YEAR,
    YearSummary,
    build_year_summary,
    format_year_summary,
    read_year_summary_total,
    write_year_summary,
    year_summary_name,
)
from trex.summary.totals import update_statement_totals

__all__ = [
    "MONTHS_IN_YEAR",
    "SummaryRow",
    "YearSummary",
    "build_year_summary",
    "check_year_summary_total",
    "find_duplicated_statements",
    "format_year_summary",
    "month_summary_name",
    "read_dated_expenses",
    "read_year_summary_total",
    "read_year_credits",
    "read_year_expenses",
    "summarize",
    "to_sgd",
    "update_statement_totals",
    "write_year_summary",
    "year_summary_name",
]

logger = get_logger(__name__)


def summarize(
    year: int | None = None,
    out_name: str | None = None,
    parsed_dir: Path | None = None,
    summary_dir: Path | None = None,
    statements_dir: Path | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    not_medical_file: Path | None = None,
) -> Path:
    """Build the summary for a year, log it as a table, and write it as CSV.

    Also rewrites one combined statement per month (see `write_month_summaries`)
    and the healthcare listing, matched against the IHP claims exports in
    `statements_dir` (see `update_healthcare_listing`). A charge outside HEALTHCARE
    is listed if it looks medical and `not_medical_file` (not_medical.csv by
    default) does not match it. Raises ValueError for an export or a regex it
    cannot read.
    Returns the yearly path written — ``<summary dir>/summary<year>.csv`` by default.
    """
    year = year or datetime.now().year
    out_name = out_name or year_summary_name(year)
    summary_dir = summary_dir or get_summary_dir()

    not_medical = read_remark_regex(not_medical_file or get_not_medical_file())
    rows = read_year_expenses(year, parsed_dir)
    summary = build_year_summary(year, rows)
    for line in format_year_summary(summary):
        logger.info("%s", line)
    warn_if_uncategorized(summary)
    warn_if_duplicated(rows)
    warn_if_multi_category(rows)
    warn_if_medical_elsewhere(rows, not_medical)

    summary_dir.mkdir(parents=True, exist_ok=True)
    out_path = summary_dir / out_name
    write_year_summary(summary, out_path)
    logger.info("wrote %s", out_path)
    write_month_summaries(year, rows, read_year_credits(year, parsed_dir), summary_dir)

    medical = [row for row in rows if HEALTHCARE_CATEGORY_ID in row.category_ids]
    medical += [row for row in rows if is_medical_elsewhere(row, not_medical)]
    charges = sorted((as_sgd_transaction(year, row) for row in medical), key=lambda t: t.date)
    listing = summary_dir / healthcare_listing_name(year)
    update_healthcare_listing(year, listing, charges, statements_dir, window_days)
    return out_path
