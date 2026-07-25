"""Turning a whole extracted CSV into a categorized `Statement`."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from trex.categorize.rules import CatRules
from trex.config import get_extracted_dir
from trex.constants import (
    ISSUER_CHASE,
    ISSUER_PAYLAH,
    PAYLAH_TOPUP_REMARK,
    UOB_CARD_HEADERS,
)
from trex.log import get_logger
from trex.models import Credit, Statement, Transaction
from trex.parse.rows import (
    detect_issuer,
    parse_chase_row,
    parse_paylah_row,
    parse_statement_date,
    parse_uob_row,
)

logger = get_logger(__name__)

CHASE_SOURCE_LABEL = "CHASE"
#: UOB marks a card payment (as opposed to a refund) with this description.
UOB_PAYMENT_REMARK = "PAYMT THRU E-BANK/HOMEB/CYBERB"


def find_extracted_csv(name: str, extracted_dir: str | Path | None = None) -> Path:
    """Return the extracted CSV for a statement short name."""
    extracted_dir = Path(extracted_dir) if extracted_dir else get_extracted_dir()
    matches = sorted(extracted_dir.glob(f"{name}*.csv"))
    if not matches:
        raise FileNotFoundError(f"no extracted CSV for {name!r} in {extracted_dir}")
    return matches[0]


def parse_extracted_csv(
    name: str,
    extracted_dir: str | Path | None = None,
    rules: CatRules | None = None,
) -> Statement:
    """Read the extracted CSV for `name` and return a categorized Statement."""
    path = find_extracted_csv(name, extracted_dir)
    issuer = detect_issuer(name)

    with open(path) as handle:
        rows = list(csv.reader(handle))

    statement_date = parse_statement_date(rows[0] if rows else None, issuer)
    body = rows[1:] if statement_date else rows

    if issuer == ISSUER_CHASE:
        return _parse_chase(name, body, statement_date, rules)
    if issuer == ISSUER_PAYLAH:
        return _parse_paylah(name, body, statement_date, rules)
    return _parse_uob(name, body, statement_date, rules)


def _classify(rules: CatRules | None, source: str, cost: float, remark: str):
    """Categorize a remark with the given rule set, or leave it uncategorized."""
    if rules is None:
        return None
    return rules.classify_remark(source, cost, remark)


def _parse_chase(
    name: str, rows: list[list[str]], statement_date: datetime | None, rules: CatRules | None
) -> Statement:
    """Chase: every row is an expense on the one card; credits were dropped upstream."""
    statement = Statement(name=name, issuer=ISSUER_CHASE, statement_date=statement_date)
    for fields in rows:
        row = parse_chase_row(fields, statement_date)
        if row is None:
            continue
        category = _classify(rules, CHASE_SOURCE_LABEL, row.cost, row.remark)
        statement.expenses.append(
            Transaction(row.date, row.cost, row.remark, category, CHASE_SOURCE_LABEL)
        )
    return statement


def _parse_paylah(
    name: str, rows: list[list[str]], statement_date: datetime | None, rules: CatRules | None
) -> Statement:
    """PayLah: debits are expenses; credits are refunds, except wallet top-ups.

    Top-ups mirror the spending they fund one-for-one, so they are rolled up
    into a single payment line instead of appearing as many separate credits.
    """
    statement = Statement(name=name, issuer=ISSUER_PAYLAH, statement_date=statement_date)
    source = name[:3].upper()  # PLG or PLY
    topups: list[tuple[datetime, float]] = []

    for fields in rows:
        row = parse_paylah_row(fields, statement_date)
        if row is None:
            continue
        if not row.is_credit:
            category = _classify(rules, source, row.cost, row.remark)
            statement.expenses.append(Transaction(row.date, row.cost, row.remark, category, source))
        elif row.remark.startswith(PAYLAH_TOPUP_REMARK):
            topups.append((row.date, row.cost))
        else:
            statement.credits.append(Credit(row.date, row.cost, row.remark, "refund", source))

    statement.credits.sort(key=lambda credit: credit.date)
    if topups:
        statement.credits.append(_roll_up_topups(topups, statement_date, source))
    return statement


def _roll_up_topups(
    topups: list[tuple[datetime, float]], statement_date: datetime | None, source: str
) -> Credit:
    """Combine every wallet top-up into one dated payment credit."""
    date = statement_date or max(date for date, _ in topups)
    total = sum(cost for _, cost in topups)
    return Credit(date, total, f"{PAYLAH_TOPUP_REMARK} ({len(topups)}x)", "payment", source)


def _parse_uob(
    name: str, rows: list[list[str]], statement_date: datetime | None, rules: CatRules | None
) -> Statement:
    """UOB: one PDF holds several cards, so rows are attributed to the open section."""
    statement = Statement(name=name, issuer="UOB", statement_date=statement_date)
    current_card: str | None = None
    seen_cards: set[str] = set()

    for fields in rows:
        card = _card_section(fields)
        if card is not None:
            if card not in seen_cards:
                seen_cards.add(card)
                current_card = card
            continue
        if current_card is None:
            continue

        row = parse_uob_row(fields, statement_date)
        if row is None:
            continue
        if row.is_credit:
            kind = "payment" if UOB_PAYMENT_REMARK in row.remark else "refund"
            statement.credits.append(Credit(row.date, row.cost, row.remark, kind, current_card))
        else:
            category = _classify(rules, current_card, row.cost, row.remark)
            statement.expenses.append(
                Transaction(row.date, row.cost, row.remark, category, current_card)
            )
    return statement


def _card_section(fields: list[str]) -> str | None:
    """Return the card label if this row is a card section header, else None."""
    if len(fields) == 1 and fields[0] in UOB_CARD_HEADERS:
        return UOB_CARD_HEADERS[fields[0]]
    return None
