"""Extracting transaction lines from a Chase credit-card statement."""

from __future__ import annotations

import re

from trex.extract.pdf import PageRow, find_in_cells, format_csv_line, iter_rows
from trex.log import get_logger

logger = get_logger(__name__)

#: The statement period, e.g. "12/15/25 - 01/14/26"; the closing date ends it.
PERIOD_RE = re.compile(r"(\d{2}/\d{2}/\d{2}\s+-\s+\d{2}/\d{2}/\d{2})$")
#: A foreign-currency line: date then currency name, with no amount of its own.
CURRENCY_RE = re.compile(r"^(\d{2}/\d{2})\s+([A-Z][A-Z\s]*)$")
#: The exchange-rate line that follows a foreign-currency transaction.
EXCHANGE_RATE_RE = re.compile(r"^[\d,.]+\s+X\s+[\d.]+\s+\(EXCHG RATE\)$")
#: An ordinary transaction: date then merchant.
TRANSACTION_RE = re.compile(r"^(\d{2}/\d{2})\s+(.+)$")

ACTIVITY_START = "Transaction Merchant"
ACTIVITY_END_MARKERS = ("Totals Year-to-Date",)
INTEREST_SECTION = "INTEREST CHARGES"


def extract_chase_lines(pages: list[list[PageRow]]) -> list[str]:
    """Return CSV lines for the transactions in a Chase statement.

    Emits the statement period first, then one line per transaction inside the
    account-activity section. Credits (negative amounts) are logged and skipped.
    """
    lines: list[str] = []
    seen_period = False
    in_activity = False

    for cells, description, amount in iter_rows(pages):
        if amount == description:
            amount = ""

        if not seen_period:
            period = find_in_cells(cells, PERIOD_RE)
            if period:
                lines.append(format_csv_line([period.group(1)]))
                seen_period = True
                continue

        if description.startswith(ACTIVITY_START):
            in_activity = True
            continue
        if _ends_activity(description):
            in_activity = False
            continue
        if not in_activity:
            continue

        line = _transaction_line(description, amount)
        if line is not None:
            lines.append(line)
    return lines


def _ends_activity(description: str) -> bool:
    """True at the year-to-date totals or the interest section, which follow activity."""
    return (
        any(marker in description for marker in ACTIVITY_END_MARKERS)
        or description == INTEREST_SECTION
    )


def _transaction_line(description: str, amount: str) -> str | None:
    """Return the CSV line for one activity row, or None if it carries no data."""
    currency = CURRENCY_RE.match(description)
    if currency and not amount:
        # The date and the currency that follows it are separate PDF columns;
        # downstream CSVs have always had them unspaced.
        return format_csv_line(["", currency.group(1) + currency.group(2), ""])

    if EXCHANGE_RATE_RE.match(description) and not amount:
        return format_csv_line(["", description, ""])

    if amount.startswith("-"):
        logger.info("  [skip credit] %s %s", description, amount)
        return None

    transaction = TRANSACTION_RE.match(description)
    if transaction and amount:
        return format_csv_line([transaction.group(1), transaction.group(2), amount])
    return None
