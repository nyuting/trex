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
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from trex.cells import format_amount
from trex.config import get_parsed_dir, get_summary_dir
from trex.constants import CARDS_BY_ID, USD_CARD
from trex.log import get_logger
from trex.models import to_cents
from trex.parse import statement_year_month
from trex.serialize import read_parsed_expenses, read_parsed_statement_date
from trex.summary.rows import to_sgd
from trex.summary.table import MONTHS_IN_YEAR

logger = get_logger(__name__)

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
    year = year or datetime.now().year
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
            sgd = [
                None if cost is None else to_sgd(cost, label)
                for cost, label in zip(cells, CARDS_BY_ID.values(), strict=True)
            ]
            known = [cost for cost in sgd if cost is not None]
            amounts = [cells[0], sgd[0], *cells[1:], sum(known) if known else None]
            writer.writerow([f"{month:02d}", *map(format_amount, amounts)])
