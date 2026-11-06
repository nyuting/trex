"""Extracting transaction lines from a UOB statement (several cards in one PDF)."""

from __future__ import annotations

import re

from trex.extract.pdf import PageRow, format_csv_line, split_off_header
from trex.log import get_logger

logger = get_logger(__name__)

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
#: Start of the UNI$ row in the Rewards Summary table; notes that mention UNI$ don't start so.
_REWARDS_ROW_PREFIX = f"{REWARDS_LABEL} - "
#: The statement's UNI$ row in the Rewards Summary table: previous, earned, used,
#: adjustment, current, then the expiring amount and its date, which are dropped
#: and may be blank.
REWARDS_RE = re.compile(
    rf"^{re.escape(_REWARDS_ROW_PREFIX)}({_POINTS}) ({_POINTS}) ({_POINTS}) ({_POINTS})"
    rf" ({_POINTS})(?: {_POINTS} \S+)?$"
)

PREVIOUS_BALANCE = "PREVIOUS BALANCE"


def extract_uob_lines(rows: list[PageRow]) -> list[str]:
    """Return CSV lines for the transactions in a UOB statement.

    Emits the statement date first. Card section headers follow, so the
    parser knows which card each transaction belongs to; they are deduplicated
    where a section continues onto the next page. Each section carries its
    ``PREVIOUS BALANCE`` line and ends with its ``TOTAL BALANCE FOR`` line.
    The statement-wide UNI$ rewards row, when printed, is emitted once as
    ``UNI$,<previous>,<earned>,<used>,<adjustment>,<current>``. A UNI$ row in
    any other layout is emitted as a bare ``UNI$``, so the parse fails its
    balance check rather than silently skipping the rewards line.
    """
    statement_date, rows = split_off_header(rows, STATEMENT_DATE_RE)
    lines = [format_csv_line([STATEMENT_DATE_LABEL, statement_date.group(1)])]
    last_section: str | None = None
    seen_rewards = False
    #: Index of the bare UNI$ line emitted for an unreadable row, which a
    #: readable one on a later page replaces.
    placeholder: int | None = None

    for row in rows:
        text = f"{row.text} {row.amount}".rstrip()
        rewards = REWARDS_RE.match(text)
        if rewards:
            line = format_csv_line([REWARDS_LABEL, *rewards.groups()])
            if placeholder is not None:
                lines[placeholder] = line
                placeholder = None
            elif not seen_rewards:
                lines.append(line)
            seen_rewards = True
            continue
        if text.startswith(_REWARDS_ROW_PREFIX):
            if not seen_rewards:
                # Log only the cell count: the row holds the points figures.
                logger.warning(
                    "UNI$ row not in the expected layout (%d cells)", len(text.split()) - 2
                )
                placeholder = len(lines)
                lines.append(format_csv_line([REWARDS_LABEL]))
                seen_rewards = True
            continue

        if SECTION_TOTAL_RE.match(row.text):
            lines.append(format_csv_line([row.text, row.amount]))
            last_section = None
            continue

        # A card header row carries no amount of its own.
        if CARD_SECTION_RE.match(row.text) and not row.amount:
            if row.text != last_section:
                lines.append(format_csv_line([row.text]))
                last_section = row.text
            continue

        if row.text == PREVIOUS_BALANCE:
            lines.append(format_csv_line(["", "", PREVIOUS_BALANCE, row.amount]))
            continue

        if REFERENCE_RE.match(row.text):
            lines.append(format_csv_line(["", "", row.text, ""]))
            continue

        transaction = TRANSACTION_RE.match(row.text)
        if transaction:
            lines.append(format_csv_line([*transaction.groups(), row.amount]))
    return lines
