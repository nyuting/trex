"""The category x month table: building it, rendering it, and its CSV file."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from trex.constants import CATEGORIES
from trex.summary.rows import SummaryRow, to_sgd

MONTHS_IN_YEAR = 12
MONTH_HEADERS = [f"{month:02d}" for month in range(1, MONTHS_IN_YEAR + 1)]
CELL_WIDTH = 9


@dataclass
class Excluded:
    """Spending left out of every total, by month, and how many rows it came from."""

    label: str
    monthly: list[float] = field(default_factory=lambda: [0.0] * MONTHS_IN_YEAR)
    rows: int = 0

    def total(self) -> float:
        return sum(self.monthly)


@dataclass
class YearSummary:
    """A category x month table of spending, in SGD.

    Spending no known category claims, and spending filed under several, are
    tallied apart and count toward no category and not the grand total.
    """

    year: int
    #: category id -> 12 monthly totals
    totals: dict[int, list[float]] = field(default_factory=dict)
    uncategorized: Excluded = field(default_factory=lambda: Excluded("Uncategorized"))
    multi_category: Excluded = field(default_factory=lambda: Excluded("Multi-category"))

    def row_total(self, category_id: int) -> float:
        return sum(self.totals[category_id])

    def month_total(self, month_index: int) -> float:
        """Return every category's total for one month (0-based index)."""
        return sum(values[month_index] for values in self.totals.values())

    def grand_total(self) -> float:
        return sum(sum(values) for values in self.totals.values())

    def excluded(self) -> list[Excluded]:
        """Return the excluded tallies that have any rows."""
        return [tally for tally in (self.uncategorized, self.multi_category) if tally.rows]


def year_summary_name(year: int) -> str:
    return f"summary{year}.csv"


def build_year_summary(year: int, rows: list[SummaryRow]) -> YearSummary:
    """Aggregate expense rows into a YearSummary, tallying excluded spending apart."""
    summary = YearSummary(year=year, totals={c: [0.0] * MONTHS_IN_YEAR for c in sorted(CATEGORIES)})
    for row in rows:
        cost = to_sgd(row.cost, row.card)
        if len(row.category_ids) == 1:
            summary.totals[row.category_ids[0]][row.month - 1] += cost
            continue
        tally = summary.multi_category if row.category_ids else summary.uncategorized
        tally.monthly[row.month - 1] += cost
        tally.rows += 1
    return summary


def format_year_summary(summary: YearSummary) -> list[str]:
    """Return the summary rendered as aligned text lines, header first.

    Zero cells are left blank so the months with activity stand out, except on
    the Total line. Excluded spending gets its own lines below the total.
    """
    rows = _table_rows(summary)
    label_width = max(len(label) for label, _, _ in rows)

    def line(label: str, cells: list[str], total: str) -> str:
        return f"{label:<{label_width}}  " + "  ".join(cells) + f"  {total:>{CELL_WIDTH}}"

    lines = [str(summary.year), line("", [f"{m:>{CELL_WIDTH}}" for m in MONTH_HEADERS], "Total")]
    for label, values, total in rows:
        cells = [
            f"{'':>{CELL_WIDTH}}" if label != "Total" and not value else f"{value:>{CELL_WIDTH}.2f}"
            for value in values
        ]
        lines.append(line(label, cells, f"{total:.2f}"))
    return lines


def _table_rows(summary: YearSummary) -> list[tuple[str, list[float], float]]:
    """Return (label, monthly values, year total) per row: categories, Total, excluded."""
    rows = [
        (f"{c} {CATEGORIES[c]}", summary.totals[c], summary.row_total(c))
        for c in sorted(summary.totals)
    ]
    month_totals = [summary.month_total(index) for index in range(MONTHS_IN_YEAR)]
    rows.append(("Total", month_totals, summary.grand_total()))
    return rows + [(tally.label, tally.monthly, tally.total()) for tally in summary.excluded()]


def write_year_summary(summary: YearSummary, path: str | Path) -> None:
    """Write the summary as a CSV with a Total row and a Total column.

    `Uncategorized` and `Multi-category` rows follow the total, each only when
    there is such spending, so a clean year's file keeps the layout it has
    always had.
    """
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["category", *MONTH_HEADERS, "Total"])
        for label, values, total in _table_rows(summary):
            writer.writerow([label, *(f"{value:.2f}" for value in values), f"{total:.2f}"])


def read_year_summary_total(path: str | Path) -> float:
    """Return the grand-total cell of a summary CSV."""
    with open(path) as handle:
        for row in csv.reader(handle):
            if row and row[0] == "Total":
                return float(row[-1])
    raise ValueError(f"no Total row in {path}")
