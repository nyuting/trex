"""Reading parsed statements back as dated rows, and converting their amounts to SGD."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TypeVar

from trex.config import EXCHANGE_RATE_USD2SGD, get_parsed_dir
from trex.constants import CATEGORIES, USD_CARD
from trex.log import get_logger
from trex.models import (
    Credit,
    ParsedCreditRow,
    ParsedExpenseRow,
    Transaction,
    category_from_ids,
)
from trex.parse.rows import parse_row_date
from trex.serialize import (
    ROW_DATE_FORMAT,
    read_parsed_credits,
    read_parsed_expenses,
    read_parsed_statement_date,
)

logger = get_logger(__name__)

ParsedRow = TypeVar("ParsedRow", ParsedExpenseRow, ParsedCreditRow)


@dataclass(frozen=True)
class SummaryRow:
    """One expense line reduced to what the summary needs."""

    month: int
    cost: float
    card: str
    category_ids: list[int]
    #: the transaction's own date and description, enough to recognise it twice over
    date: str = ""
    remark: str = ""
    #: the parsed file it was read from
    statement: str = ""


def read_year_expenses(year: int, parsed_dir: Path | None = None) -> list[SummaryRow]:
    """Return every expense across all parsed statements for `year`.

    Categories the current `CATEGORIES` doesn't know are dropped, so a row
    nothing claims comes back with an empty `category_ids` — callers must
    account for it rather than let it vanish.
    """
    known_categories = set(CATEGORIES)
    rows = []
    for path, date, row in _rows_dated_in(year, parsed_dir, read_parsed_expenses):
        category_ids = [c for c in row.category_ids if c in known_categories]
        rows.append(
            SummaryRow(
                date.month, row.cost, row.card, category_ids, row.date, row.remark, path.name
            )
        )
    return rows


def read_year_credits(year: int, parsed_dir: Path | None = None) -> list[Credit]:
    """Return every credit — payments, refunds, rebates — dated in `year`, as printed.

    Chase amounts are still in USD.
    """
    return [
        Credit(date, row.cost, row.remark, row.kind, row.card or None)
        for _, date, row in _rows_dated_in(year, parsed_dir, read_parsed_credits)
    ]


def read_dated_expenses(parsed_dir: Path | None = None) -> Iterator[tuple[datetime, float, str]]:
    """Yield (full date, cost in SGD, remark) for every expense in every parsed statement."""
    for _, date, row in _rows_dated_in(None, parsed_dir, read_parsed_expenses):
        yield date, to_sgd(row.cost, row.card), row.remark


def _rows_dated_in(
    year: int | None, parsed_dir: Path | None, read_rows: Callable[[Path], list[ParsedRow]]
) -> Iterator[tuple[Path, datetime, ParsedRow]]:
    """Yield (file, full date, row) per row `read_rows` finds in a parsed file, dated in `year`.

    A `year` of None yields rows from every year.

    Each row's year comes from its file's statement date, so a December
    transaction on a January statement lands in December.
    """
    for path in sorted((parsed_dir or get_parsed_dir()).glob("*.csv")):
        statement_date = read_parsed_statement_date(path)
        for row in read_rows(path):
            try:
                date = parse_row_date(row.date, ROW_DATE_FORMAT, statement_date)
            except ValueError:
                logger.debug("%s: skipping row with unparsable date %r", path, row.date)
                continue
            if year is None or date.year == year:
                yield path, date, row


def to_sgd(cost: float, card: str) -> float:
    """Convert a transaction cost to SGD based on the card it was made on."""
    return cost * EXCHANGE_RATE_USD2SGD if card == USD_CARD else cost


def as_sgd_transaction(year: int, row: SummaryRow) -> Transaction:
    """Return a summary row as a Transaction in SGD, dated in `year`."""
    return Transaction(
        datetime.strptime(f"{year}/{row.date}", f"%Y/{ROW_DATE_FORMAT}"),
        to_sgd(row.cost, row.card),
        remark_in_sgd(row.remark, row.cost, row.card),
        category_from_ids(row.category_ids),
        row.card or None,
    )


def remark_in_sgd(remark: str, cost: float, card: str) -> str:
    """Return `remark`, noting the original amount when `to_sgd` converts it."""
    return f"{remark} [USD {cost:.2f}]" if card == USD_CARD else remark
