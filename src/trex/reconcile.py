"""Folding hand edits back into the rule set.

The workflow: copy ``<name>.csv`` to ``<name>-update.csv``, fix the category
cell of any row that was filed wrongly, then run reconcile. Each corrected row
teaches the cat.csv rule that matched it, so the fix sticks for future
statements, and ``<name>.csv`` is rewritten with the rows in their new sections.
"""

from __future__ import annotations

import csv
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from trex.categorize import get_rules
from trex.categorize.rules import CatRules
from trex.config import get_parsed_dir, get_prior_dir
from trex.log import get_logger
from trex.models import Credit, Statement, Transaction
from trex.parse.rows import adjust_year, detect_issuer
from trex.serialize import (
    parse_expense_row,
    read_parsed_csv,
    read_parsed_statement_date,
    write_parsed_csv,
)

logger = get_logger(__name__)

UPDATE_SUFFIX = "-update.csv"
#: A credits row in a parsed CSV starts with a bare MM/DD in its own column.
CREDIT_DATE_RE = re.compile(r"^\d{2}/\d{2}$")
#: Totals may drift by this much (rounding) before a reconcile is refused.
TOTAL_TOLERANCE = 0.005


@dataclass
class ReconcileReport:
    """What a reconcile did: rules taught, rows it could not place, rows untouched."""

    appended: list[tuple[str, int]]
    unmatched: list[tuple[str, str, list[int]]]
    unchanged: int


def parsed_path(name: str, parsed_dir: Path | None = None) -> Path:
    """Return the parsed CSV path for a statement short name."""
    return (parsed_dir or get_parsed_dir()) / f"{name}.csv"


def update_path(name: str, parsed_dir: Path | None = None) -> Path:
    """Return the hand-edited ``-update.csv`` path for a statement short name."""
    return (parsed_dir or get_parsed_dir()) / f"{name}{UPDATE_SUFFIX}"


def list_pending_updates(parsed_dir: Path | None = None) -> list[str]:
    """Return the short names of statements with an unreconciled -update.csv."""
    parsed_dir = parsed_dir or get_parsed_dir()
    return sorted(path.name[: -len(UPDATE_SUFFIX)] for path in parsed_dir.glob(f"*{UPDATE_SUFFIX}"))


def reconcile_update(
    name: str, parsed_dir: Path | None = None, rules: CatRules | None = None
) -> ReconcileReport | None:
    """Teach cat.csv from the edits in ``<name>-update.csv``, then regroup and delete it.

    Returns None without changing anything if the files are missing or the two
    totals disagree — a changed total means rows were added or lost by hand,
    which is a mistake rather than a recategorization.

    Edit the per-row category cell (the column before the source column);
    section headers are ignored. Rows whose remark matches no rule are reported
    for a manual cat.csv entry rather than guessed at.
    """
    parsed_dir = parsed_dir or get_parsed_dir()
    original = parsed_path(name, parsed_dir)
    update = update_path(name, parsed_dir)

    if not update.exists():
        logger.info("no %s for %s", UPDATE_SUFFIX, name)
        return None
    if not original.exists():
        logger.info("no original %s", original)
        return None

    original_rows = read_parsed_csv(original)
    updated_rows = read_parsed_csv(update)
    if not _totals_agree(original_rows, updated_rows):
        return None

    rules = rules if rules is not None else get_rules()
    report = _teach_rules(original_rows, updated_rows, rules)
    _log_report(report)

    rules.flush()
    regroup_update(name, parsed_dir)
    update.unlink()
    logger.info("reconciled %s: regrouped %s, removed %s", name, original, UPDATE_SUFFIX)
    return report


def _totals_agree(original_rows, updated_rows) -> bool:
    """True if the edited file still sums to the original total."""
    original_total = sum(row.cost for row in original_rows)
    updated_total = sum(row.cost for row in updated_rows)
    if abs(original_total - updated_total) < TOTAL_TOLERANCE:
        return True
    logger.warning(
        "  WARN: total changed %.2f -> %.2f (diff %+.2f); aborting reconcile",
        original_total,
        updated_total,
        updated_total - original_total,
    )
    return False


def _teach_rules(original_rows, updated_rows, rules: CatRules) -> ReconcileReport:
    """Append each row's corrected categories to the rule that matched its remark."""
    updated_by_key = {(row.date, row.cost, row.remark): row for row in updated_rows}
    appended: list[tuple[str, int]] = []
    unmatched: list[tuple[str, str, list[int]]] = []
    unchanged = 0

    for row in original_rows:
        edited = updated_by_key.get((row.date, row.cost, row.remark))
        if edited is None:
            continue
        if sorted(row.category_ids) == sorted(edited.category_ids):
            unchanged += 1
            continue

        rule = rules.find(row.remark)
        if rule is None:
            unmatched.append((row.remark, row.source, edited.category_ids))
            continue
        for category_id in edited.category_ids:
            if category_id not in rule.category_ids:
                rule.category_ids.append(category_id)
                rules.dirty = True
                appended.append((rule.pattern, category_id))

    return ReconcileReport(appended, unmatched, unchanged)


def _log_report(report: ReconcileReport) -> None:
    if report.appended:
        logger.info("\n  APPENDED categories to existing rules:")
        for pattern, category_id in report.appended:
            logger.info("    +%d -> /%s/", category_id, pattern)
    if report.unmatched:
        logger.info("\n  UNMATCHED (add a regex to cat.csv by hand):")
        for remark, source, category_ids in report.unmatched:
            logger.info("    cats=%s src=%s: %s", category_ids, source, remark)
    logger.info(
        "  (%d rows unchanged, %d appends, %d unmatched)",
        report.unchanged,
        len(report.appended),
        len(report.unmatched),
    )


def regroup_update(name: str, parsed_dir: Path | None = None) -> float:
    """Rewrite ``<name>.csv`` from ``<name>-update.csv``, re-sectioning edited rows.

    Pure regrouping: cat.csv is untouched and -update.csv is left in place. Use
    this to preview how an edit will land before committing it with reconcile.
    """
    parsed_dir = parsed_dir or get_parsed_dir()
    original = parsed_path(name, parsed_dir)
    update = update_path(name, parsed_dir)

    if not update.exists():
        logger.info("no %s for %s", UPDATE_SUFFIX, name)
        return 0.0
    if not original.exists():
        logger.info("no original %s", original)
        return 0.0

    return write_parsed_csv(read_parsed_statement(update, name), original)


def read_parsed_statement(path: Path, name: str | None = None) -> Statement:
    """Read a parsed CSV back into a Statement, expenses and credits alike.

    The inverse of `trex.serialize.write_parsed_csv`; writing what this returns
    reproduces the file it was read from.
    """
    name = name if name is not None else path.stem
    statement_date = read_parsed_statement_date(path)
    return Statement(
        name=name,
        issuer=detect_issuer(name),
        statement_date=statement_date,
        expenses=_read_expenses(path, statement_date),
        credits=_read_credits(path, statement_date),
    )


def _to_full_date(text: str, statement_date: datetime | None) -> datetime:
    """Turn an ``MM/DD`` cell into a full date anchored to the statement."""
    return adjust_year(datetime.strptime(text, "%m/%d"), statement_date)


def _read_expenses(path: Path, statement_date: datetime | None) -> list[Transaction]:
    """Read the expense rows of a parsed CSV back into Transactions."""
    expenses = []
    for row in read_parsed_csv(path):
        category = None
        if len(row.category_ids) == 1:
            category = row.category_ids[0]
        elif row.category_ids:
            category = tuple(row.category_ids)
        expenses.append(
            Transaction(
                _to_full_date(row.date, statement_date),
                row.cost,
                row.remark,
                category,
                row.source or None,
            )
        )
    return expenses


def _read_credits(path: Path, statement_date: datetime | None) -> list[Credit]:
    """Read the credits block (real CSV columns, negative amounts) back into Credits."""
    credits = []
    with open(path) as handle:
        for row in csv.reader(handle):
            if len(row) < 5 or not CREDIT_DATE_RE.match(row[0] or ""):
                continue
            try:
                signed_cost = float(row[1])
            except ValueError:
                logger.debug("skipping credit row with unparsable amount %r", row)
                continue
            credits.append(
                Credit(
                    _to_full_date(row[0], statement_date),
                    -signed_cost,
                    row[2],
                    row[3],
                    row[4] or None,
                )
            )
    return credits


# --- cross-checking against a previous run ------------------------------


def check_against_prior(statement: Statement, prior_dir: Path | None = None) -> Counter | None:
    """Report transactions present in a previous run of this statement but not now.

    Returns the missing rows as a Counter, or None if there is nothing to
    compare against. Used to catch extraction regressions after a parser change.
    """
    prior_dir = prior_dir or get_prior_dir()
    path = prior_dir / f"{statement.name}.csv"
    if not path.exists():
        return None

    current = _fingerprints(statement)
    prior = _prior_fingerprints(path)
    if not prior:
        return None

    missing = prior - current
    extra = current - prior
    if missing:
        logger.warning(
            "\n  WARN [%s]: %d prior rows missing from new output:",
            statement.name,
            sum(missing.values()),
        )
        for row, count in list(missing.items())[:20]:
            logger.warning("    -%dx  %s", count, row)
        if len(missing) > 20:
            logger.warning("    ... and %d more", len(missing) - 20)
    else:
        suffix = f" (new has {sum(extra.values())} additional)" if extra else ""
        logger.info(
            "\n  OK [%s]: all %d prior rows present%s",
            statement.name,
            sum(prior.values()),
            suffix,
        )
    return missing


def _fingerprint(date: str, cost: float, remark: str) -> tuple[str, float, str]:
    """Return a comparison key that ignores category and source."""
    return (date, round(abs(cost), 2), remark)


def _fingerprints(statement: Statement) -> Counter:
    """Return fingerprints for every expense and credit in a Statement."""
    counter: Counter = Counter()
    for row in [*statement.expenses, *statement.credits]:
        counter[_fingerprint(row.date.strftime("%m/%d"), row.cost, row.remark)] += 1
    return counter


def _prior_fingerprints(path: Path) -> Counter:
    """Return fingerprints for every row of a previously written parsed CSV."""
    counter: Counter = Counter()
    with open(path) as handle:
        for row in csv.reader(handle):
            if len(row) == 1:
                parsed = parse_expense_row(row[0])
                if parsed is not None:
                    counter[_fingerprint(parsed.date, parsed.cost, parsed.remark)] += 1
            elif len(row) >= 3 and CREDIT_DATE_RE.match(row[0] or ""):
                try:
                    cost = float(row[1])
                except ValueError:
                    continue
                counter[_fingerprint(row[0], cost, row[2])] += 1
    return counter
