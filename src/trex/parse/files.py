"""Turning a whole extracted CSV into a categorized `Statement`."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from trex.categorize.rules import CatRules
from trex.config import get_extracted_dir
from trex.constants import (
    CHASE_CARD_LABEL,
    ISSUER_CHASE,
    ISSUER_PAYLAH,
    ISSUER_UOB,
    PAYLAH_TOPUP_REMARK,
    UOB_CARD_HEADERS,
)
from trex.extract.chase import NEW_BALANCE as CHASE_NEW_BALANCE
from trex.extract.chase import PREVIOUS_BALANCE as CHASE_PREVIOUS_BALANCE
from trex.extract.paylah import CLOSING_BALANCE as PAYLAH_CLOSING_BALANCE
from trex.extract.paylah import PREVIOUS_BALANCE as PAYLAH_PREVIOUS_BALANCE
from trex.extract.uob import PREVIOUS_BALANCE, REWARDS_LABEL, SECTION_TOTAL_PREFIX
from trex.log import get_logger
from trex.models import CardBalance, Category, Credit, RewardsBalance, Statement, Transaction
from trex.parse.rows import (
    detect_issuer,
    parse_chase_row,
    parse_cost,
    parse_paylah_row,
    parse_points,
    parse_statement_date,
    parse_uob_row,
)

logger = get_logger(__name__)

#: UOB marks a card payment (as opposed to a refund) with this description.
UOB_PAYMENT_REMARK = "PAYMT THRU E-BANK/HOMEB/CYBERB"
CHASE_PAYMENT_REMARK = "AUTOMATIC PAYMENT"


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

    return PARSERS[issuer](name, body, statement_date, rules)


def _classify(rules: CatRules | None, card: str, cost: float, remark: str) -> Category:
    """Categorize a remark with the given rule set, or leave it uncategorized."""
    if rules is None:
        return None
    return rules.classify_remark(card, cost, remark)


def _parse_chase(
    name: str, rows: list[list[str]], statement_date: datetime | None, rules: CatRules | None
) -> Statement:
    """Chase: one card; negative rows are payments or statement credits.

    The account summary's previous and new balance, when the extracted CSV has
    them, are recorded in ``statement.balances`` so the parse can be checked
    against them.
    """
    statement = Statement(name=name, issuer=ISSUER_CHASE, statement_date=statement_date)
    for fields in rows:
        if len(fields) == 2 and fields[0] in (CHASE_PREVIOUS_BALANCE, CHASE_NEW_BALANCE):
            balance = statement.balances.setdefault(CHASE_CARD_LABEL, CardBalance(CHASE_CARD_LABEL))
            if fields[0] == CHASE_PREVIOUS_BALANCE:
                balance.previous = _signed_cost(fields[1])
            else:
                balance.stated_total = _signed_cost(fields[1])
            continue
        row = parse_chase_row(fields, statement_date)
        if row is None:
            continue
        if row.is_credit:
            kind = "payment" if CHASE_PAYMENT_REMARK in row.remark else "refund"
            statement.credits.append(Credit(row.date, row.cost, row.remark, kind, CHASE_CARD_LABEL))
            continue
        category = _classify(rules, CHASE_CARD_LABEL, row.cost, row.remark)
        statement.expenses.append(
            Transaction(row.date, row.cost, row.remark, category, CHASE_CARD_LABEL)
        )
    return statement


def _parse_paylah(
    name: str, rows: list[list[str]], statement_date: datetime | None, rules: CatRules | None
) -> Statement:
    """PayLah: debits are expenses; credits are refunds, except wallet top-ups.

    Top-ups mirror the spending they fund one-for-one, so they are rolled up
    into a single payment line instead of appearing as many separate credits.
    The wallet's previous and closing balance, when the extracted CSV has them,
    are recorded in ``statement.balances`` so the parse can be checked against
    them.
    """
    statement = Statement(name=name, issuer=ISSUER_PAYLAH, statement_date=statement_date)
    card = name[:3].upper()  # PLG or PLY
    topups: list[tuple[datetime, float]] = []

    for fields in rows:
        if len(fields) == 2 and fields[0] in (PAYLAH_PREVIOUS_BALANCE, PAYLAH_CLOSING_BALANCE):
            balance = statement.balances.setdefault(card, CardBalance(card))
            if fields[0] == PAYLAH_PREVIOUS_BALANCE:
                balance.previous = _signed_cost(fields[1])
            else:
                balance.stated_total = _signed_cost(fields[1])
            continue
        row = parse_paylah_row(fields, statement_date)
        if row is None:
            continue
        if not row.is_credit:
            category = _classify(rules, card, row.cost, row.remark)
            statement.expenses.append(Transaction(row.date, row.cost, row.remark, category, card))
        elif row.remark.startswith(PAYLAH_TOPUP_REMARK):
            topups.append((row.date, row.cost))
        else:
            statement.credits.append(Credit(row.date, row.cost, row.remark, "refund", card))

    statement.credits.sort(key=lambda credit: credit.date)
    if topups:
        statement.credits.append(_roll_up_topups(topups, statement_date, card))
    return statement


def _roll_up_topups(
    topups: list[tuple[datetime, float]], statement_date: datetime | None, card: str
) -> Credit:
    """Combine every wallet top-up into one dated payment credit."""
    date = statement_date or max(date for date, _ in topups)
    total = sum(cost for _, cost in topups)
    return Credit(date, total, f"{PAYLAH_TOPUP_REMARK} ({len(topups)}x)", "payment", card)


def _parse_uob(
    name: str, rows: list[list[str]], statement_date: datetime | None, rules: CatRules | None
) -> Statement:
    """UOB: one PDF holds several cards, so rows are attributed to the open section.

    Each card's printed previous and total balance are recorded in
    ``statement.balances`` so the parse can be checked against them. The UNI$
    rewards row, printed after every card section, goes to ``statement.rewards``.
    """
    statement = Statement(name=name, issuer=ISSUER_UOB, statement_date=statement_date)
    current_card: str | None = None
    seen_cards: set[str] = set()

    for fields in rows:
        if fields and fields[0] == REWARDS_LABEL:
            statement.rewards = _rewards_balance(fields[1:])
            continue

        card = _card_section(fields)
        if card is not None:
            if card not in seen_cards:
                seen_cards.add(card)
                current_card = card
                statement.balances[card] = CardBalance(card)
            continue

        total_card = _section_total_card(fields)
        if total_card is not None:
            balance = statement.balances.setdefault(total_card, CardBalance(total_card))
            balance.stated_total = _signed_cost(fields[1])
            current_card = None
            continue
        if current_card is None:
            continue

        if len(fields) == 4 and fields[2] == PREVIOUS_BALANCE:
            statement.balances[current_card].previous = _signed_cost(fields[3])
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


#: Issuer -> the function that turns its extracted rows into a Statement.
PARSERS = {
    ISSUER_CHASE: _parse_chase,
    ISSUER_PAYLAH: _parse_paylah,
    ISSUER_UOB: _parse_uob,
}


def _card_section(fields: list[str]) -> str | None:
    """Return the card label if this row is a card section header, else None."""
    if len(fields) == 1 and fields[0] in UOB_CARD_HEADERS:
        return UOB_CARD_HEADERS[fields[0]]
    return None


def _section_total_card(fields: list[str]) -> str | None:
    """Return the card a closing-total row belongs to, or None for any other row.

    A header missing from `UOB_CARD_HEADERS` comes back as the raw header text,
    so its balance is still recorded and the check reports the card as unparsed
    instead of its rows vanishing unnoticed.
    """
    if len(fields) != 2 or not fields[0].startswith(SECTION_TOTAL_PREFIX):
        return None
    header = fields[0].removeprefix(SECTION_TOTAL_PREFIX)
    return UOB_CARD_HEADERS.get(header, header)


def _rewards_balance(cells: list[str]) -> RewardsBalance:
    """Return the UNI$ row's previous, earned, used, adjustment and current figures.

    A missing or unreadable cell is left None, which fails the balance check.
    """
    values: list[float | None] = []
    for index in range(5):
        try:
            values.append(parse_points(cells[index]))
        except (IndexError, ValueError):
            logger.debug("unparsable rewards cell in %r", cells)
            values.append(None)
    return RewardsBalance(*values)


def _signed_cost(text: str) -> float | None:
    """Return a balance cell as a signed amount (``CR`` is negative), or None."""
    try:
        cost, is_credit = parse_cost(text)
    except ValueError:
        logger.debug("unparsable balance %r", text)
        return None
    return -cost if is_credit else cost
