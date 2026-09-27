"""Stage 2 of the pipeline: extracted CSV -> categorized CSV in ``data/parsed``."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from trex.categorize import get_rules
from trex.categorize.rules import CatRules
from trex.config import get_parsed_dir, get_statements_dir
from trex.extract import derive_short_name, extract_pdf_to_csv
from trex.log import get_logger
from trex.models import Statement, category_from_ids, row_key
from trex.parse.balance import BalanceMismatch, check_card_totals
from trex.parse.files import find_extracted_csv, parse_extracted_csv
from trex.parse.rows import date_within_statement_year, detect_issuer, parse_statement_date
from trex.serialize import format_expense_row, read_parsed_expenses, write_parsed_csv

logger = get_logger(__name__)

__all__ = [
    "BalanceMismatch",
    "date_within_statement_year",
    "categorize_statement",
    "check_card_totals",
    "detect_issuer",
    "find_extracted_csv",
    "find_statement_pdf",
    "carry_over_manual_categories",
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
    save_rules: bool = True,
) -> Statement:
    """Extract, categorize and write one statement. Returns the Statement.

    `name` is a statement short name such as ``UOB05``, resolved to a PDF under
    the statements directory. `year` is used only when the statement carries no
    detectable date of its own. Any rules learned while categorizing are written
    back to cat.csv unless `save_rules` is False. A multi-category row narrowed
    manually in the existing parsed CSV keeps that choice (see
    `carry_over_manual_categories`); any left multi-category is warned about. Any card
    whose rows don't add up to its printed total balance is logged as a warning;
    the CSV is written regardless, so the rows can be inspected.
    """
    pdf_path = find_statement_pdf(name)
    name = derive_short_name(pdf_path)
    extract_pdf_to_csv(pdf_path)

    rules = rules if rules is not None else get_rules()
    statement = categorize_statement(name, year=year, rules=rules)
    if verbose:
        _log_transactions(statement)

    path = get_parsed_dir() / f"{statement.name}.csv"
    carry_over_manual_categories(statement, path)
    total = write_parsed_csv(statement, path)
    logger.info("  %d txns, grand total %.2f", len(statement.expenses), total)
    multi = [t for t in statement.expenses if t.is_multi_category]
    if multi:
        logger.warning(
            "  %s: %d rows are still multi-category; narrow them and reconcile",
            name,
            len(multi),
        )

    if save_rules:
        rules.save_if_changed()
    return statement


def carry_over_manual_categories(statement: Statement, path: Path) -> int:
    """Carry manually chosen categories from the parsed CSV at `path` onto `statement`.

    A row the rules file under several categories keeps the categories it has in
    the existing file when those are a strict, non-empty subset of the rules', or
    a single category of any kind: a multi-category rule can't decide the row, so
    a choice made for it (by reconcile or manually) is worth more. Rows the rules
    leave uncategorized or file under one category follow the rules. Rows are
    paired by date, cost and remark. Mutates `statement`; returns how many rows
    kept a manual choice. Does nothing if `path` doesn't exist.
    """
    if not path.exists():
        return 0
    previous = {
        row_key(row.date, row.cost, row.remark): row.category_ids
        for row in read_parsed_expenses(path)
    }
    kept = 0
    for transaction in statement.expenses:
        if not isinstance(transaction.category, tuple):
            continue
        key = row_key(transaction.date.strftime("%m/%d"), transaction.cost, transaction.remark)
        chosen = previous.get(key)
        if chosen and (len(chosen) == 1 or set(chosen) < set(transaction.category)):
            logger.info(
                "  kept manual choice %s over %s: %s",
                chosen,
                transaction.category,
                transaction.remark,
            )
            transaction.category = category_from_ids(chosen)
            kept += 1
    return kept


def categorize_statement(
    name: str,
    year: int | None = None,
    rules: CatRules | None = None,
    extracted_dir: Path | None = None,
) -> Statement:
    """Categorize the already-extracted CSV for `name` and return the Statement.

    Writes nothing: this is `parse_statement` without the PDF extraction before
    it or the CSV write after it. `year` is used only when the statement
    carries no date of its own. Any card whose rows don't add up to its printed
    total balance is logged as a warning.
    """
    statement = parse_extracted_csv(name, extracted_dir, rules=rules)
    if year is not None and statement.statement_date is None:
        _assume_year(statement, year)
    for mismatch in check_card_totals(statement):
        logger.warning("  %s: balance check failed: %s", name, mismatch.describe())
    return statement


def _assume_year(statement: Statement, year: int) -> None:
    """Anchor an undated statement to the end of `year` and re-date its rows."""
    statement.statement_date = datetime(year, 12, 31)
    for transaction in statement.expenses:
        transaction.date = date_within_statement_year(transaction.date, statement.statement_date)
    for credit in statement.credits:
        credit.date = date_within_statement_year(credit.date, statement.statement_date)


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
                transaction.card,
            ),
        )
