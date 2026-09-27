"""Reading individual rows of an extracted CSV: dates, costs, statement dates."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from trex.constants import ISSUER_CHASE, ISSUER_PAYLAH, ISSUER_UOB
from trex.extract import detect_issuer as detect_issuer  # re-exported for parse callers
from trex.extract.chase import PERIOD_DATE_FORMAT, PERIOD_RE
from trex.extract.uob import STATEMENT_DATE_LABEL
from trex.log import get_logger

logger = get_logger(__name__)

#: Placeholder year for reading a row's day and month before its real year is known.
LEAP_YEAR = 2000


@dataclass(frozen=True)
class RawRow:
    """One transaction read off an extracted CSV, before categorization."""

    date: datetime
    cost: float
    remark: str
    is_credit: bool = False


def parse_cost(text: str) -> tuple[float, bool]:
    """Return (amount, is_credit) for an amount cell such as ``"1,234.56 CR"``."""
    text = text.replace(",", "")
    is_credit = text.endswith("CR")
    if is_credit:
        text = text[:-2]
    return float(text), is_credit


def parse_points(text: str) -> float:
    """Return a rewards-points cell such as ``"1,234.56-"`` as a signed amount.

    UOB prints a negative UNI$ figure with a trailing minus. Raises ValueError
    for a cell that is not a number.
    """
    text = text.strip().replace(",", "")
    if text.endswith("-"):
        return -float(text[:-1])
    return float(text)


def date_within_statement_year(date: datetime, statement_date: datetime | None) -> datetime:
    """Return `date` with the year that puts it on or before `statement_date`.

    Statement rows carry only day and month, so a December transaction on a
    January statement belongs to the previous year. With no statement date to
    anchor to, the current year is assumed.
    """
    if statement_date is None:
        return date.replace(year=datetime.now().year)
    candidate = date.replace(year=statement_date.year)
    if candidate > statement_date:
        candidate = candidate.replace(year=statement_date.year - 1)
    return candidate


def read_row_date(text: str, fmt: str, statement_date: datetime | None) -> datetime:
    """Return a row's day-and-month `text` dated to its statement.

    See `date_within_statement_year` for how the year is chosen.

    The day and month are read against a leap year, so 29 Feb parses; strptime's
    default year, 1900, is not one. Raises ValueError for an unparsable date, or
    for 29 Feb when the year it lands in is not a leap year.
    """
    date = datetime.strptime(f"{text} {LEAP_YEAR}", f"{fmt} %Y")
    return date_within_statement_year(date, statement_date)


def parse_statement_date(first_row: list[str] | None, issuer: str) -> datetime | None:
    """Return the statement's closing date from the first extracted row, or None.

    Each issuer states it differently: UOB labels it, Chase gives a period whose
    end date is used, PayLah writes it bare.
    """
    if not first_row:
        return None
    try:
        if issuer == ISSUER_UOB and len(first_row) >= 2 and first_row[0] == STATEMENT_DATE_LABEL:
            return datetime.strptime(re.sub(r"\s+", " ", first_row[1].strip()), "%d %b %Y")
        if issuer == ISSUER_CHASE:
            period = PERIOD_RE.fullmatch(first_row[0].strip())
            if period:
                return datetime.strptime(period.group(2), PERIOD_DATE_FORMAT)
        if issuer == ISSUER_PAYLAH:
            return datetime.strptime(first_row[0].strip(), "%d %b %Y")
    except ValueError:
        logger.debug("no statement date in first row %r (%s)", first_row, issuer)
        return None
    return None


# --- per-issuer row readers ---------------------------------------------
#
# Each returns a RawRow, or None when the line is not a transaction (a section
# header, a reference line, a malformed row). Returning None is normal and
# expected — extracted CSVs interleave transactions with structural rows.


def parse_chase_row(fields: list[str], statement_date: datetime | None) -> RawRow | None:
    """Read a Chase row: ``date,merchant,amount``; a negative amount is a credit."""
    if len(fields) != 3:
        return None
    try:
        date = read_row_date(fields[0], "%m/%d", statement_date)
        cost, _ = parse_cost(fields[2])
    except ValueError:
        logger.debug("skipping unparsable Chase row %r", fields)
        return None
    return RawRow(date, abs(cost), fields[1], cost < 0)


def parse_paylah_row(fields: list[str], statement_date: datetime | None) -> RawRow | None:
    """Read a PayLah row: ``date,description,amount,CR|DB``."""
    if len(fields) != 4:
        return None
    try:
        date = read_row_date(fields[0], "%d %b", statement_date)
        cost, _ = parse_cost(fields[2])
    except ValueError:
        logger.debug("skipping unparsable PayLah row %r", fields)
        return None
    return RawRow(date, cost, fields[1], fields[3].strip() == "CR")


def parse_uob_row(fields: list[str], statement_date: datetime | None) -> RawRow | None:
    """Read a UOB row: ``posting date,transaction date,description,amount``."""
    if len(fields) != 4:
        return None
    try:
        date = read_row_date(fields[1], "%d %b", statement_date)
        cost, is_credit = parse_cost(fields[3])
    except ValueError:
        logger.debug("skipping unparsable UOB row %r", fields)
        return None
    return RawRow(date, cost, fields[2], is_credit)
