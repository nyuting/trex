"""Extracting transaction lines from a Chase credit-card statement."""

from __future__ import annotations

import re

from trex.extract.pdf import PageRow, find_in_cells, format_csv_line, iter_row_cells

#: The statement period, e.g. "12/15/25 - 01/14/26": opening date, closing date.
PERIOD_RE = re.compile(r"(\d{2}/\d{2}/\d{2})\s*-\s*(\d{2}/\d{2}/\d{2})$")
PERIOD_DATE_FORMAT = "%m/%d/%y"
#: A foreign-currency line: date then currency name, with no amount of its own.
CURRENCY_RE = re.compile(r"^(\d{2}/\d{2})\s+([A-Z][A-Z\s]*)$")
#: The exchange-rate line that follows a foreign-currency transaction.
EXCHANGE_RATE_RE = re.compile(r"^[\d,.]+\s+X\s+[\d.]+\s+\(EXCHG RATE\)$")
#: An ordinary transaction: date then merchant.
TRANSACTION_RE = re.compile(r"^(\d{2}/\d{2})\s+(.+)$")

PREVIOUS_BALANCE = "Previous Balance"
NEW_BALANCE = "New Balance"
#: A line of the account summary on page 1, e.g. "Payment, Credits -$6,514.90".
ACCOUNT_SUMMARY_RE = re.compile(
    r"^(Previous Balance|Payment, Credits|Purchases|Cash Advances|Balance Transfers"
    r"|Fees Charged|Interest Charged|New Balance) ([-+]?)\$([\d,]+\.\d{2})$"
)

ACTIVITY_START = "Transaction Merchant"
ACTIVITY_END_MARKERS = ("Totals Year-to-Date",)
INTEREST_SECTION = "INTEREST CHARGES"


def extract_chase_lines(pages: list[list[PageRow]]) -> list[str]:
    """Return CSV lines for the transactions in a Chase statement.

    Emits the statement period first, then the account summary as
    ``label,amount`` lines (``Previous Balance`` through ``New Balance``), then
    one line per transaction inside the account-activity section. Credits keep
    their minus sign.
    """
    lines: list[str] = []
    summary: dict[str, str] = {}
    seen_period = False
    in_activity = False

    for cells, description, amount in iter_row_cells(pages):
        if not seen_period:
            period = find_in_cells(cells, PERIOD_RE)
            if period:
                lines.append(format_csv_line([period.group(0)]))
                seen_period = True
                continue

        summary_line = ACCOUNT_SUMMARY_RE.match(description)
        if summary_line:
            label, sign, amount_text = summary_line.groups()
            summary.setdefault(label, format_csv_line([label, sign.lstrip("+") + amount_text]))
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

    # The summary precedes the period on the page, but the period must stay row 0.
    at = 1 if seen_period else 0
    lines[at:at] = summary.values()
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

    transaction = TRANSACTION_RE.match(description)
    if transaction and amount:
        return format_csv_line([transaction.group(1), transaction.group(2), amount])
    return None
