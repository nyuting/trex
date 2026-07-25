"""Stage 2 of the pipeline: extracted CSV -> categorized CSV in ``data/parsed``."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from trex.categorize import get_rules
from trex.categorize.rules import CatRules
from trex.config import get_statements_dir
from trex.extract import extract_pdf_to_csv, short_name
from trex.log import get_logger
from trex.models import Statement
from trex.parse.files import find_extracted_csv, parse_extracted_csv
from trex.parse.rows import adjust_year, detect_issuer, parse_statement_date
from trex.serialize import format_expense_row, write_parsed_csv

logger = get_logger(__name__)

__all__ = [
    "adjust_year",
    "detect_issuer",
    "find_extracted_csv",
    "find_statement_pdf",
    "parse_extracted_csv",
    "parse_statement",
    "parse_statement_date",
]


def find_statement_pdf(name: str, statements_dir: str | Path | None = None) -> Path:
    """Return the statement PDF whose filename starts with `name`."""
    statements_dir = Path(statements_dir) if statements_dir else get_statements_dir()
    matches = sorted(statements_dir.glob(f"{name}*.pdf"))
    if not matches:
        raise FileNotFoundError(f"no PDF in {statements_dir} matching {name!r}")
    return matches[0]


def parse_statement(
    name: str,
    year: int | None = None,
    verbose: bool = False,
    rules: CatRules | None = None,
    flush: bool = True,
) -> Statement:
    """Extract, categorize and write one statement. Returns the Statement.

    `name` is a statement short name such as ``UOB05``, resolved to a PDF under
    the statements directory. `year` is used only when the statement carries no
    detectable date of its own. Any rules learned while categorizing are written
    back to cat.csv unless `flush` is False.
    """
    pdf_path = find_statement_pdf(name)
    name = short_name(pdf_path)
    extract_pdf_to_csv(pdf_path)

    rules = rules if rules is not None else get_rules()
    statement = parse_extracted_csv(name, rules=rules)
    if year is not None and statement.statement_date is None:
        _assume_year(statement, year)

    if verbose:
        _log_transactions(statement)

    total = write_parsed_csv(statement)
    logger.info("  %d txns, grand total %.2f", len(statement.expenses), total)

    if flush:
        rules.flush()
    return statement


def _assume_year(statement: Statement, year: int) -> None:
    """Anchor an undated statement to the end of `year` and re-date its rows."""
    statement.statement_date = datetime(year, 12, 31)
    for transaction in statement.expenses:
        transaction.date = adjust_year(transaction.date, statement.statement_date)
    for credit in statement.credits:
        credit.date = adjust_year(credit.date, statement.statement_date)


def _log_transactions(statement: Statement) -> None:
    """Log every expense in parse order, in the same layout as the parsed CSV."""
    for number, transaction in enumerate(statement.expenses, start=1):
        logger.info(
            "%s",
            format_expense_row(
                number,
                transaction.date,
                transaction.cost,
                transaction.remark,
                transaction.category,
                transaction.source,
            ),
        )
