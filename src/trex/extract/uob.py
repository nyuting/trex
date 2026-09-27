"""Extracting transaction lines from a UOB statement (several cards in one PDF)."""

from __future__ import annotations

import re

from trex.extract.pdf import PageRow, find_in_cells, format_csv_line, iter_row_cells

#: Label of the statement date, in the page header and in the extracted CSV.
STATEMENT_DATE_LABEL = "Statement Date"
#: Start of the closing balance line for a card section; the card header follows it.
SECTION_TOTAL_PREFIX = "TOTAL BALANCE FOR "

#: The statement date in the page header.
STATEMENT_DATE_RE = re.compile(rf"^{STATEMENT_DATE_LABEL}\s+(.+)$")
#: A card section header, e.g. "UOB ONE CARD" — one UOB PDF holds several cards.
CARD_SECTION_RE = re.compile(r"^[A-Z][A-Z\s']+ (AMEX|VISA|CARD)$")
#: The closing balance line for a card section.
SECTION_TOTAL_RE = re.compile(rf"^{SECTION_TOTAL_PREFIX}.+$")
#: A reference line belonging to the transaction above it.
REFERENCE_RE = re.compile(r"^Ref No\. : .+$")
#: A transaction: posting date, transaction date, then description.
TRANSACTION_RE = re.compile(r"^(\d{2}\s\w{3})\s(\d{2}\s\w{3})\s(.+)$")

#: First cell of the UNI$ rewards line, in the extracted CSV.
REWARDS_LABEL = "UNI$"
#: A UNI$ amount as printed: thousands separators, two decimals, a trailing minus if negative.
_POINTS = r"[\d,]+\.\d{2}-?"
#: The statement's UNI$ row in the Rewards Summary table: previous, earned, used,
#: adjustment, current, then the expiring amount and its date, which are dropped.
REWARDS_RE = re.compile(
    rf"^{re.escape(REWARDS_LABEL)} - ({_POINTS}) ({_POINTS}) ({_POINTS}) ({_POINTS}) ({_POINTS})"
    rf" {_POINTS} \S+$"
)

PREVIOUS_BALANCE = "PREVIOUS BALANCE"


def extract_uob_lines(pages: list[list[PageRow]]) -> list[str]:
    """Return CSV lines for the transactions in a UOB statement.

    Emits the statement date first. Card section headers follow, so the
    parser knows which card each transaction belongs to; they are deduplicated
    where a section continues onto the next page. Each section carries its
    ``PREVIOUS BALANCE`` line and ends with its ``TOTAL BALANCE FOR`` line.
    The statement-wide UNI$ rewards row, when printed, is emitted once as
    ``UNI$,<previous>,<earned>,<used>,<adjustment>,<current>``.
    """
    lines: list[str] = []
    seen_statement_date = False
    last_section: str | None = None
    seen_rewards = False

    for cells, description, amount in iter_row_cells(pages):
        if not seen_statement_date:
            statement_date = find_in_cells(cells, STATEMENT_DATE_RE)
            if statement_date:
                lines.append(format_csv_line([STATEMENT_DATE_LABEL, statement_date.group(1)]))
                seen_statement_date = True
                continue

        rewards = REWARDS_RE.match(" ".join(cells))
        if rewards:
            if not seen_rewards:
                lines.append(format_csv_line([REWARDS_LABEL, *rewards.groups()]))
                seen_rewards = True
            continue

        if SECTION_TOTAL_RE.match(description):
            lines.append(format_csv_line([description, amount]))
            last_section = None
            continue

        # A card header row carries no amount of its own.
        if CARD_SECTION_RE.match(description) and not amount:
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
