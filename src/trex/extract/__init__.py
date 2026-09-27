"""Stage 1 of the pipeline: statement PDF -> raw CSV under ``data/extracted``.

The output is a faithful transcription of the PDF, not yet categorized; the
`trex.parse` package turns it into transactions.
"""

from __future__ import annotations

import re
from pathlib import Path

from trex.config import get_extracted_dir, get_statements_dir
from trex.constants import ISSUER_BY_PREFIX, ISSUER_CHASE, ISSUER_PAYLAH, ISSUER_UOB
from trex.extract.chase import extract_chase_lines
from trex.extract.paylah import extract_paylah_lines
from trex.extract.pdf import PageRow, read_pdf_rows
from trex.extract.uob import extract_uob_lines
from trex.log import get_logger

logger = get_logger(__name__)

__all__ = [
    "EXTRACTORS",
    "detect_issuer",
    "extract_all_pdfs",
    "extract_pdf_to_csv",
    "derive_short_name",
]

#: Issuer -> the function that reads that issuer's pages.
EXTRACTORS = {
    ISSUER_CHASE: extract_chase_lines,
    ISSUER_PAYLAH: extract_paylah_lines,
    ISSUER_UOB: extract_uob_lines,
}

#: A statement's short name is its leading letters plus digits, e.g. "UOB05".
SHORT_NAME_RE = re.compile(r"^([A-Za-z]+\d+)")


def detect_issuer(path_or_name: str | Path) -> str:
    """Return the issuer family for a statement path or short name (e.g. ``UOB05``).

    Matches the filename's prefix against `ISSUER_BY_PREFIX`, ignoring case.
    """
    name = Path(path_or_name).name
    upper = name.upper()
    for prefix, issuer in ISSUER_BY_PREFIX.items():
        if upper.startswith(prefix):
            return issuer
    raise ValueError(f"unknown issuer for {name!r}")


def derive_short_name(path: str | Path) -> str:
    """Return the statement's short name, e.g. ``UOB05.pdf`` -> ``UOB05``."""
    name = Path(path).name
    match = SHORT_NAME_RE.match(name)
    if not match:
        raise ValueError(f"can't derive short name from {name}")
    return match.group(1)


def extract_pdf_to_csv(pdf_path: str | Path, dest_path: str | Path | None = None) -> Path:
    """Extract one statement PDF to a raw CSV and return the path written.

    Defaults to ``<extracted dir>/<short name>.csv``.
    """
    pdf_path = Path(pdf_path)
    if dest_path is None:
        dest_path = get_extracted_dir() / f"{derive_short_name(pdf_path)}.csv"
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    pages: list[list[PageRow]] = read_pdf_rows(pdf_path)
    lines = EXTRACTORS[detect_issuer(pdf_path)](pages)
    dest_path.write_text("\n".join(lines) + "\n", newline="")

    logger.info("wrote %s", dest_path)
    return dest_path


def extract_all_pdfs(pdf_dir: str | Path | None = None) -> list[Path]:
    """Extract every PDF in the statements directory. Returns the paths written.

    A PDF whose filename names no known issuer or short name is not a statement;
    it is skipped with a warning rather than aborting the rest of the batch.
    """
    pdf_dir = Path(pdf_dir) if pdf_dir is not None else get_statements_dir()
    written = []
    for pdf in sorted(pdf_dir.glob("*.pdf")):
        try:
            detect_issuer(pdf)
            derive_short_name(pdf)
        except ValueError as err:
            logger.warning("skipping %s: %s", pdf.name, err)
            continue
        written.append(extract_pdf_to_csv(pdf))
    return written
