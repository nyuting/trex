"""Extracting transaction lines from a DBS PayLah statement (PLG / PLY)."""

from __future__ import annotations

import re

from trex.extract.pdf import PageRow, find_in_cells, format_csv_line, iter_rows

#: The statement date at the top of the first page, e.g. "31 Jan 2026".
STATEMENT_DATE_RE = re.compile(r"^(\d{2}\s\w{3}\s\d{4})\b")
#: A reference line belonging to the transaction above it.
REFERENCE_RE = re.compile(r"^REF NO:\.\s.+$")
#: An amount cell ending in its credit/debit indicator.
AMOUNT_RE = re.compile(r"^(.+?)\s+(CR|DB)$")
#: A transaction's date and description, e.g. "05 Jan ACME COFFEE".
DATE_AND_DESCRIPTION_RE = re.compile(r"^(\d{2}\s\w{3})\s+(.+)$")


def extract_paylah_lines(
    pages: list[list[PageRow]], include_statement_date: bool = True
) -> list[str]:
    """Return CSV lines for the transactions in a PayLah statement.

    Each transaction becomes ``date,description,amount,CR|DB``; reference lines
    are kept as their own row so the parser can attach them to the transaction
    above.
    """
    lines: list[str] = []
    seen_statement_date = False

    for cells, _description, _amount in iter_rows(pages):
        if include_statement_date and not seen_statement_date:
            statement_date = find_in_cells(cells, STATEMENT_DATE_RE)
            if statement_date:
                lines.append(format_csv_line([statement_date.group(1)]))
                seen_statement_date = True
                continue

        reference = find_in_cells(cells, REFERENCE_RE)
        if reference:
            lines.append(format_csv_line(["", reference.group(0), "", ""]))
            continue

        line = _transaction_line(cells)
        if line is not None:
            lines.append(line)
    return lines


def _transaction_line(cells: list[str]) -> str | None:
    """Return the CSV line for a transaction row, or None if the row is not one."""
    amount_match = AMOUNT_RE.match(cells[-1])
    if not amount_match:
        return None
    amount, indicator = amount_match.groups()

    date_match = DATE_AND_DESCRIPTION_RE.match(" ".join(cells[:-1]))
    if not date_match:
        return None
    date, description = date_match.groups()
    return format_csv_line([date, description, amount, indicator])
