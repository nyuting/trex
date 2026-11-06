"""Turning a statement PDF into rows of (text, amount).

Statements are laid out as a wide text column followed by a right-aligned
amount column, so rows are rebuilt from word coordinates and split at the one
large horizontal gap that precedes the amount. This replaces tabula's stream
mode, which needed a JDK. Characters are deduplicated first because some PDF
generators fake bold by overprinting each glyph, which would read as "TToottaall".
"""

from __future__ import annotations

import re
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


@dataclass(frozen=True)
class PageRow:
    """One reconstructed line of a statement: its description and its amount."""

    text: str
    amount: str


def read_pdf_rows(path: str | Path) -> list[PageRow]:
    """Return every row of the PDF, page after page, in reading order.

    Raises ValueError when no page has any text, i.e. a scanned or broken PDF,
    rather than extracting an empty statement.
    """
    rows: list[PageRow] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            rows.extend(_to_page_row(words) for words in group_words_into_rows(page.dedupe_chars()))
            page.close()  # drop the page's cached objects; long PDFs otherwise pile up
    if not rows:
        raise ValueError(f"{path} has no text layer (scanned PDF?)")
    return rows


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


def _to_page_row(words: list[dict]) -> PageRow:
    """Build a PageRow, peeling off the words after the last wide gap if they're an amount.

    Only the last gap wider than `CELL_GAP` matters: the amount column is the
    rightmost one, and every other gap stays inside the text.
    """
    texts = [word["text"] for word in words]
    for index in range(len(words) - 1, 0, -1):
        if words[index]["x0"] - words[index - 1]["x1"] > CELL_GAP:
            tail = " ".join(texts[index:])
            if AMOUNT_RE.match(tail):
                return PageRow(" ".join(texts[:index]), tail)
            break
    return PageRow(" ".join(texts), "")


def find_in_row(row: PageRow, regex: re.Pattern[str]) -> re.Match[str] | None:
    """Return the first regex match in the row's text, else in its amount, or None.

    Searches rather than matches: the text may carry a neighbouring label
    merged into it (e.g. Chase's "Opening/Closing Date <period>").
    """
    return regex.search(row.text) or regex.search(row.amount)


def split_off_header(
    rows: list[PageRow], regex: re.Pattern[str]
) -> tuple[re.Match[str], list[PageRow]]:
    """Return the first row's match for `regex` and every other row, in order.

    Rows before the header are kept: Chase prints its account summary above the
    period. Raises ValueError when no row matches, since without its header the
    statement's dates can't be placed in a year.
    """
    for index, row in enumerate(rows):
        match = find_in_row(row, regex)
        if match:
            return match, rows[:index] + rows[index + 1 :]
    raise ValueError(f"no statement header matching {regex.pattern!r}")


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
