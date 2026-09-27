"""Folding manual edits back into the rule set.

Edit the category cell of any wrongly filed row in ``<name>.csv`` and run
``trex reconcile <name>``: the file is compared with what the
current rules make of the extracted CSV, and each corrected row updates the
cat.csv rule that matched it, so the fix sticks for future statements. The
file is then re-parsed with the updated rules, so other rows those rules
match move too.
"""

from __future__ import annotations

import copy
import re
import tempfile
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from trex.categorize import get_rules
from trex.categorize.rules import CatRule, CatRules
from trex.config import get_parsed_dir, get_prior_dir
from trex.constants import CARD_IDS_BY_LABEL, PERSONAL_TRANSFER_PREFIXES
from trex.log import get_logger
from trex.models import (
    Credit,
    ParsedCreditRow,
    ParsedExpenseRow,
    RowKey,
    Statement,
    Transaction,
    category_from_ids,
    category_ids,
    row_key,
    to_cents,
)
from trex.parse import categorize_statement
from trex.parse.files import parse_extracted_csv
from trex.parse.rows import date_within_statement_year, detect_issuer
from trex.serialize import (
    read_card_balances,
    read_parsed_credits,
    read_parsed_expenses,
    read_parsed_statement_date,
    read_rewards,
    write_parsed_csv,
)

logger = get_logger(__name__)


@dataclass
class ReconcileReport:
    """What a reconcile did to cat.csv, and the rows it left to edit manually.

    `changed` holds (pattern, categories before, categories after) per rule
    edited; `added` holds (pattern, categories) per new literal rule;
    `unmatched` holds (remark, card, categories) for person transfers, which
    belong in cat_personal.csv. `narrowed` counts multi-category rows edited
    down to some of their categories: a choice for that row, not the rule.
    """

    changed: list[tuple[str, list[int], list[int]]] = field(default_factory=list)
    added: list[tuple[str, list[int]]] = field(default_factory=list)
    unmatched: list[tuple[str, str, list[int]]] = field(default_factory=list)
    narrowed: int = 0
    unchanged: int = 0
    #: Statements not reconciled: the parsed file is missing or its total moved.
    skipped: list[str] = field(default_factory=list)
    #: Per statement, the categories each recategorized row was edited to.
    edits: dict[str, dict[RowKey, list[int]]] = field(default_factory=dict)


@dataclass
class _Edit:
    """One row whose categories were changed manually."""

    remark: str
    card: str
    before: list[int]
    after: list[int]

    @property
    def narrows(self) -> bool:
        """True if a multi-category row was cut down to some of its categories."""
        return bool(self.after) and set(self.after) < set(self.before)


def parsed_path(name: str, parsed_dir: Path | None = None) -> Path:
    """Return the parsed CSV path for a statement short name."""
    return (parsed_dir or get_parsed_dir()) / f"{name}.csv"


def reconcile_in_place(
    names: str | Sequence[str],
    parsed_dir: Path | None = None,
    extracted_dir: Path | None = None,
    rules: CatRules | None = None,
) -> ReconcileReport | None:
    """Turn manual category fixes in each ``<name>.csv`` into cat.csv rules, then re-parse them.

    Each statement's baseline is what the rules, as they stood before this
    call, make of its extracted CSV; every row whose category differs from that
    counts as an edit. All the edits are folded into the rules together, so one rule edited to
    different categories in different rows or statements ends up with all of
    them. A statement whose parsed file is missing or whose total no longer
    matches its baseline is skipped and listed in `skipped`. Returns None if
    every statement was skipped.
    """
    names = [names] if isinstance(names, str) else list(names)
    rules = rules if rules is not None else get_rules()
    report = ReconcileReport()
    edits: list[_Edit] = []
    to_reparse: list[tuple[str, Path, list[ParsedExpenseRow]]] = []

    for name in names:
        edited_path = parsed_path(name, parsed_dir)
        if not edited_path.exists():
            logger.info("no parsed %s", edited_path)
            report.skipped.append(name)
            continue
        baseline_rows = _categorize_afresh(name, extracted_dir, rules)
        edited_rows = read_parsed_expenses(edited_path)
        if not _totals_agree(baseline_rows, edited_rows, name):
            report.skipped.append(name)
            continue
        edits += _find_edits(name, baseline_rows, edited_rows, report)
        to_reparse.append((name, edited_path, edited_rows))

    if not to_reparse:
        return None
    _fold_edits_into_rules(edits, rules, report)

    for name, edited_path, edited_rows in to_reparse:
        _reparse(name, edited_path, edited_rows, report.edits[name], extracted_dir, rules)
        logger.info("reconciled %s in place: re-parsed %s", name, edited_path)
    return report


def _reparse(
    name: str,
    path: Path,
    edited_rows: list[ParsedExpenseRow],
    edits: dict[RowKey, list[int]],
    extracted_dir: Path | None,
    rules: CatRules,
) -> None:
    """Rewrite `path` by parsing the extracted CSV again with the updated `rules`.

    Every edit in `edits` is kept even where the rules don't reproduce it: a
    row-only choice on a multi-category row, or a person transfer, which is
    warned about because a later plain parse would lose it. Any other row whose
    category moved from `edited_rows` is logged.
    """
    dated = read_parsed_statement_date(path)
    statement = categorize_statement(
        name, year=dated.year if dated else None, rules=rules, extracted_dir=extracted_dir
    )
    before = {row_key(row.date, row.cost, row.remark): row.category_ids for row in edited_rows}

    for transaction in statement.expenses:
        key = row_key(transaction.date.strftime("%m/%d"), transaction.cost, transaction.remark)
        from_rules = category_ids(transaction.category)
        wanted = edits.get(key)
        if wanted is not None and sorted(from_rules) != sorted(wanted):
            if wanted and set(wanted) < set(from_rules):
                logger.debug("  kept row-only %s of %s: %s", wanted, from_rules, transaction.remark)
            else:
                logger.warning(
                    "  kept manual edit %s (rules give %s; a plain parse will undo it): %s",
                    wanted,
                    from_rules,
                    transaction.remark,
                )
            transaction.category = category_from_ids(wanted)
        elif key in before and sorted(from_rules) != sorted(before[key]):
            logger.info(
                "  re-parse moved %s -> %s: %s", before[key], from_rules, transaction.remark
            )

    write_parsed_csv(statement, path)
    rules.save_if_changed()


def _categorize_afresh(
    name: str, extracted_dir: Path | None, rules: CatRules
) -> list[ParsedExpenseRow]:
    """Return the rows the current rules would write for `name`.

    Categorizes with a copy of `rules`, so neither they nor data/parsed change.
    """
    statement = parse_extracted_csv(name, extracted_dir, rules=copy.deepcopy(rules))
    with tempfile.TemporaryDirectory() as scratch:
        path = Path(scratch) / f"{name}.csv"
        write_parsed_csv(statement, path)
        return read_parsed_expenses(path)


def _totals_agree(
    original_rows: list[ParsedExpenseRow],
    updated_rows: list[ParsedExpenseRow],
    name: str = "",
) -> bool:
    """True if the edited file still sums to the original total, in whole cents."""
    original_total = sum(row.cost for row in original_rows)
    updated_total = sum(row.cost for row in updated_rows)
    if to_cents(original_total) == to_cents(updated_total):
        return True
    logger.warning(
        "  WARN%s: total changed %.2f -> %.2f (diff %+.2f); aborting reconcile",
        f" [{name}]" if name else "",
        original_total,
        updated_total,
        updated_total - original_total,
    )
    return False


def _find_edits(
    name: str,
    original_rows: list[ParsedExpenseRow],
    updated_rows: list[ParsedExpenseRow],
    report: ReconcileReport,
) -> list[_Edit]:
    """Return the rows whose categories differ, recording them in `report.edits[name]`."""
    updated_by_key = {row_key(row.date, row.cost, row.remark): row for row in updated_rows}
    statement_edits = report.edits.setdefault(name, {})
    edits = []
    for row in original_rows:
        key = row_key(row.date, row.cost, row.remark)
        edited = updated_by_key.get(key)
        if edited is None:
            continue
        if sorted(row.category_ids) == sorted(edited.category_ids):
            report.unchanged += 1
            continue
        statement_edits[key] = edited.category_ids
        edits.append(_Edit(row.remark, edited.card, row.category_ids, edited.category_ids))
    return edits


def _fold_edits_into_rules(edits: list[_Edit], rules: CatRules, report: ReconcileReport) -> None:
    """Update the rule behind each edited row, add literal ones where none matched, save.

    Edits are grouped by the rule that filed them. Narrowing a multi-category
    row is a choice for that row alone and leaves the rule be. Otherwise a
    correction replaces only the categories no edit kept: a rule filed as
    [2, 5] edited to [4, 5] becomes [4, 5], and one edited to 8 in one row and
    10 in another becomes [8, 10].
    """
    groups: dict[object, tuple[CatRule | None, list[_Edit]]] = {}
    for edit in edits:
        rule = rules.find(edit.remark)
        key = id(rule) if rule is not None else edit.remark
        groups.setdefault(key, (rule, []))[1].append(edit)

    for rule, group in groups.values():
        if rule is None:
            _add_literal_rule(group, rules, report)
            continue
        corrections = [edit for edit in group if not edit.narrows]
        report.narrowed += len(group) - len(corrections)
        if corrections:
            _update_rule(rule, corrections, rules, report)

    _log_report(report)
    rules.save_if_changed()


def _update_rule(
    rule: CatRule, edits: list[_Edit], rules: CatRules, report: ReconcileReport
) -> None:
    """Add the categories `edits` introduced to `rule`, dropping the ones they replaced.

    A category counts as replaced when an edit took it away: always on a
    single-category rule, but on a multi-category one only when the edit kept
    some of the row's categories and swapped the rest. Moving a multi-category
    row somewhere entirely new just adds that category, since the merchant
    evidently varies.
    """
    kept = _ordered_union(edit.after for edit in edits)
    replaced = {
        category_id
        for edit in edits
        if len(edit.before) == 1 or set(edit.after) & set(edit.before)
        for category_id in edit.before
        if category_id not in edit.after
    } - set(kept)
    after = [category_id for category_id in rule.category_ids if category_id not in replaced]
    after += [category_id for category_id in kept if category_id not in after]
    if not after:
        logger.warning("  not emptying /%s/; edit cat.csv manually", rule.pattern)
        return
    if after == rule.category_ids:
        return
    if len({tuple(sorted(edit.after)) for edit in edits}) > 1:
        logger.info("  rows disagree on /%s/; filing it under all of %s", rule.pattern, after)
    report.changed.append((rule.pattern, list(rule.category_ids), after))
    rule.category_ids[:] = after
    rules.dirty = True


def _add_literal_rule(group: list[_Edit], rules: CatRules, report: ReconcileReport) -> None:
    """Add an exact-match rule for a remark no rule matched, unless it names a person."""
    remark = group[0].remark
    category_ids = _ordered_union(edit.after for edit in group)
    if not category_ids:
        return
    if remark.startswith(PERSONAL_TRANSFER_PREFIXES):
        report.unmatched.append((remark, group[0].card, category_ids))
        return
    card_ids = {CARD_IDS_BY_LABEL[edit.card] for edit in group if edit.card in CARD_IDS_BY_LABEL}
    rule = rules.add_rule(re.escape(remark), category_ids, card_ids)
    report.added.append((rule.pattern, rule.category_ids))


def _ordered_union(lists: Iterable[list[int]]) -> list[int]:
    """Return every id in `lists`, once each, in first-seen order."""
    return list(dict.fromkeys(category_id for ids in lists for category_id in ids))


def _log_report(report: ReconcileReport) -> None:
    if report.changed:
        logger.info("\n  CHANGED rules:")
        for pattern, before, after in report.changed:
            logger.info("    %s -> %s  /%s/", before, after, pattern)
    if report.added:
        logger.info("\n  ADDED literal rules:")
        for pattern, category_ids in report.added:
            logger.info("    %s  /%s/", category_ids, pattern)
    if report.unmatched:
        logger.info("\n  PERSON TRANSFERS (add to cat_personal.csv manually):")
        for remark, card, category_ids in report.unmatched:
            logger.info("    cats=%s card=%s: %s", category_ids, card, remark)
    logger.info(
        "  (%d rows unchanged, %d narrowed for that row only, %d rules changed, %d added, "
        "%d left for cat_personal.csv)",
        report.unchanged,
        report.narrowed,
        len(report.changed),
        len(report.added),
        len(report.unmatched),
    )


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
        balances=read_card_balances(path),
        rewards=read_rewards(path),
    )


def _to_full_date(text: str, statement_date: datetime | None) -> datetime:
    """Turn an ``MM/DD`` cell into a full date anchored to the statement."""
    return date_within_statement_year(datetime.strptime(text, "%m/%d"), statement_date)


def _read_expenses(path: Path, statement_date: datetime | None) -> list[Transaction]:
    """Read the expense rows of a parsed CSV back into Transactions."""
    expenses = []
    for row in read_parsed_expenses(path):
        expenses.append(
            Transaction(
                _to_full_date(row.date, statement_date),
                row.cost,
                row.remark,
                category_from_ids(row.category_ids),
                row.card or None,
            )
        )
    return expenses


def _read_credits(path: Path, statement_date: datetime | None) -> list[Credit]:
    """Read the credits block of a parsed CSV back into Credits."""
    return [
        Credit(
            _to_full_date(row.date, statement_date),
            row.cost,
            row.remark,
            row.kind,
            row.card or None,
        )
        for row in read_parsed_credits(path)
    ]


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


def _fingerprints(statement: Statement) -> Counter[RowKey]:
    """Return a key per expense and credit in a Statement, ignoring category and card."""
    rows: list[Transaction | Credit] = [*statement.expenses, *statement.credits]
    return Counter(row_key(row.date.strftime("%m/%d"), abs(row.cost), row.remark) for row in rows)


def _prior_fingerprints(path: Path) -> Counter[RowKey]:
    """Return a key per expense and credit of a previously written parsed CSV."""
    rows: list[ParsedExpenseRow | ParsedCreditRow] = [
        *read_parsed_expenses(path),
        *read_parsed_credits(path),
    ]
    return Counter(row_key(row.date, abs(row.cost), row.remark) for row in rows)
