"""Extracting transaction lines from a UOB statement (several cards in one PDF)."""

from __future__ import annotations

import re

from trex.extract.pdf import PageRow, find_in_cells, format_csv_line, iter_rows

#: The statement date in the page header.
STATEMENT_DATE_RE = re.compile(r"^Statement Date\s+(.+)$")
#: A card section header, e.g. "UOB ONE CARD" — one UOB PDF holds several cards.
CARD_SECTION_RE = re.compile(r"^[A-Z][A-Z\s']+ (AMEX|VISA|CARD)$")
#: The closing balance line for a card section.
SECTION_TOTAL_RE = re.compile(r"^TOTAL BALANCE FOR .+$")
#: A reference line belonging to the transaction above it.
REFERENCE_RE = re.compile(r"^Ref No\. : .+$")
#: A transaction: posting date, transaction date, then description.
TRANSACTION_RE = re.compile(r"^(\d{2}\s\w{3})\s(\d{2}\s\w{3})\s(.+)$")

PREVIOUS_BALANCE = "PREVIOUS BALANCE"


def extract_uob_lines(pages: list[list[PageRow]]) -> list[str]:
    """Return CSV lines for the transactions in a UOB statement.

    Card section headers are emitted so the parser knows which card each
    following transaction belongs to, and are deduplicated where a section
    continues onto the next page.
    """
    lines: list[str] = []
    seen_statement_date = False
    last_section: str | None = None

    for cells, description, amount in iter_rows(pages):
        if not seen_statement_date:
            statement_date = find_in_cells(cells, STATEMENT_DATE_RE)
            if statement_date:
                lines.append(format_csv_line(["Statement Date", statement_date.group(1)]))
                seen_statement_date = True
                continue

        if SECTION_TOTAL_RE.match(description):
            lines.append(format_csv_line([description, amount]))
            last_section = None
            continue

        if _is_card_section(description, amount):
            if description != last_section:
                lines.append(format_csv_line([description]))
                last_section = description
            continue

        if description == PREVIOUS_BALANCE:
            lines.append(format_csv_line(["", "", PREVIOUS_BALANCE, amount]))
            continue

        if REFERENCE_RE.match(description):
            lines.append(format_csv_line(["", "", description, ""]))
            continue

        transaction = TRANSACTION_RE.match(description)
        if transaction:
            lines.append(format_csv_line([*transaction.groups(), amount]))
    return lines


def _is_card_section(description: str, amount: str) -> bool:
    """True for a card header row, which carries no amount of its own."""
    return bool(CARD_SECTION_RE.match(description)) and (not amount or amount == description)
