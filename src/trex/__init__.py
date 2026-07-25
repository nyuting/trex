"""trex — turn credit-card PDF statements into categorized monthly summaries.

The pipeline runs in three stages, each usable on its own::

    from trex import parse_statement, summarize

    parse_statement("UOB05")   # PDF -> extracted CSV -> categorized CSV
    summarize(year=2026)       # every parsed CSV -> category x month table

Paths come from :mod:`trex.config`, never from the current working directory.
"""

from __future__ import annotations

__version__ = "0.1.0"

from trex.categorize import classify_remark, flush_rules, regroup_cat_file, suggest_brands
from trex.config import get_data_dir, get_parsed_dir
from trex.constants import CATEGORIES, SOURCES_BY_ID
from trex.extract import extract_all_pdfs, extract_pdf_to_csv
from trex.models import Credit, Statement, Transaction
from trex.parse import parse_statement
from trex.reconcile import list_pending_updates, reconcile_update, regroup_update
from trex.serialize import read_parsed_csv, write_parsed_csv
from trex.summary import (
    check_summary_total,
    find_duplicated_statements,
    find_uncategorized,
    summarize,
)

__all__ = [
    "CATEGORIES",
    "SOURCES_BY_ID",
    "Credit",
    "Statement",
    "Transaction",
    "__version__",
    "check_summary_total",
    "classify_remark",
    "extract_all_pdfs",
    "extract_pdf_to_csv",
    "find_duplicated_statements",
    "find_uncategorized",
    "flush_rules",
    "get_data_dir",
    "get_parsed_dir",
    "list_pending_updates",
    "parse_statement",
    "read_parsed_csv",
    "reconcile_update",
    "regroup_update",
    "regroup_cat_file",
    "suggest_brands",
    "summarize",
    "write_parsed_csv",
]
