"""Reading individual rows of an extracted CSV: dates, costs, statement dates."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from trex.constants import ISSUER_CHASE, ISSUER_PAYLAH, ISSUER_UOB
from trex.log import get_logger

logger = get_logger(__name__)

#: Chase's period header, e.g. "12/15/25 - 01/14/26"; the closing date is group 1.
CHASE_PERIOD_RE = re.compile(r"\s*\d{2}/\d{2}/\d{2}\s*-\s*(\d{2}/\d{2}/\d{2})\s*$")


@dataclass(frozen=True)
class RawRow:
    """One transaction read off an extracted CSV, before categorization."""

    date: datetime
    cost: float
    remark: str
    is_credit: bool = False


def detect_issuer(name: str) -> str:
    """Return the issuer family for a statement short name (e.g. ``UOB05``)."""
    upper = name.upper()
    if upper.startswith("CHASE"):
        return ISSUER_CHASE
    if upper.startswith(("PLY", "PLG")):
        return ISSUER_PAYLAH
    if upper.startswith("UOB"):
        return ISSUER_UOB
    raise ValueError(f"unknown statement source for name: {name!r}")


def parse_cost(text: str) -> tuple[float, bool]:
    """Return (amount, is_credit) for an amount cell such as ``"1,234.56 CR"``."""
    text = text.replace(",", "")
    is_credit = text.endswith("CR")
    if is_credit:
        text = text[:-2]
    return float(text), is_credit


def adjust_year(date: datetime, statement_date: datetime | None) -> datetime:
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


def parse_statement_date(first_row: list[str] | None, issuer: str) -> datetime | None:
    """Return the statement's closing date from the first extracted row, or None.

    Each issuer states it differently: UOB labels it, Chase gives a period whose
    end date is used, PayLah writes it bare.
    """
    if not first_row:
        return None
    try:
        if issuer == ISSUER_UOB and len(first_row) >= 2 and first_row[0] == "Statement Date":
            return datetime.strptime(re.sub(r"\s+", " ", first_row[1].strip()), "%d %b %Y")
        if issuer == ISSUER_CHASE:
            period = CHASE_PERIOD_RE.match(first_row[0])
            if period:
                return datetime.strptime(period.group(1), "%m/%d/%y")
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
    """Read a Chase row: ``date,merchant,amount``."""
    if len(fields) != 3:
        return None
    try:
        date = datetime.strptime(fields[0], "%m/%d")
        cost = float(fields[2])
    except ValueError:
        logger.debug("skipping unparsable Chase row %r", fields)
        return None
    return RawRow(adjust_year(date, statement_date), cost, fields[1])


def parse_paylah_row(fields: list[str], statement_date: datetime | None) -> RawRow | None:
    """Read a PayLah row: ``date,description,amount,CR|DB``."""
    if len(fields) != 4:
        return None
    try:
        date = datetime.strptime(fields[0], "%d %b")
        cost, _ = parse_cost(fields[2])
    except ValueError:
        logger.debug("skipping unparsable PayLah row %r", fields)
        return None
    return RawRow(adjust_year(date, statement_date), cost, fields[1], fields[3].strip() == "CR")


def parse_uob_row(fields: list[str], statement_date: datetime | None) -> RawRow | None:
    """Read a UOB row: ``posting date,transaction date,description,amount``."""
    if len(fields) != 4:
        return None
    try:
        date = datetime.strptime(fields[1], "%d %b")
        cost, is_credit = parse_cost(fields[3])
    except ValueError:
        logger.debug("skipping unparsable UOB row %r", fields)
        return None
    return RawRow(adjust_year(date, statement_date), cost, fields[2], is_credit)
