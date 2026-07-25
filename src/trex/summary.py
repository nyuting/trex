"""Stage 3: consolidate every parsed statement into a category x month table."""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from itertools import combinations
from pathlib import Path

from trex.config import EXCHANGE_RATE_USD2SGD, get_parsed_dir
from trex.constants import CATEGORIES
from trex.log import get_logger
from trex.parse.rows import adjust_year
from trex.serialize import read_parsed_csv, read_parsed_statement_date

logger = get_logger(__name__)

MONTHS_IN_YEAR = 12
#: Chase statements are in USD; every other card is already SGD.
USD_SOURCE = "CHASE"
UPDATE_SUFFIX = "-update.csv"
CELL_WIDTH = 9
#: label of the row that carries spending no category claimed
UNCATEGORIZED_LABEL = "Uncategorized"


@dataclass(frozen=True)
class SummaryRow:
    """One expense line reduced to what the summary needs."""

    month: int
    cost: float
    source: str
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


def default_summary_name(year: int) -> str:
    """Return the summary filename for a year, e.g. ``summary2026.csv``."""
    return f"summary{year}.csv"


def iter_parsed_rows(
    year: int, summary_name: str, parsed_dir: Path | None = None
) -> list[SummaryRow]:
    """Return every expense across all parsed statements for `year`.

    Each row's full date comes from its file's statement date, so a December
    transaction on a January statement lands in December. Categories the current
    `CATEGORIES` doesn't know are dropped, so a row nothing claims comes back with
    an empty `category_ids` — callers must account for it rather than let it
    vanish. Pending -update.csv files are excluded, as is the summary file itself.
    """
    parsed_dir = parsed_dir or get_parsed_dir()
    known_categories = set(CATEGORIES)
    rows: list[SummaryRow] = []

    for path in sorted(parsed_dir.glob("*.csv")):
        if path.name.endswith(UPDATE_SUFFIX) or path.name == summary_name:
            continue
        statement_date = read_parsed_statement_date(path)
        for row in read_parsed_csv(path):
            try:
                date = adjust_year(datetime.strptime(row.date, "%m/%d"), statement_date)
            except ValueError:
                logger.debug("%s: skipping row with unparsable date %r", path, row.date)
                continue
            if date.year != year:
                continue
            category_ids = [c for c in row.category_ids if c in known_categories]
            rows.append(
                SummaryRow(
                    date.month, row.cost, row.source, category_ids, row.date, row.remark, path.name
                )
            )
    return rows


def find_uncategorized(
    year: int | None = None, summary_name: str | None = None, parsed_dir: Path | None = None
) -> list[SummaryRow]:
    """Return the rows for `year` that no known category claims."""
    year = year if year is not None else datetime.now().year
    summary_name = summary_name or default_summary_name(year)
    return [row for row in iter_parsed_rows(year, summary_name, parsed_dir) if not row.category_ids]


def find_duplicated_statements(rows: list[SummaryRow]) -> list[tuple[str, str, int, float]]:
    """Return (file, other file, shared rows, shared SGD) for parsed files that
    overlap.

    Nothing deduplicates the parsed directory, so a stray copy of a statement —
    ``UOB05 copy.csv``, a re-download under a longer name — is counted in full a
    second time. Two statements never legitimately carry the same transaction, so
    any overlap at all is worth reporting. Repeats *within* one file are left
    alone: buying the same coffee twice in a day is ordinary.
    """
    by_transaction: dict[tuple, Counter[str]] = defaultdict(Counter)
    for row in rows:
        by_transaction[(row.date, row.cost, row.source, row.remark)][row.statement] += 1

    shared_rows: Counter[tuple[str, str]] = Counter()
    shared_cost: Counter[tuple[str, str]] = Counter()
    for transaction, statements in by_transaction.items():
        if len(statements) < 2:
            continue
        for pair in combinations(sorted(statements), 2):
            overlap = min(statements[pair[0]], statements[pair[1]])
            shared_rows[pair] += overlap
            shared_cost[pair] += overlap * to_sgd(transaction[1], transaction[2])

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


def to_sgd(cost: float, source: str) -> float:
    """Convert a transaction cost to SGD based on the card it was made on."""
    return cost * EXCHANGE_RATE_USD2SGD if source == USD_SOURCE else cost


def build_summary(year: int, summary_name: str, parsed_dir: Path | None = None) -> Summary:
    """Aggregate parsed statements into a Summary, splitting shared costs evenly.

    A transaction filed under several categories contributes an equal share to
    each, so the table's grand total still equals total spending. Spending no
    category claims is tallied separately on `Summary.uncategorized` instead of
    being dropped silently.
    """
    return build_summary_from_rows(year, iter_parsed_rows(year, summary_name, parsed_dir))


def build_summary_from_rows(year: int, rows: list[SummaryRow]) -> Summary:
    """Aggregate already-read rows into a Summary. See `build_summary`."""
    summary = Summary(year=year, totals={c: [0.0] * MONTHS_IN_YEAR for c in sorted(CATEGORIES)})
    for row in rows:
        cost = to_sgd(row.cost, row.source)
        if not row.category_ids:
            summary.uncategorized[row.month - 1] += cost
            summary.uncategorized_rows += 1
            continue
        share = cost / len(row.category_ids)
        for category_id in row.category_ids:
            summary.totals[category_id][row.month - 1] += share
    return summary


def summarize(
    year: int | None = None, out_name: str | None = None, parsed_dir: Path | None = None
) -> Path:
    """Build the summary for a year, log it as a table, and write it as CSV.

    Returns the path written — ``<parsed dir>/summary<year>.csv`` by default.
    """
    year = year if year is not None else datetime.now().year
    out_name = out_name or default_summary_name(year)
    parsed_dir = parsed_dir or get_parsed_dir()

    rows = iter_parsed_rows(year, out_name, parsed_dir)
    summary = build_summary_from_rows(year, rows)
    for line in format_summary_table(summary):
        logger.info("%s", line)
    warn_if_uncategorized(summary)
    warn_if_duplicated(rows)

    out_path = parsed_dir / out_name
    write_summary_csv(summary, out_path)
    logger.info("wrote %s", out_path)
    return out_path


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
    spending gets its own line below the total, which it is not part of.
    """
    category_ids = sorted(summary.totals)
    labels = [f"{c} {CATEGORIES[c]}" for c in category_ids] + ["Total"]
    if summary.uncategorized_rows:
        labels.append(UNCATEGORIZED_LABEL)
    label_width = max(len(label) for label in labels)
    months = [f"{month:02d}" for month in range(1, MONTHS_IN_YEAR + 1)]

    def cell(value: float) -> str:
        return f"{value:>{CELL_WIDTH}.2f}" if value else f"{'':>{CELL_WIDTH}}"

    lines = [str(summary.year)]
    lines.append(
        f"{'':<{label_width}}  "
        + "  ".join(f"{month:>{CELL_WIDTH}}" for month in months)
        + f"  {'Total':>{CELL_WIDTH}}"
    )
    for category_id in category_ids:
        label = f"{category_id} {CATEGORIES[category_id]}"
        lines.append(
            f"{label:<{label_width}}  "
            + "  ".join(cell(value) for value in summary.totals[category_id])
            + f"  {summary.row_total(category_id):>{CELL_WIDTH}.2f}"
        )
    lines.append(
        f"{'Total':<{label_width}}  "
        + "  ".join(
            f"{summary.month_total(index):>{CELL_WIDTH}.2f}" for index in range(MONTHS_IN_YEAR)
        )
        + f"  {summary.grand_total():>{CELL_WIDTH}.2f}"
    )
    if summary.uncategorized_rows:
        lines.append(
            f"{UNCATEGORIZED_LABEL:<{label_width}}  "
            + "  ".join(cell(value) for value in summary.uncategorized)
            + f"  {summary.uncategorized_total():>{CELL_WIDTH}.2f}"
        )
    return lines


def write_summary_csv(summary: Summary, path: str | Path) -> None:
    """Write the summary as a CSV with a Total row and a Total column.

    An `Uncategorized` row follows the total, and only when there is such
    spending, so a clean year's file keeps the layout it has always had.
    """
    months = [f"{month:02d}" for month in range(1, MONTHS_IN_YEAR + 1)]
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["category", *months, "Total"])
        for category_id in sorted(summary.totals):
            values = summary.totals[category_id]
            writer.writerow(
                [
                    f"{category_id} {CATEGORIES[category_id]}",
                    *(f"{value:.2f}" for value in values),
                    f"{summary.row_total(category_id):.2f}",
                ]
            )
        writer.writerow(
            [
                "Total",
                *(f"{summary.month_total(index):.2f}" for index in range(MONTHS_IN_YEAR)),
                f"{summary.grand_total():.2f}",
            ]
        )
        if summary.uncategorized_rows:
            writer.writerow(
                [
                    UNCATEGORIZED_LABEL,
                    *(f"{value:.2f}" for value in summary.uncategorized),
                    f"{summary.uncategorized_total():.2f}",
                ]
            )


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
) -> tuple[float, float, float]:
    """Re-sum the parsed statements and compare against the summary's total.

    Returns (recomputed, summary total, difference). A mismatch means the
    summary is stale or a parsed file changed since it was written. Uncategorized
    spending is reported too: it matches on both sides by construction, so it
    would otherwise hide behind an OK.
    """
    year = year if year is not None else datetime.now().year
    summary_name = summary_name or default_summary_name(year)
    parsed_dir = parsed_dir or get_parsed_dir()

    rows = iter_parsed_rows(year, summary_name, parsed_dir)
    recomputed = sum(to_sgd(row.cost, row.source) for row in rows if row.category_ids)
    uncategorized = sum(to_sgd(row.cost, row.source) for row in rows if not row.category_ids)
    summary_total = read_summary_total(parsed_dir / summary_name)
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
            recomputed + uncategorized,
        )
    return recomputed, summary_total, difference
