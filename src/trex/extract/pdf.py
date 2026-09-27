"""Turning a statement PDF into rows of (text, amount).

Statements are laid out as a wide text column followed by a right-aligned
amount column, so rows are rebuilt from word coordinates and split at the one
large horizontal gap that precedes the amount. This replaces tabula's stream
mode, which needed a JDK.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pdfplumber

if TYPE_CHECKING:
    from pdfplumber.page import Page

#: Points. Words within this vertical distance of each other share a row.
ROW_TOLERANCE = 3.0
#: Points. A horizontal gap wider than this starts a new cell.
CELL_GAP = 8.0
#: Points. Below this, adjacent characters belong to the same word.
WORD_TOLERANCE = 1.5

#: Cells that look like a money amount, optionally flagged credit/debit.
AMOUNT_RE = re.compile(r"^-?[\d,]*\.\d{2}(\s+(CR|DB))?$")

#: Separator used to merge the leading cells back into one description cell.
CELL_JOIN = " "


@dataclass(frozen=True)
class PageRow:
    """One reconstructed line of a statement: its description and its amount."""

    text: str
    amount: str


def group_words_into_rows(page: Page) -> list[list[dict]]:
    """Return the page's words grouped into rows, each sorted left to right."""
    rows: dict[float, list[dict]] = {}
    for word in page.extract_words(x_tolerance=WORD_TOLERANCE, use_text_flow=False):
        for top in rows:
            if abs(top - word["top"]) <= ROW_TOLERANCE:
                rows[top].append(word)
                break
        else:
            rows[word["top"]] = [word]
    return [sorted(rows[top], key=lambda w: w["x0"]) for top in sorted(rows)]


def split_row_into_cells(words: list[dict]) -> list[str]:
    """Split one row's words into cell strings at wide horizontal gaps."""
    cells: list[list[dict]] = [[words[0]]]
    for previous, word in zip(words, words[1:], strict=False):
        if word["x0"] - previous["x1"] > CELL_GAP:
            cells.append([])
        cells[-1].append(word)
    return [" ".join(word["text"] for word in cell) for cell in cells]


def read_pdf_rows(path: str | Path) -> list[list[PageRow]]:
    """Return each page of the PDF as a list of PageRow, in reading order."""
    pages: list[list[PageRow]] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            pages.append([_to_page_row(words) for words in group_words_into_rows(page)])
    return pages


def _to_page_row(words: list[dict]) -> PageRow:
    """Build a PageRow, peeling off a trailing amount cell when there is one."""
    cells = split_row_into_cells(words)
    if len(cells) > 1 and AMOUNT_RE.match(cells[-1]):
        return PageRow(CELL_JOIN.join(cells[:-1]), cells[-1])
    return PageRow(CELL_JOIN.join(cells), "")


def iter_row_cells(pages: list[list[PageRow]]) -> Iterator[tuple[list[str], str, str]]:
    """Yield (non-empty cells, description, amount) for every non-blank row.

    ``cells`` keeps every populated field of the row, because an issuer marker
    is sometimes merged into a neighbouring cell rather than the first one.
    """
    for page in pages:
        for row in page:
            cells = [value.strip() for value in (row.text, row.amount) if value.strip()]
            if not cells:
                continue
            description = cells[0]
            amount = cells[-1] if len(cells) > 1 else ""
            yield cells, description, amount


def find_in_cells(cells: list[str], regex: re.Pattern[str]) -> re.Match[str] | None:
    """Return the first regex match found anywhere in `cells`, or None.

    Searches rather than matches: a cell may carry a neighbouring label that the
    extractor merged into it (e.g. Chase's "Opening/Closing Date <period>").
    """
    for cell in cells:
        match = regex.search(cell)
        if match:
            return match
    return None


def format_csv_line(cells: list[str]) -> str:
    """Render cells as one CSV line the statement parsers can read back.

    Matches the historical tabula-GUI output byte for byte: only a leading empty
    cell is written as ``""``; later empty cells are bare commas.
    """
    out = []
    for index, cell in enumerate(cells):
        if cell == "":
            out.append('""' if index == 0 else "")
        elif "," in cell or '"' in cell:
            out.append('"' + cell.replace('"', '""') + '"')
        else:
            out.append(cell)
    return ",".join(out)
