"""Warnings about spending the summary miscounts, and re-checking a written summary's total."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime
from itertools import combinations
from pathlib import Path

from trex.config import get_summary_dir
from trex.constants import HEALTHCARE_CATEGORY_ID
from trex.healthcare import looks_medical
from trex.log import get_logger
from trex.models import RowKey, row_key
from trex.summary.rows import SummaryRow, read_year_expenses, to_sgd
from trex.summary.table import (
    YearSummary,
    build_year_summary,
    read_year_summary_total,
    year_summary_name,
)

logger = get_logger(__name__)


def find_duplicated_statements(rows: list[SummaryRow]) -> list[tuple[str, str, int, float]]:
    """Return (file, other file, shared rows, shared SGD) for parsed files that overlap.

    Nothing deduplicates the parsed directory, so a stray copy of a statement —
    ``UOB05 copy.csv``, a re-download under a longer name — is counted in full a
    second time. Two statements never legitimately carry the same transaction, so
    any overlap at all is worth reporting. Repeats *within* one file are left
    alone: buying the same coffee twice in a day is ordinary.
    """
    by_transaction: dict[tuple[RowKey, str], Counter[str]] = defaultdict(Counter)
    for row in rows:
        by_transaction[(row_key(row.date, row.cost, row.remark), row.card)][row.statement] += 1

    shared_rows: Counter[tuple[str, str]] = Counter()
    shared_cost: defaultdict[tuple[str, str], float] = defaultdict(float)
    for ((_, cost, _), card), statements in by_transaction.items():
        for pair in combinations(sorted(statements), 2):
            overlap = min(statements[pair[0]], statements[pair[1]])
            shared_rows[pair] += overlap
            shared_cost[pair] += overlap * to_sgd(cost, card)

    return [(*pair, count, shared_cost[pair]) for pair, count in shared_rows.most_common()]


def warn_if_duplicated(rows: list[SummaryRow]) -> None:
    for first, second, count, cost in find_duplicated_statements(rows):
        logger.warning(
            "%s and %s share %d identical rows (%.2f SGD), counted twice in the total; "
            "one of them is probably a stray copy",
            first,
            second,
            count,
            cost,
        )


def warn_if_multi_category(rows: list[SummaryRow]) -> None:
    """Log a warning per parsed file that still has rows filed under several categories."""
    counts: Counter[str] = Counter()
    costs: defaultdict[str, float] = defaultdict(float)
    for row in rows:
        if len(row.category_ids) > 1:
            counts[row.statement] += 1
            costs[row.statement] += to_sgd(row.cost, row.card)
    for statement in sorted(counts):
        logger.warning(
            "%s has %d multi-category rows (%.2f SGD), EXCLUDED from the total above; "
            "narrow each to one category and reconcile",
            statement,
            counts[statement],
            costs[statement],
        )


def warn_if_uncategorized(summary: YearSummary) -> None:
    if summary.uncategorized.rows:
        logger.warning(
            "%d rows (%.2f SGD) are uncategorized and EXCLUDED from the total above; "
            "categorize them or the year is understated by that much",
            summary.uncategorized.rows,
            summary.uncategorized.total(),
        )


def is_medical_elsewhere(row: SummaryRow, not_medical: re.Pattern[str] | None) -> bool:
    """True if a row looks medical (see `looks_medical`) but is not filed under HEALTHCARE."""
    return HEALTHCARE_CATEGORY_ID not in row.category_ids and looks_medical(row.remark, not_medical)


def warn_if_medical_elsewhere(rows: list[SummaryRow], not_medical: re.Pattern[str] | None) -> None:
    """Log a warning per row that looks medical but is not filed under HEALTHCARE.

    Such a row is in the healthcare listing but not in its HEALTHCARE totals, nor
    in the summary's; moving it to HEALTHCARE and reconciling fixes both.
    """
    for row in rows:
        if not is_medical_elsewhere(row, not_medical):
            continue
        logger.warning(
            "%s %s %.2f %r looks medical but is not filed under HEALTHCARE; "
            "move it there and reconcile",
            row.statement,
            row.date,
            row.cost,
            row.remark,
        )


def check_year_summary_total(
    year: int | None = None,
    summary_name: str | None = None,
    tolerance: float = 0.01,
    parsed_dir: Path | None = None,
    summary_dir: Path | None = None,
) -> tuple[float, float, float]:
    """Re-sum the parsed statements and compare against the summary's total.

    Returns (recomputed, summary total, difference). A mismatch means the
    summary is stale or a parsed file changed since it was written. Excluded
    spending is reported too: it matches on both sides by construction, so it
    would otherwise hide behind an OK.
    """
    year = year or datetime.now().year
    summary_path = (summary_dir or get_summary_dir()) / (summary_name or year_summary_name(year))
    rows = read_year_expenses(year, parsed_dir)
    built = build_year_summary(year, rows)
    recomputed = built.grand_total()
    summary_total = read_year_summary_total(summary_path)
    difference = recomputed - summary_total

    status = "OK" if abs(difference) < tolerance else "MISMATCH"
    logger.info(
        "  %s: parsed-sum=%.2f  summary=%.2f  diff=%+.2f",
        status,
        recomputed,
        summary_total,
        difference,
    )
    warn_if_duplicated(rows)
    tallies = (built.uncategorized, built.multi_category)
    gross = recomputed + built.uncategorized.total() + built.multi_category.total()
    for tally in tallies:
        if tally.total():
            logger.warning(
                "  %s: %.2f SGD is in neither figure above (gross spend %.2f)",
                tally.label.upper(),
                tally.total(),
                gross,
            )
    return recomputed, summary_total, difference
