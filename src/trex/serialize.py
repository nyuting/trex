"""The on-disk format of parsed statement CSVs — read and write, in one place.

A parsed CSV is a human-editable text table stored in a CSV shell: a
``Statement Date`` header, an optional block of credits as real columns (per
card, with that card's balance, when the statement prints balances), an
optional block of rewards points (UOB's UNI$) in the same columns, then one
section per category whose expense lines are single-column fixed-width
strings. Nothing outside this module should know that layout; everything else
works with :mod:`trex.models` objects.
"""

from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from trex.cells import format_amount, parse_id_list
from trex.config import get_parsed_dir
from trex.constants import CATEGORIES, MULTI_CATEGORY_LABEL, UNCATEGORIZED_LABEL
from trex.log import get_logger
from trex.models import (
    CardBalance,
    Category,
    Credit,
    ParsedCreditRow,
    ParsedExpenseRow,
    RewardsBalance,
    Statement,
    Transaction,
    category_ids,
)

if TYPE_CHECKING:
    from _csv import Writer as CsvWriter

logger = get_logger(__name__)

#: Matches a formatted expense line: number, date, cost, category cell, card, remark.
EXPENSE_ROW_RE = re.compile(r"^\s*(\d+)\s+(\d{2}/\d{2})\s+([\d.]+)\s+(\S+)\s+(\S+)\s+(.*)$")
#: A credits row starts with a bare MM/DD in its own column; balance rows leave it empty.
CREDIT_DATE_RE = re.compile(r"^\d{2}/\d{2}$")

_STATEMENT_DATE_HEADER = "Statement Date"
_COLUMN_HEADER = ["date", "cost", "remark", "category", "card"]

#: Remark cells of the per-card balance rows. Only the two printed balances are
#: read back; the credits, spending and balance rows are recomputed on write.
_PREVIOUS_BALANCE = "previous balance"
_CREDITS_TOTAL = "credits"
_SPENDING_TOTAL = "spending"
_COMPUTED_BALANCE = "balance"
_STATED_BALANCE = "statement balance"
_TOTAL_MARK = "total"

#: Remark cells of the rewards block, one per printed figure, in RewardsBalance
#: field order. ``used`` is written negative so the column sums to the balance;
#: the balance row is recomputed on write and not read back.
_REWARDS_ROWS = {
    "previous": "UNI$ previous",
    "earned": "UNI$ earned",
    "used": "UNI$ used",
    "adjustment": "UNI$ adjustment",
    "current": "UNI$ statement balance",
}
_REWARDS_COMPUTED = "UNI$ balance"


# --- formatting ---------------------------------------------------------


def format_expense_row(
    number: int,
    date: datetime | None,
    cost: float,
    remark: str,
    category: Category,
    card: str | None,
) -> str:
    """Return one fixed-width expense line, the inverse of `parse_expense_row`."""
    card_cell = card if card else "-"
    category_cell = ",".join(str(c) for c in category_ids(category)) or "-"
    date_cell = date.strftime("%m/%d") if date is not None else ""
    return (
        f"{number:>4}  {date_cell:>5}  {cost:>8.2f}  {category_cell:>3}  {card_cell:>9}  {remark}"
    )


def format_total_row(cost: float, category_cell: str, remark: str) -> str:
    """Return a subtotal/grand-total line, aligned to the expense columns."""
    return f"{'':>4}  {'':>5}  {cost:>8.2f}  {category_cell:>3}  {'Total':>9}  {remark}"


def category_section_label(category_id: int | None) -> str:
    """Return the section header text for a category id, e.g. ``"1 DINING"``."""
    if category_id in CATEGORIES:
        return f"{category_id} {CATEGORIES[category_id]}"
    return UNCATEGORIZED_LABEL


# --- reading ------------------------------------------------------------


def parse_expense_row(line: str) -> ParsedExpenseRow | None:
    """Parse one line written by `format_expense_row`; None if it is not one.

    The row number is intentionally dropped: it is display ordering, recomputed
    on every write.
    """
    match = EXPENSE_ROW_RE.match(line)
    if not match:
        return None
    _, date_cell, cost_cell, category_cell, card_cell, remark = match.groups()
    try:
        cost = float(cost_cell)
    except ValueError:
        return None
    return ParsedExpenseRow(
        date=date_cell,
        cost=cost,
        remark=remark,
        category_ids=parse_id_list(category_cell),
        card="" if card_cell == "-" else card_cell,
    )


def read_parsed_statement_date(path: str | Path) -> datetime | None:
    """Return the ``Statement Date`` header of a parsed CSV, or None if absent."""
    with open(path) as handle:
        for row in csv.reader(handle):
            if row and row[0] == _STATEMENT_DATE_HEADER and len(row) >= 2:
                try:
                    return datetime.strptime(row[1].strip(), "%Y-%m-%d")
                except ValueError:
                    return None
            if row and row[0] == _COLUMN_HEADER[0]:
                return None
    return None


def read_card_balances(path: str | Path) -> dict[str, CardBalance]:
    """Return the printed per-card balances of a parsed CSV, in file order.

    Empty for a statement written without balances (any non-UOB issuer, or a
    file written before balances were recorded).
    """
    balances: dict[str, CardBalance] = {}
    with open(path) as handle:
        for row in csv.reader(handle):
            if len(row) < 5 or row[0] or row[2] not in (_PREVIOUS_BALANCE, _STATED_BALANCE):
                continue
            card = row[4]
            balance = balances.setdefault(card, CardBalance(card))
            amount = _read_amount(row[1])
            if row[2] == _PREVIOUS_BALANCE:
                balance.previous = amount
            else:
                balance.stated_total = amount
    return balances


def read_rewards(path: str | Path) -> RewardsBalance | None:
    """Return the printed rewards-points figures of a parsed CSV, or None if it has none."""
    field_by_remark = {remark: name for name, remark in _REWARDS_ROWS.items()}
    values: dict[str, float | None] = {}
    with open(path) as handle:
        for row in csv.reader(handle):
            if len(row) < 5 or row[0] or row[2] not in field_by_remark:
                continue
            amount = _read_amount(row[1])
            name = field_by_remark[row[2]]
            values[name] = -amount if name == "used" and amount is not None else amount
    return RewardsBalance(**values) if values else None


def _read_amount(cell: str) -> float | None:
    try:
        return float(cell)
    except ValueError:
        return None


def read_parsed_expenses(path: str | Path) -> list[ParsedExpenseRow]:
    """Return the expense rows of a parsed CSV, skipping headers and subtotals.

    Uncategorized rows are kept with an empty ``category_ids`` so reconcile can
    spot the ones a human has since filled in.
    """
    rows: list[ParsedExpenseRow] = []
    with open(path) as handle:
        for raw_row in csv.reader(handle):
            if len(raw_row) != 1:
                continue
            parsed = parse_expense_row(raw_row[0])
            if parsed is not None:
                rows.append(parsed)
    return rows


def read_parsed_credits(path: str | Path) -> list[ParsedCreditRow]:
    """Return the credits-block rows of a parsed CSV, with their amounts made positive.

    The per-card balance and total rows are not credits and are skipped.
    """
    credits: list[ParsedCreditRow] = []
    with open(path) as handle:
        for row in csv.reader(handle):
            if len(row) < 5 or not CREDIT_DATE_RE.match(row[0] or ""):
                continue
            try:
                signed_cost = float(row[1])
            except ValueError:
                logger.debug("skipping credit row with unparsable amount %r", row)
                continue
            credits.append(ParsedCreditRow(row[0], -signed_cost, row[2], row[3], row[4]))
    return credits


# --- writing ------------------------------------------------------------


def write_parsed_csv(statement: Statement, path: str | Path | None = None) -> float:
    """Write `statement` as a category-grouped parsed CSV and return its total.

    Layout: statement-date header, credits (grouped per card with that card's
    previous balance, credits, spending and balance when the statement carries
    balances), one section per category (sorted by remark then date, with a
    subtotal), a multi-category section, grand total.
    Defaults to ``<parsed dir>/<statement name>.csv``.
    """
    path = Path(path) if path is not None else get_parsed_dir() / f"{statement.name}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)

    single_category, multi_category = _split_by_category(statement.expenses)
    grand_total = 0.0
    row_number = 0

    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        _write_header(writer, statement)
        carded = _write_card_balances(writer, statement)
        _write_rewards(writer, statement.rewards)
        _write_credits(writer, [c for c in statement.credits if c.card not in carded])

        for category_id in _section_order(single_category):
            writer.writerow([category_section_label(category_id)])
            subtotal, row_number = _write_transactions(
                writer, single_category[category_id], row_number
            )
            label = (
                CATEGORIES.get(category_id, UNCATEGORIZED_LABEL)
                if category_id is not None
                else UNCATEGORIZED_LABEL
            )
            category_cell = str(category_id) if category_id is not None else "-"
            writer.writerow([format_total_row(subtotal, category_cell, label)])
            writer.writerow([])
            grand_total += subtotal

        if multi_category:
            writer.writerow([MULTI_CATEGORY_LABEL])
            subtotal, row_number = _write_transactions(writer, multi_category, row_number)
            writer.writerow([format_total_row(subtotal, "", MULTI_CATEGORY_LABEL)])
            writer.writerow([])
            grand_total += subtotal

        writer.writerow([format_total_row(grand_total, "", "")])

    logger.info("wrote %s", path)
    return grand_total


def _split_by_category(
    expenses: list[Transaction],
) -> tuple[dict[int | None, list[Transaction]], list[Transaction]]:
    """Bucket transactions by single category id, splitting out multi-category ones."""
    by_category: dict[int | None, list[Transaction]] = {}
    multi_category: list[Transaction] = []
    for transaction in expenses:
        if isinstance(transaction.category, tuple):
            multi_category.append(transaction)
        else:
            by_category.setdefault(transaction.category, []).append(transaction)
    return by_category, multi_category


def _section_order(by_category: dict[int | None, list[Transaction]]) -> list[int | None]:
    """Return category ids in ascending order, with uncategorized (None) last."""
    ordered: list[int | None] = [*sorted(c for c in by_category if c is not None)]
    if None in by_category:
        ordered.append(None)
    return ordered


def _write_header(writer: CsvWriter, statement: Statement) -> None:
    if statement.statement_date:
        date_cell = " " + statement.statement_date.strftime("%Y-%m-%d")
        writer.writerow([_STATEMENT_DATE_HEADER, date_cell])
        writer.writerow([])
    writer.writerow(_COLUMN_HEADER)


def _write_card_balances(writer: CsvWriter, statement: Statement) -> set[str]:
    """Write one block per card with printed balances; return the cards written.

    Each block: previous balance, the card's credits, then credits, spending and
    the resulting balance, and the statement's own balance with ``ok`` or how
    far it is off.
    """
    for card, balance in statement.balances.items():
        credits, spending = statement.card_credits_and_spending(card)
        computed = statement.card_computed_balance(card)
        writer.writerow(["", format_amount(balance.previous), _PREVIOUS_BALANCE, "", card])
        for credit in statement.credits:
            if credit.card == card:
                _write_credit(writer, credit)
        writer.writerow(["", f"{-credits:.2f}", _CREDITS_TOTAL, _TOTAL_MARK, card])
        writer.writerow(["", f"{spending:.2f}", _SPENDING_TOTAL, _TOTAL_MARK, card])
        writer.writerow(["", f"{computed:.2f}", _COMPUTED_BALANCE, _TOTAL_MARK, card])
        writer.writerow(
            [
                "",
                format_amount(balance.stated_total),
                _STATED_BALANCE,
                _balance_status(balance, computed),
                card,
            ]
        )
        writer.writerow([])
    return set(statement.balances)


def _write_rewards(writer: CsvWriter, rewards: RewardsBalance | None) -> None:
    """Write the rewards block: the printed figures, the computed balance, then the
    printed balance with ``ok`` or how far off it is."""
    if rewards is None:
        return
    used = None if rewards.used is None else 0.0 - rewards.used  # never "-0.00"
    for amount, remark in (
        (rewards.previous, _REWARDS_ROWS["previous"]),
        (rewards.earned, _REWARDS_ROWS["earned"]),
        (used, _REWARDS_ROWS["used"]),
        (rewards.adjustment, _REWARDS_ROWS["adjustment"]),
    ):
        writer.writerow(["", format_amount(amount), remark, "", ""])
    computed = rewards.computed_current()
    writer.writerow(["", f"{computed:.2f}", _REWARDS_COMPUTED, _TOTAL_MARK, ""])
    status = _status(rewards.cents_off())
    writer.writerow(["", format_amount(rewards.current), _REWARDS_ROWS["current"], status, ""])
    writer.writerow([])


def _balance_status(balance: CardBalance, computed: float) -> str:
    """Return ``ok``, ``off <stated - computed>``, or ``missing``."""
    return _status(balance.cents_off(computed))


def _status(difference: int | None) -> str:
    """Return ``ok``, ``off <difference in units>``, or ``missing`` for a difference in cents."""
    if difference is None:
        return "missing"
    return "ok" if difference == 0 else f"off {difference / 100:.2f}"


def _write_credits(writer: CsvWriter, credits: list[Credit]) -> None:
    for credit in credits:
        _write_credit(writer, credit)
    if credits:
        writer.writerow([])


def _write_credit(writer: CsvWriter, credit: Credit) -> None:
    writer.writerow(
        [
            credit.date.strftime("%m/%d"),
            f"{-credit.cost:.2f}",
            credit.remark,
            credit.kind,
            credit.card or "",
        ]
    )


def _write_transactions(
    writer: CsvWriter, transactions: list[Transaction], row_number: int
) -> tuple[float, int]:
    """Write transactions sorted by remark then date; return (subtotal, next row number)."""
    subtotal = 0.0
    for transaction in sorted(transactions, key=lambda t: (t.remark.lower(), t.date)):
        row_number += 1
        writer.writerow(
            [
                format_expense_row(
                    row_number,
                    transaction.date,
                    transaction.cost,
                    transaction.remark,
                    transaction.category,
                    transaction.card,
                )
            ]
        )
        subtotal += transaction.cost
    return subtotal, row_number
