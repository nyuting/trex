"""Each statement's spending total, by month and card, recorded once and then guarded.

``statement_totals<year>.csv`` holds one row per month and one column per card,
each cell the spending on that card's statement for that month, in the card's
own currency (Chase also gets an SGD column). Once a cell is recorded it is not
silently overwritten: a later run that computes a different total reports it
and keeps the recorded value until the change is accepted. A total that moves
means a row was lost, duplicated or re-priced since it was checked.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from trex.cells import format_amount
from trex.config import get_extracted_dir, get_parsed_dir, get_summary_dir
from trex.constants import CARDS_BY_ID, USD_CARD
from trex.log import get_logger
from trex.models import to_cents
from trex.parse.balance import BalanceMismatch, check_card_totals
from trex.parse.files import parse_extracted_csv
from trex.parse.rows import detect_issuer
from trex.serialize import read_parsed_expenses, read_parsed_statement_date
from trex.summary import MONTHS_IN_YEAR, to_sgd

logger = get_logger(__name__)

#: The month a statement covers, as its short name gives it: ``Chase01``, ``PLY1201``.
NAME_MONTH_RE = re.compile(r"^[A-Za-z]+(\d{2})")
#: Card columns, in card-id order; Chase's holds USD.
CARD_COLUMNS = tuple(
    f"{label} (USD)" if label == USD_CARD else label for label in CARDS_BY_ID.values()
)
USD_SGD_COLUMN = f"{USD_CARD} (SGD)"
TOTAL_COLUMN = "Total (SGD)"

#: (month, card label) -> total in the card's own currency
Totals = dict[tuple[int, str], float]


@dataclass(frozen=True)
class TotalChange:
    """A recorded total that the parsed statements no longer add up to."""

    month: int
    card: str
    recorded: float
    #: None when no parsed statement supplies this cell any more
    current: float | None

    def describe(self) -> str:
        """Return a one-line explanation suitable for a warning."""
        now = "no statement" if self.current is None else f"{self.current:.2f}"
        return f"{self.card} {self.month:02d}: recorded {self.recorded:.2f}, now {now}"


def statement_totals_name(year: int) -> str:
    """Return the totals filename for a year, e.g. ``statement_totals2026.csv``."""
    return f"statement_totals{year}.csv"


def statement_year_month(name: str, statement_date: datetime) -> tuple[int, int]:
    """Return the (year, month) a statement covers.

    The month is the first two digits of the short name (``Chase01`` -> 1,
    ``PLY1201`` -> 12), which is how the files are labelled even where the
    statement closes early the following month. The year is the closing date's,
    less one when the named month falls after the closing month. A name with no
    month in it falls back to the closing date.
    """
    match = NAME_MONTH_RE.match(name)
    month = int(match.group(1)) if match else 0
    if not 1 <= month <= MONTHS_IN_YEAR:
        return statement_date.year, statement_date.month
    year = statement_date.year - 1 if month > statement_date.month else statement_date.year
    return year, month


def compute_statement_totals(year: int, parsed_dir: Path | None = None) -> Totals:
    """Return every card's spending per statement month of `year`, from the parsed CSVs.

    Two statements landing in the same cell are added together, with a
    warning, since that usually means a stray copy.
    """
    parsed_dir = parsed_dir or get_parsed_dir()
    totals: Totals = {}
    owners: dict[tuple[int, str], str] = {}
    for path in sorted(parsed_dir.glob("*.csv")):
        statement_date = read_parsed_statement_date(path)
        if statement_date is None:
            logger.warning("%s: no statement date, left out of the statement totals", path.name)
            continue
        statement_year, month = statement_year_month(path.stem, statement_date)
        if statement_year != year:
            continue
        by_card: dict[str, float] = {}
        for row in read_parsed_expenses(path):
            by_card[row.card] = by_card.get(row.card, 0.0) + row.cost
        for card, cost in by_card.items():
            if card not in CARDS_BY_ID.values():
                logger.warning("%s: card %r has no column in the statement totals", path.name, card)
                continue
            key = (month, card)
            if key in totals:
                logger.warning(
                    "%s and %s both give %s for %02d; adding them",
                    owners[key],
                    path.name,
                    card,
                    month,
                )
            totals[key] = totals.get(key, 0.0) + cost
            owners[key] = path.name
    return totals


def read_statement_totals(path: Path) -> Totals:
    """Return the card-currency cells of a totals CSV; {} when the file doesn't exist.

    The SGD and total columns are derived, so they are not read back.
    """
    if not path.exists():
        return {}
    totals: Totals = {}
    with open(path) as handle:
        for row in csv.DictReader(handle):
            month = int(row["month"])
            for label, column in zip(CARDS_BY_ID.values(), CARD_COLUMNS, strict=True):
                cell = (row.get(column) or "").strip()
                if cell:
                    totals[(month, label)] = float(cell)
    return totals


def write_statement_totals(totals: Totals, path: Path) -> None:
    """Write a totals CSV: a row per month, a column per card, plus Chase in SGD and a total.

    Cells with no statement are left blank.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(
            ["month", *CARD_COLUMNS[:1], USD_SGD_COLUMN, *CARD_COLUMNS[1:], TOTAL_COLUMN]
        )
        for month in range(1, MONTHS_IN_YEAR + 1):
            cells = [totals.get((month, label)) for label in CARDS_BY_ID.values()]
            usd = cells[0]
            sgd = [
                to_sgd(cost, label)
                for cost, label in zip(cells, CARDS_BY_ID.values(), strict=True)
                if cost is not None
            ]
            writer.writerow(
                [
                    f"{month:02d}",
                    format_amount(usd),
                    format_amount(None if usd is None else to_sgd(usd, USD_CARD)),
                    *(format_amount(cost) for cost in cells[1:]),
                    format_amount(sum(sgd) if sgd else None),
                ]
            )


def update_statement_totals(
    year: int | None = None,
    accept: bool = False,
    parsed_dir: Path | None = None,
    summary_dir: Path | None = None,
) -> list[TotalChange]:
    """Record new statement totals for `year` and return the recorded ones that changed.

    A cell not yet in ``statement_totals<year>.csv`` is added. A recorded cell
    whose statement now adds up differently, or has gone, is returned and keeps
    its recorded value, unless `accept` is set, in which case the new value
    replaces it. Each change is logged as a warning. The file is rewritten
    either way, with the SGD columns recomputed at the current exchange rate.
    """
    year = year if year is not None else datetime.now().year
    path = (summary_dir or get_summary_dir()) / statement_totals_name(year)
    recorded = read_statement_totals(path)
    current = {
        key: round(cost, 2) for key, cost in compute_statement_totals(year, parsed_dir).items()
    }

    changes = [
        TotalChange(month, card, cost, current.get((month, card)))
        for (month, card), cost in sorted(recorded.items())
        if to_cents(current.get((month, card))) != to_cents(cost)
    ]
    merged = dict(current) if accept else {**current, **recorded}
    write_statement_totals({key: cost for key, cost in merged.items() if cost is not None}, path)

    for change in changes:
        if accept:
            logger.warning("statement total accepted: %s", change.describe())
        else:
            logger.warning(
                "statement total changed: %s (kept; --accept-totals to take it)",
                change.describe(),
            )
    logger.info("wrote %s", path)
    return changes


def check_statement_balances(
    year: int | None = None, extracted_dir: Path | None = None
) -> dict[str, list[BalanceMismatch]]:
    """Run the balance check on every extracted statement of `year`; return the failures.

    Each statement is logged as OK, as having no printed balances, or as failed
    with one warning per card that doesn't add up. The result maps each failed
    statement's short name to its mismatches. Nothing is categorized or written.
    """
    year = year if year is not None else datetime.now().year
    extracted_dir = extracted_dir or get_extracted_dir()
    failures: dict[str, list[BalanceMismatch]] = {}
    logger.info("balance check, %d:", year)
    for path in sorted(extracted_dir.glob("*.csv")):
        name = path.stem
        try:
            detect_issuer(name)
        except ValueError:
            continue
        statement = parse_extracted_csv(name, extracted_dir, rules=None)
        if statement.statement_date is None:
            logger.warning("  %s: no statement date, left out of the balance check", name)
            continue
        if statement_year_month(name, statement.statement_date)[0] != year:
            continue
        if not statement.balances:
            logger.info("  %s: no printed balances", name)
            continue
        mismatches = check_card_totals(statement)
        if not mismatches:
            logger.info("  %s: OK", name)
            continue
        failures[name] = mismatches
        for mismatch in mismatches:
            logger.warning("  %s: FAIL %s", name, mismatch.describe())
    return failures
