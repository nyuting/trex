"""Dataclasses passed between the extract, categorize, parse and summary stages.

These replace the positional tuples and ad-hoc dicts the pipeline used to pass
around, so a call site shows what each field means without a lookup.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import overload

#: A transaction's category is a single id, several ids, or None when unmatched.
Category = int | tuple[int, ...] | None


@overload
def to_cents(amount: float) -> int: ...
@overload
def to_cents(amount: None) -> None: ...
@overload
def to_cents(amount: float | None) -> int | None: ...
def to_cents(amount: float | None) -> int | None:
    """Return an amount rounded to whole cents, so totals compare exactly; None stays None."""
    return None if amount is None else round(amount * 100)


def category_from_ids(category_ids: list[int]) -> Category:
    """Return a list of ids as a Category: None if empty, one id, or a tuple."""
    if not category_ids:
        return None
    if len(category_ids) == 1:
        return category_ids[0]
    return tuple(category_ids)


def category_ids(category: Category) -> list[int]:
    """Return a Category as a list of ids, empty when uncategorized."""
    if category is None:
        return []
    if isinstance(category, tuple):
        return list(category)
    return [category]


#: A row's identity across two versions of a parsed CSV: (MM/DD, cost, remark).
RowKey = tuple[str, float, str]


def row_key(date: str, cost: float, remark: str) -> RowKey:
    """Return the key that pairs a row across versions; cost is rounded as written."""
    return (date, round(cost, 2), remark)


@dataclass
class Transaction:
    """One spending line item on a statement."""

    date: datetime
    cost: float
    remark: str
    category: Category = None
    card: str | None = None

    @property
    def is_multi_category(self) -> bool:
        """True when more than one category rule matched this remark."""
        return isinstance(self.category, tuple)

    @property
    def is_uncategorized(self) -> bool:
        """True when no category rule matched this remark."""
        return self.category is None


@dataclass
class Credit:
    """A payment, refund or rebate — money coming back, shown above expenses."""

    date: datetime
    cost: float
    remark: str
    kind: str = ""
    card: str | None = None


@dataclass
class CardBalance:
    """The balances a statement prints for one card, when it prints them.

    Either is None when its line was not found, which itself suggests the
    extraction missed part of the card's section.
    """

    card: str
    previous: float | None = None
    stated_total: float | None = None

    def cents_off(self, computed: float) -> int | None:
        """Return stated minus `computed` in whole cents; None if a printed balance is missing."""
        if self.previous is None or self.stated_total is None:
            return None
        return to_cents(self.stated_total) - to_cents(computed)


@dataclass
class RewardsBalance:
    """A statement's rewards-points line (UOB's UNI$), as printed.

    ``used`` is positive, as printed; ``adjustment`` is signed. A field is None
    when its cell could not be read.
    """

    previous: float | None = None
    earned: float | None = None
    used: float | None = None
    adjustment: float | None = None
    current: float | None = None

    def computed_current(self) -> float:
        """Return previous + earned - used + adjustment, counting unread fields as 0."""
        return (
            (self.previous or 0.0)
            + (self.earned or 0.0)
            - (self.used or 0.0)
            + (self.adjustment or 0.0)
        )

    def cents_off(self) -> int | None:
        """Return printed minus computed current in hundredths; None if a field is unread."""
        inputs = (self.previous, self.earned, self.used, self.adjustment)
        if self.current is None or any(value is None for value in inputs):
            return None
        return to_cents(self.current) - to_cents(self.computed_current())


@dataclass
class Statement:
    """One statement: its issuer, period end date, and classified rows."""

    name: str
    issuer: str
    statement_date: datetime | None = None
    expenses: list[Transaction] = field(default_factory=list)
    credits: list[Credit] = field(default_factory=list)
    #: Card label -> printed balances; empty for issuers that don't carry them.
    balances: dict[str, CardBalance] = field(default_factory=dict)
    #: The rewards-points line; None for issuers or statements that don't print one.
    rewards: RewardsBalance | None = None

    def total_expenses(self) -> float:
        """Return the sum of all expense costs."""
        return sum(transaction.cost for transaction in self.expenses)

    def card_credits_and_spending(self, card: str) -> tuple[float, float]:
        """Return (credits, spending) for one card, both as positive sums."""
        credits = sum(credit.cost for credit in self.credits if credit.card == card)
        spending = sum(t.cost for t in self.expenses if t.card == card)
        return credits, spending

    def card_computed_balance(self, card: str) -> float:
        """Return previous balance - credits + spending for one card (previous 0 if not printed)."""
        balance = self.balances.get(card)
        previous = balance.previous if balance else None
        credits, spending = self.card_credits_and_spending(card)
        return (previous or 0.0) - credits + spending


@dataclass
class ParsedExpenseRow:
    """One expense line read back out of a parsed CSV.

    ``category_ids`` is empty for rows still marked ``-`` (uncategorized), which
    is how reconcile detects that a human categorized a row manually.
    """

    date: str
    cost: float
    remark: str
    category_ids: list[int]
    card: str


@dataclass
class ParsedCreditRow:
    """One row of a parsed CSV's credits block; ``cost`` is positive, as on `Credit`."""

    date: str
    cost: float
    remark: str
    kind: str
    card: str
