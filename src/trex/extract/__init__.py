"""Stage 1 of the pipeline: statement PDF -> raw CSV under ``data/extracted``.

The output is a faithful transcription of the PDF, not yet categorized; the
`trex.parse` package turns it into transactions.
"""

from __future__ import annotations

import re
from pathlib import Path

from trex.config import get_extracted_dir, get_statements_dir
from trex.extract.chase import extract_chase_lines
from trex.extract.paylah import extract_paylah_lines
from trex.extract.pdf import PageRow, read_pdf_rows
from trex.extract.uob import extract_uob_lines
from trex.log import get_logger

logger = get_logger(__name__)

__all__ = [
    "EXTRACTORS",
    "detect_issuer_prefix",
    "extract_all_pdfs",
    "extract_pdf_to_csv",
    "short_name",
]

#: Filename prefix -> the function that reads that issuer's pages.
EXTRACTORS = {
    "Chase": extract_chase_lines,
    "PLG": extract_paylah_lines,
    "PLY": extract_paylah_lines,
    "UOB": extract_uob_lines,
}

#: A statement's short name is its leading letters plus digits, e.g. "UOB05".
SHORT_NAME_RE = re.compile(r"^([A-Za-z]+\d+)")


def detect_issuer_prefix(path: str | Path) -> str:
    """Return the EXTRACTORS key for a statement path, from its filename prefix."""
    name = Path(path).name
    for prefix in EXTRACTORS:
        if name.startswith(prefix):
            return prefix
    raise ValueError(f"unknown statement type for {name}")


def short_name(path: str | Path) -> str:
    """Return the statement's short name, e.g. ``UOB05.pdf`` -> ``UOB05``."""
    name = Path(path).name
    match = SHORT_NAME_RE.match(name)
    if not match:
        raise ValueError(f"can't derive short name from {name}")
    return match.group(1)


def extract_pdf_to_csv(source_path: str | Path, dest_path: str | Path | None = None) -> Path:
    """Extract one statement PDF to a raw CSV and return the path written.

    Defaults to ``<extracted dir>/<short name>.csv``.
    """
    source_path = Path(source_path)
    if dest_path is None:
        dest_path = get_extracted_dir() / f"{short_name(source_path)}.csv"
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    pages: list[list[PageRow]] = read_pdf_rows(source_path)
    lines = EXTRACTORS[detect_issuer_prefix(source_path)](pages)
    dest_path.write_text("\n".join(lines) + "\n", newline="")

    logger.info("wrote %s", dest_path)
    return dest_path


def extract_all_pdfs(source_dir: str | Path | None = None) -> list[Path]:
    """Extract every PDF in the statements directory. Returns the paths written."""
    source_dir = Path(source_dir) if source_dir is not None else get_statements_dir()
    return [extract_pdf_to_csv(pdf) for pdf in sorted(source_dir.glob("*.pdf"))]
