"""Stage 3: consolidate every parsed statement into a category x month table."""

from __future__ import annotations

import calendar
import csv
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from itertools import combinations
from pathlib import Path

from trex.config import EXCHANGE_RATE_USD2SGD, get_parsed_dir, get_summary_dir
from trex.constants import CATEGORIES, HEALTHCARE_CATEGORY_ID, USD_CARD
from trex.log import get_logger
from trex.models import Credit, RowKey, Statement, Transaction, category_from_ids, row_key
from trex.parse.rows import date_within_statement_year
from trex.serialize import (
    read_parsed_credits,
    read_parsed_expenses,
    read_parsed_statement_date,
    write_parsed_csv,
)

logger = get_logger(__name__)

MONTHS_IN_YEAR = 12
CELL_WIDTH = 9
#: label of the row that carries spending no category claimed
UNCATEGORIZED_ROW_LABEL = "Uncategorized"
#: label of the row that carries spending filed under several categories
MULTI_CATEGORY_ROW_LABEL = "Multi-category"
MONTH_ABBREVIATIONS = tuple(calendar.month_abbr[month].lower() for month in range(1, 13))


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


@dataclass
class Summary:
    """A category x month table of spending, in SGD."""

    year: int
    #: category id -> 12 monthly totals
    totals: dict[int, list[float]] = field(default_factory=dict)
    #: spending that carries no known category, by month — excluded from every total below
    uncategorized: list[float] = field(default_factory=lambda: [0.0] * MONTHS_IN_YEAR)
    #: how many rows that uncategorized spending came from
    uncategorized_rows: int = 0
    #: spending filed under several categories, by month — also excluded from every total
    multi_category: list[float] = field(default_factory=lambda: [0.0] * MONTHS_IN_YEAR)
    #: how many rows that multi-category spending came from
    multi_category_rows: int = 0

    def row_total(self, category_id: int) -> float:
        """Return the year's total for one category."""
        return sum(self.totals[category_id])

    def month_total(self, month_index: int) -> float:
        """Return every category's total for one month (0-based index)."""
        return sum(values[month_index] for values in self.totals.values())

    def grand_total(self) -> float:
        """Return the total for the whole year."""
        return sum(sum(values) for values in self.totals.values())

    def uncategorized_total(self) -> float:
        """Return the year's spending that no category claimed."""
        return sum(self.uncategorized)

    def multi_category_total(self) -> float:
        """Return the year's spending filed under several categories."""
        return sum(self.multi_category)

    def excluded_rows(self) -> list[tuple[str, list[float], float]]:
        """Return (label, monthly values, year total) per non-empty row left out of the total."""
        rows = []
        if self.uncategorized_rows:
            rows.append((UNCATEGORIZED_ROW_LABEL, self.uncategorized, self.uncategorized_total()))
        if self.multi_category_rows:
            rows.append(
                (MULTI_CATEGORY_ROW_LABEL, self.multi_category, self.multi_category_total())
            )
        return rows


def yearly_summary_name(year: int) -> str:
    """Return the summary filename for a year, e.g. ``summary2026.csv``."""
    return f"summary{year}.csv"


def monthly_summary_name(year: int, month: int) -> str:
    """Return the per-month summary filename, e.g. ``summary2026_01jan.csv``."""
    return f"summary{year}_{month:02d}{MONTH_ABBREVIATIONS[month - 1]}.csv"


def category_listing_name(year: int, category_id: int) -> str:
    """Return one category's listing filename, e.g. ``healthcare2026.csv``."""
    return f"{CATEGORIES[category_id].lower()}{year}.csv"


def _list_parsed_paths(summary_name: str, parsed_dir: Path) -> list[Path]:
    """Return the parsed statements to summarize, leaving out the summary itself."""
    return [path for path in sorted(parsed_dir.glob("*.csv")) if path.name != summary_name]


def _resolve_date(path: Path, cell: str, statement_date: datetime | None) -> datetime | None:
    """Return the full date of an ``MM/DD`` cell, or None (logged) if unparsable."""
    try:
        return date_within_statement_year(datetime.strptime(cell, "%m/%d"), statement_date)
    except ValueError:
        logger.debug("%s: skipping row with unparsable date %r", path, cell)
        return None


def read_year_expenses(
    year: int, summary_name: str, parsed_dir: Path | None = None
) -> list[SummaryRow]:
    """Return every expense across all parsed statements for `year`.

    Each row's full date comes from its file's statement date, so a December
    transaction on a January statement lands in December. Categories the current
    `CATEGORIES` doesn't know are dropped, so a row nothing claims comes back with
    an empty `category_ids` — callers must account for it rather than let it
    vanish. The summary file itself is excluded.
    """
    parsed_dir = parsed_dir or get_parsed_dir()
    known_categories = set(CATEGORIES)
    rows: list[SummaryRow] = []

    for path in _list_parsed_paths(summary_name, parsed_dir):
        statement_date = read_parsed_statement_date(path)
        for row in read_parsed_expenses(path):
            date = _resolve_date(path, row.date, statement_date)
            if date is None or date.year != year:
                continue
            category_ids = [c for c in row.category_ids if c in known_categories]
            rows.append(
                SummaryRow(
                    date.month, row.cost, row.card, category_ids, row.date, row.remark, path.name
                )
            )
    return rows


def read_year_credits(year: int, summary_name: str, parsed_dir: Path | None = None) -> list[Credit]:
    """Return every credit — payments, refunds, rebates — dated in `year`.

    Amounts are as printed (Chase still in USD); files are chosen as for
    `read_year_expenses`.
    """
    parsed_dir = parsed_dir or get_parsed_dir()
    credits: list[Credit] = []
    for path in _list_parsed_paths(summary_name, parsed_dir):
        statement_date = read_parsed_statement_date(path)
        for row in read_parsed_credits(path):
            date = _resolve_date(path, row.date, statement_date)
            if date is None or date.year != year:
                continue
            credits.append(Credit(date, row.cost, row.remark, row.kind, row.card or None))
    return credits


def find_uncategorized(
    year: int | None = None, summary_name: str | None = None, parsed_dir: Path | None = None
) -> list[SummaryRow]:
    """Return the rows for `year` that no known category claims."""
    year = year if year is not None else datetime.now().year
    summary_name = summary_name or yearly_summary_name(year)
    return [
        row for row in read_year_expenses(year, summary_name, parsed_dir) if not row.category_ids
    ]


def find_multi_category(
    year: int | None = None, summary_name: str | None = None, parsed_dir: Path | None = None
) -> list[SummaryRow]:
    """Return the rows for `year` filed under more than one category."""
    year = year if year is not None else datetime.now().year
    summary_name = summary_name or yearly_summary_name(year)
    rows = read_year_expenses(year, summary_name, parsed_dir)
    return [row for row in rows if len(row.category_ids) > 1]


def find_duplicated_statements(rows: list[SummaryRow]) -> list[tuple[str, str, int, float]]:
    """Return (file, other file, shared rows, shared SGD) for parsed files that
    overlap.

    Nothing deduplicates the parsed directory, so a stray copy of a statement —
    ``UOB05 copy.csv``, a re-download under a longer name — is counted in full a
    second time. Two statements never legitimately carry the same transaction, so
    any overlap at all is worth reporting. Repeats *within* one file are left
    alone: buying the same coffee twice in a day is ordinary.
    """
    by_transaction: dict[tuple[RowKey, str], Counter[str]] = defaultdict(Counter)
    for row in rows:
        by_transaction[(row_key(row.date, row.cost, row.remark), row.card)][row.statement] += 1

    shared_rows: Counter[tuple[str, str]] = Counter()
    shared_cost: defaultdict[tuple[str, str], float] = defaultdict(float)
    for ((_, cost, _), card), statements in by_transaction.items():
        if len(statements) < 2:
            continue
        for pair in combinations(sorted(statements), 2):
            overlap = min(statements[pair[0]], statements[pair[1]])
            shared_rows[pair] += overlap
            shared_cost[pair] += overlap * to_sgd(cost, card)

    return [
        (first, second, count, shared_cost[(first, second)])
        for (first, second), count in shared_rows.most_common()
    ]


def warn_if_duplicated(rows: list[SummaryRow]) -> None:
    """Log a warning for each pair of parsed files carrying the same transactions."""
    for first, second, count, cost in find_duplicated_statements(rows):
        logger.warning(
            "%s and %s share %d identical rows (%.2f SGD), counted twice in the total; "
            "one of them is probably a stray copy",
            first,
            second,
            count,
            cost,
        )


def warn_if_multi_category(rows: list[SummaryRow]) -> None:
    """Log a warning per parsed file that still has rows filed under several categories."""
    counts: Counter[str] = Counter()
    costs: defaultdict[str, float] = defaultdict(float)
    for row in rows:
        if len(row.category_ids) > 1:
            counts[row.statement] += 1
            costs[row.statement] += to_sgd(row.cost, row.card)
    for statement in sorted(counts):
        logger.warning(
            "%s has %d multi-category rows (%.2f SGD), EXCLUDED from the total above; "
            "narrow each to one category and reconcile",
            statement,
            counts[statement],
            costs[statement],
        )


def to_sgd(cost: float, card: str) -> float:
    """Convert a transaction cost to SGD based on the card it was made on."""
    return cost * EXCHANGE_RATE_USD2SGD if card == USD_CARD else cost


def build_summary(year: int, summary_name: str, parsed_dir: Path | None = None) -> Summary:
    """Aggregate parsed statements into a Summary of single-category spending.

    Spending no category claims is tallied separately on `Summary.uncategorized`,
    and spending filed under several categories on `Summary.multi_category`;
    neither counts toward any category or the grand total, and neither is
    dropped silently.
    """
    return build_summary_from_rows(year, read_year_expenses(year, summary_name, parsed_dir))


def build_summary_from_rows(year: int, rows: list[SummaryRow]) -> Summary:
    """Aggregate already-read rows into a Summary. See `build_summary`."""
    summary = Summary(year=year, totals={c: [0.0] * MONTHS_IN_YEAR for c in sorted(CATEGORIES)})
    for row in rows:
        cost = to_sgd(row.cost, row.card)
        if not row.category_ids:
            summary.uncategorized[row.month - 1] += cost
            summary.uncategorized_rows += 1
        elif len(row.category_ids) > 1:
            summary.multi_category[row.month - 1] += cost
            summary.multi_category_rows += 1
        else:
            summary.totals[row.category_ids[0]][row.month - 1] += cost
    return summary


def summarize(
    year: int | None = None,
    out_name: str | None = None,
    parsed_dir: Path | None = None,
    summary_dir: Path | None = None,
) -> Path:
    """Build the summary for a year, log it as a table, and write it as CSV.

    Also rewrites one combined statement per month (see `write_monthly_summaries`)
    and the healthcare listing (see `write_category_listing`).
    Returns the yearly path written — ``<summary dir>/summary<year>.csv`` by default.
    """
    year = year if year is not None else datetime.now().year
    out_name = out_name or yearly_summary_name(year)
    parsed_dir = parsed_dir or get_parsed_dir()
    summary_dir = summary_dir or get_summary_dir()

    rows = read_year_expenses(year, out_name, parsed_dir)
    summary = build_summary_from_rows(year, rows)
    for line in format_summary_table(summary):
        logger.info("%s", line)
    warn_if_uncategorized(summary)
    warn_if_duplicated(rows)
    warn_if_multi_category(rows)

    summary_dir.mkdir(parents=True, exist_ok=True)
    out_path = summary_dir / out_name
    write_summary_csv(summary, out_path)
    logger.info("wrote %s", out_path)
    write_monthly_summaries(year, rows, read_year_credits(year, out_name, parsed_dir), summary_dir)
    write_category_listing(year, rows, HEALTHCARE_CATEGORY_ID, summary_dir)
    return out_path


def build_monthly_statements(
    year: int, rows: list[SummaryRow], credits: list[Credit]
) -> dict[int, Statement]:
    """Return month -> one Statement combining every card's expenses and credits.

    Amounts are converted to SGD so a month's subtotals don't mix currencies; a
    converted row keeps its original amount in the remark. Categories unknown to
    `CATEGORIES` become uncategorized, as in the summary, so each month's grand
    total equals that month's Total plus Uncategorized and Multi-category there.
    """
    months: dict[int, Statement] = {}

    def month_statement(month: int) -> Statement:
        if month not in months:
            name = monthly_summary_name(year, month).removesuffix(".csv")
            last_day = calendar.monthrange(year, month)[1]
            months[month] = Statement(name, "", datetime(year, month, last_day))
        return months[month]

    for row in rows:
        month_statement(row.month).expenses.append(_as_transaction(year, row))
    for credit in sorted(credits, key=lambda c: (c.card or "", c.date)):
        card = credit.card or ""
        month_statement(credit.date.month).credits.append(
            Credit(
                credit.date,
                to_sgd(credit.cost, card),
                _remark_in_sgd(credit.remark, credit.cost, card),
                credit.kind,
                credit.card,
            )
        )
    return dict(sorted(months.items()))


def build_category_statement(
    year: int, rows: list[SummaryRow], category_id: int
) -> Statement | None:
    """Return one Statement of every expense in `year` filed under `category_id`, or None.

    A row filed under several categories is included at its full cost, since that
    is the amount on the receipt. Amounts are in SGD, as in the month files.
    Credits are left out.
    """
    matching = [row for row in rows if category_id in row.category_ids]
    if not matching:
        return None
    statement = Statement(
        category_listing_name(year, category_id).removesuffix(".csv"),
        "",
        datetime(year, 12, 31),
    )
    statement.expenses = [_as_transaction(year, row) for row in matching]
    return statement


def write_category_listing(
    year: int, rows: list[SummaryRow], category_id: int, summary_dir: Path
) -> Path | None:
    """Write ``<category><year>.csv`` listing that category's expenses; return its path.

    With no such expenses, a file left over from an earlier run is deleted instead
    and None is returned.
    """
    path = summary_dir / category_listing_name(year, category_id)
    statement = build_category_statement(year, rows, category_id)
    if statement is None:
        if path.exists():
            path.unlink()
            logger.info("removed %s, the year no longer has any such rows", path)
        return None
    summary_dir.mkdir(parents=True, exist_ok=True)
    write_parsed_csv(statement, path)
    logger.info("wrote %s", path)
    return path


def _as_transaction(year: int, row: SummaryRow) -> Transaction:
    """Return a summary row as a Transaction in SGD, dated in `year`."""
    return Transaction(
        datetime.strptime(f"{year}/{row.date}", "%Y/%m/%d"),
        to_sgd(row.cost, row.card),
        _remark_in_sgd(row.remark, row.cost, row.card),
        category_from_ids(row.category_ids),
        row.card or None,
    )


def _remark_in_sgd(remark: str, cost: float, card: str) -> str:
    """Return `remark`, noting the original amount when `to_sgd` converts it."""
    return f"{remark} [USD {cost:.2f}]" if card == USD_CARD else remark


def write_monthly_summaries(
    year: int, rows: list[SummaryRow], credits: list[Credit], summary_dir: Path
) -> list[Path]:
    """Write ``summary<year>_<NNmon>.csv`` for each month with activity; return the paths.

    Each is a parsed-CSV-format statement spanning every card. A month file left
    over from an earlier run whose month now has no rows is deleted, so the
    directory never shows spending that is no longer there.
    """
    summary_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for statement in build_monthly_statements(year, rows, credits).values():
        path = summary_dir / f"{statement.name}.csv"
        write_parsed_csv(statement, path)
        written.append(path)
    all_months = {monthly_summary_name(year, month) for month in range(1, MONTHS_IN_YEAR + 1)}
    for path in summary_dir.glob(f"summary{year}_*.csv"):
        if path.name in all_months and path not in written:
            path.unlink()
            logger.info("removed %s, its month no longer has any rows", path)
    return written


def warn_if_uncategorized(summary: Summary) -> None:
    """Log a warning naming the spending the summary had to leave out."""
    if not summary.uncategorized_rows:
        return
    logger.warning(
        "%d rows (%.2f SGD) are uncategorized and EXCLUDED from the total above; "
        "categorize them or the year is understated by that much",
        summary.uncategorized_rows,
        summary.uncategorized_total(),
    )


def format_summary_table(summary: Summary) -> list[str]:
    """Return the summary rendered as aligned text lines, header first.

    Zero cells are left blank so the months with activity stand out. Uncategorized
    and multi-category spending each get their own line below the total, which
    they are not part of.
    """
    rows = _table_rows(summary)
    label_width = max(len(label) for label, _, _ in rows)
    months = [f"{month:02d}" for month in range(1, MONTHS_IN_YEAR + 1)]

    def cell(value: float, blank_zero: bool) -> str:
        return f"{'':>{CELL_WIDTH}}" if blank_zero and not value else f"{value:>{CELL_WIDTH}.2f}"

    lines = [str(summary.year)]
    lines.append(
        f"{'':<{label_width}}  "
        + "  ".join(f"{month:>{CELL_WIDTH}}" for month in months)
        + f"  {'Total':>{CELL_WIDTH}}"
    )
    for label, values, total in rows:
        blank_zero = label != "Total"
        lines.append(
            f"{label:<{label_width}}  "
            + "  ".join(cell(value, blank_zero) for value in values)
            + f"  {total:>{CELL_WIDTH}.2f}"
        )
    return lines


def _table_rows(summary: Summary) -> list[tuple[str, list[float], float]]:
    """Return (label, monthly values, year total) per row: categories, Total, excluded."""
    rows = [
        (f"{c} {CATEGORIES[c]}", summary.totals[c], summary.row_total(c))
        for c in sorted(summary.totals)
    ]
    month_totals = [summary.month_total(index) for index in range(MONTHS_IN_YEAR)]
    rows.append(("Total", month_totals, summary.grand_total()))
    return rows + summary.excluded_rows()


def write_summary_csv(summary: Summary, path: str | Path) -> None:
    """Write the summary as a CSV with a Total row and a Total column.

    `Uncategorized` and `Multi-category` rows follow the total, each only when
    there is such spending, so a clean year's file keeps the layout it has
    always had.
    """
    months = [f"{month:02d}" for month in range(1, MONTHS_IN_YEAR + 1)]
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["category", *months, "Total"])
        for label, values, total in _table_rows(summary):
            writer.writerow([label, *(f"{value:.2f}" for value in values), f"{total:.2f}"])


def read_summary_total(path: str | Path) -> float:
    """Return the grand-total cell of a summary CSV."""
    with open(path) as handle:
        for row in csv.reader(handle):
            if row and row[0] == "Total":
                return float(row[-1])
    raise ValueError(f"no Total row in {path}")


def check_summary_total(
    year: int | None = None,
    summary_name: str | None = None,
    tolerance: float = 0.01,
    parsed_dir: Path | None = None,
    summary_dir: Path | None = None,
) -> tuple[float, float, float]:
    """Re-sum the parsed statements and compare against the summary's total.

    Returns (recomputed, summary total, difference). A mismatch means the
    summary is stale or a parsed file changed since it was written. Uncategorized
    and multi-category spending are reported too: they match on both sides by
    construction, so they would otherwise hide behind an OK.
    """
    year = year if year is not None else datetime.now().year
    summary_name = summary_name or yearly_summary_name(year)
    parsed_dir = parsed_dir or get_parsed_dir()
    summary_dir = summary_dir or get_summary_dir()

    rows = read_year_expenses(year, summary_name, parsed_dir)
    built = build_summary_from_rows(year, rows)
    recomputed = built.grand_total()
    uncategorized = built.uncategorized_total()
    multi = built.multi_category_total()
    summary_total = read_summary_total(summary_dir / summary_name)
    difference = recomputed - summary_total

    status = "OK" if abs(difference) < tolerance else "MISMATCH"
    logger.info(
        "  %s: parsed-sum=%.2f  summary=%.2f  diff=%+.2f",
        status,
        recomputed,
        summary_total,
        difference,
    )
    warn_if_duplicated(rows)
    if uncategorized:
        logger.warning(
            "  UNCATEGORIZED: %.2f SGD is in neither figure above (gross spend %.2f)",
            uncategorized,
            recomputed + uncategorized + multi,
        )
    if multi:
        logger.warning(
            "  MULTI-CATEGORY: %.2f SGD is in neither figure above (gross spend %.2f)",
            multi,
            recomputed + uncategorized + multi,
        )
    return recomputed, summary_total, difference
