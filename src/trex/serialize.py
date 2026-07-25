"""The on-disk format of parsed statement CSVs — read and write, in one place.

A parsed CSV is a human-editable text table stored in a CSV shell: a
``Statement Date`` header, an optional block of credits as real columns, then
one section per category whose expense lines are single-column fixed-width
strings. Nothing outside this module should know that layout; everything else
works with :mod:`trex.models` objects.
"""

from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path

from trex.config import get_parsed_dir
from trex.constants import CATEGORIES, MULTI_CATEGORY_LABEL, UNCATEGORIZED_LABEL
from trex.log import get_logger
from trex.models import Credit, ParsedExpenseRow, Statement, Transaction
from trex.text import parse_num_list

logger = get_logger(__name__)

#: Matches a formatted expense line: number, date, cost, category cell, source, remark.
EXPENSE_ROW_RE = re.compile(r"^\s*(\d+)\s+(\d{2}/\d{2})\s+([\d.]+)\s+(\S+)\s+(\S+)\s+(.*)$")

_STATEMENT_DATE_HEADER = "Statement Date"
_COLUMN_HEADER = ["date", "cost", "remark", "category", "source"]


# --- formatting ---------------------------------------------------------


def format_expense_row(
    number: int,
    date: datetime | None,
    cost: float,
    remark: str,
    category: object,
    source: str | None,
) -> str:
    """Return one fixed-width expense line, the inverse of `parse_expense_row`."""
    source_cell = source if source else "-"
    category_cell = str(category) if category is not None else "-"
    date_cell = date.strftime("%m/%d") if date is not None else ""
    return (
        f"{number:>4}  {date_cell:>5}  {cost:>8.2f}  {category_cell:>3}  {source_cell:>9}  {remark}"
    )


def format_total_row(cost: float, category_cell: str, remark: str) -> str:
    """Return a subtotal/grand-total line, aligned to the expense columns."""
    return f"{'':>4}  {'':>5}  {cost:>8.2f}  {category_cell:>3}  {'Total':>9}  {remark}"


def category_section_label(category_id: int | None) -> str:
    """Return the section header text for a category id, e.g. ``"1 DINING"``."""
    if category_id in CATEGORIES:
        return f"{category_id} {CATEGORIES[category_id]}"
    return UNCATEGORIZED_LABEL


# --- reading ------------------------------------------------------------


def parse_expense_row(line: str) -> ParsedExpenseRow | None:
    """Parse one line written by `format_expense_row`; None if it is not one.

    The row number is intentionally dropped: it is display ordering, recomputed
    on every write.
    """
    match = EXPENSE_ROW_RE.match(line)
    if not match:
        return None
    _, date_cell, cost_cell, category_cell, source_cell, remark = match.groups()
    try:
        cost = float(cost_cell)
    except ValueError:
        return None
    return ParsedExpenseRow(
        date=date_cell,
        cost=cost,
        remark=remark,
        category_ids=parse_num_list(category_cell),
        source="" if source_cell == "-" else source_cell,
    )


def read_parsed_statement_date(path: str | Path) -> datetime | None:
    """Return the ``Statement Date`` header of a parsed CSV, or None if absent."""
    with open(path) as handle:
        for row in csv.reader(handle):
            if row and row[0] == _STATEMENT_DATE_HEADER and len(row) >= 2:
                try:
                    return datetime.strptime(row[1].strip(), "%Y-%m-%d")
                except ValueError:
                    return None
            if row and row[0] == _COLUMN_HEADER[0]:
                return None
    return None


def read_parsed_csv(path: str | Path) -> list[ParsedExpenseRow]:
    """Return the expense rows of a parsed CSV, skipping headers and subtotals.

    Uncategorized rows are kept with an empty ``category_ids`` so reconcile can
    spot the ones a human has since filled in.
    """
    rows: list[ParsedExpenseRow] = []
    with open(path) as handle:
        for raw_row in csv.reader(handle):
            if len(raw_row) != 1:
                continue
            parsed = parse_expense_row(raw_row[0])
            if parsed is not None:
                rows.append(parsed)
    return rows


# --- writing ------------------------------------------------------------


def write_parsed_csv(statement: Statement, path: str | Path | None = None) -> float:
    """Write `statement` as a category-grouped parsed CSV and return its total.

    Layout: statement-date header, credits, one section per category (sorted by
    remark then date, with a subtotal), a multi-category section, grand total.
    Defaults to ``<parsed dir>/<statement name>.csv``.
    """
    path = Path(path) if path is not None else get_parsed_dir() / f"{statement.name}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)

    single_category, multi_category = _split_by_category(statement.expenses)
    grand_total = 0.0
    row_number = 0

    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        _write_header(writer, statement)
        _write_credits(writer, statement.credits)

        for category_id in _section_order(single_category):
            writer.writerow([category_section_label(category_id)])
            subtotal, row_number = _write_transactions(
                writer, single_category[category_id], row_number
            )
            label = CATEGORIES.get(category_id, UNCATEGORIZED_LABEL)
            category_cell = str(category_id) if category_id is not None else "-"
            writer.writerow([format_total_row(subtotal, category_cell, label)])
            writer.writerow([])
            grand_total += subtotal

        if multi_category:
            writer.writerow([MULTI_CATEGORY_LABEL])
            subtotal, row_number = _write_transactions(writer, multi_category, row_number)
            writer.writerow([format_total_row(subtotal, "", MULTI_CATEGORY_LABEL)])
            writer.writerow([])
            grand_total += subtotal

        writer.writerow([format_total_row(grand_total, "", "")])

    logger.info("wrote %s", path)
    return grand_total


def _split_by_category(
    expenses: list[Transaction],
) -> tuple[dict[int | None, list[Transaction]], list[Transaction]]:
    """Bucket transactions by single category id, splitting out multi-category ones."""
    by_category: dict[int | None, list[Transaction]] = {}
    multi_category: list[Transaction] = []
    for transaction in expenses:
        if transaction.is_multi_category:
            multi_category.append(transaction)
        else:
            by_category.setdefault(transaction.category, []).append(transaction)
    return by_category, multi_category


def _section_order(by_category: dict[int | None, list[Transaction]]) -> list[int | None]:
    """Return category ids in ascending order, with uncategorized (None) last."""
    ordered: list[int | None] = sorted(c for c in by_category if c is not None)
    if None in by_category:
        ordered.append(None)
    return ordered


def _write_header(writer, statement: Statement) -> None:
    if statement.statement_date:
        date_cell = " " + statement.statement_date.strftime("%Y-%m-%d")
        writer.writerow([_STATEMENT_DATE_HEADER, date_cell])
        writer.writerow([])
    writer.writerow(_COLUMN_HEADER)


def _write_credits(writer, credits: list[Credit]) -> None:
    for credit in credits:
        writer.writerow(
            [
                credit.date.strftime("%m/%d"),
                f"{-credit.cost:.2f}",
                credit.remark,
                credit.kind,
                credit.source or "",
            ]
        )
    if credits:
        writer.writerow([])


def _write_transactions(
    writer, transactions: list[Transaction], row_number: int
) -> tuple[float, int]:
    """Write transactions sorted by remark then date; return (subtotal, next row number)."""
    subtotal = 0.0
    for transaction in sorted(transactions, key=lambda t: (t.remark.lower(), t.date)):
        row_number += 1
        category_cell: object = transaction.category
        if transaction.is_multi_category:
            category_cell = ",".join(str(c) for c in transaction.category)
        writer.writerow(
            [
                format_expense_row(
                    row_number,
                    transaction.date,
                    transaction.cost,
                    transaction.remark,
                    category_cell,
                    transaction.source,
                )
            ]
        )
        subtotal += transaction.cost
    return subtotal, row_number
