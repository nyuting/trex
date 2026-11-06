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
    """Return ``TREX_DATA_DIR`` if set, else ``data/`` under the repo root.

    Read on every call rather than cached at import, so tests can point trex at
    a temporary tree by setting the environment variable.
    """
    override = os.environ.get("TREX_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return find_repo_root() / "data"


def get_categories_dir() -> Path:
    return get_data_dir() / "categories"


def get_cat_file() -> Path:
    """Return cat.csv: remark regex -> category ids, card ids."""
    return get_categories_dir() / "cat.csv"


def get_personal_cat_file() -> Path:
    """Return cat_personal.csv: rules naming a person rather than a merchant.

    Loaded alongside cat.csv but never written to, so the names and phone
    numbers in it stay out of the file the pipeline rewrites.
    """
    return get_categories_dir() / "cat_personal.csv"


def get_cat_stats_file() -> Path:
    """Return cat_stats.csv: per-rule match counts and amounts from ``trex rules stats``.

    Derived and git-ignored: it holds spending amounts and cat_personal.csv's names.
    """
    return get_categories_dir() / "cat_stats.csv"


def get_not_claimable_file() -> Path:
    """Return not_claimable.csv: remark regexes whose medical charges are never claimed.

    Read by the healthcare listing, never written. It may name a person (a
    PayNow transfer), like cat_personal.csv.
    """
    return get_categories_dir() / "not_claimable.csv"


def get_not_medical_file() -> Path:
    """Return not_medical.csv: remark regexes of merchants that only look medical.

    Read by `trex summarize`, never written: a charge outside HEALTHCARE whose
    remark it matches is neither warned about nor listed, whatever keyword it holds.
    """
    return get_categories_dir() / "not_medical.csv"


def get_brands_file() -> Path:
    return get_categories_dir() / "brands.csv"


def get_statements_dir() -> Path:
    """Return the input statement PDFs and claims exports (private, never committed)."""
    return get_data_dir() / "statements"


def get_extracted_dir() -> Path:
    return get_data_dir() / "extracted"


def get_parsed_dir() -> Path:
    return get_data_dir() / "parsed"


def get_summary_dir() -> Path:
    """Return the yearly and per-month summaries, all rebuilt by `trex summarize`."""
    return get_data_dir() / "summary"
