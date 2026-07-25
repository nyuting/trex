"""Filesystem locations and tunable constants.

Every path in trex resolves through this module, so nothing depends on the
current working directory. Set ``TREX_DATA_DIR`` to point the whole pipeline at
a different data tree (tests do exactly this).
"""

from __future__ import annotations

import os
from pathlib import Path

#: SGD per USD, applied to USD-denominated transactions when summarizing.
EXCHANGE_RATE_USD2SGD = 1.33


def find_repo_root(start: Path | None = None) -> Path:
    """Return the nearest ancestor directory containing pyproject.toml.

    Falls back to the current working directory when trex is installed
    non-editably and no project root is on the path.
    """
    start = (start or Path(__file__)).resolve()
    for candidate in [start, *start.parents]:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return Path.cwd()


def get_data_dir() -> Path:
    """Return the root of the data tree, honouring ``TREX_DATA_DIR``.

    Read on every call rather than cached at import, so tests can point trex at
    a temporary tree by setting the environment variable.
    """
    override = os.environ.get("TREX_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return find_repo_root() / "data"


def get_categories_dir() -> Path:
    """Return the directory holding the versioned categorization rule files."""
    return get_data_dir() / "categories"


def get_cat_file() -> Path:
    """Return the path to cat.csv (regex -> category ids, source ids)."""
    return get_categories_dir() / "cat.csv"


def get_personal_cat_file() -> Path:
    """Return the path to cat_personal.csv (person-to-person transfer rules).

    Loaded alongside cat.csv but never written to, so the names and phone
    numbers in it stay out of the file the pipeline rewrites.
    """
    return get_categories_dir() / "cat_personal.csv"


def get_brands_file() -> Path:
    """Return the path to brands.csv (one brand name per row)."""
    return get_categories_dir() / "brands.csv"


def get_statements_dir() -> Path:
    """Return the directory of input statement PDFs (private, never committed)."""
    return get_data_dir() / "statements"


def get_extracted_dir() -> Path:
    """Return the directory of raw CSVs produced by the PDF extractor."""
    return get_data_dir() / "extracted"


def get_parsed_dir() -> Path:
    """Return the directory of categorized, human-editable parsed CSVs."""
    return get_data_dir() / "parsed"


def get_prior_dir() -> Path:
    """Return the optional directory of previous-run parsed CSVs, used for diffing."""
    return get_data_dir() / "parsed_prior"
