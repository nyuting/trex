"""Dataclasses passed between the extract, categorize, parse and summary stages.

These replace the positional tuples and ad-hoc dicts the pipeline used to pass
around, so a call site shows what each field means without a lookup.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

#: A transaction's category is a single id, several ids, or None when unmatched.
Category = int | tuple[int, ...] | None


@dataclass
class Transaction:
    """One spending line item on a statement."""

    date: datetime | None
    cost: float
    remark: str
    category: Category = None
    source: str | None = None

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

    date: datetime | None
    cost: float
    remark: str
    kind: str = ""
    source: str | None = None


@dataclass
class Statement:
    """One statement: its issuer, period end date, and classified rows."""

    name: str
    issuer: str
    statement_date: datetime | None = None
    expenses: list[Transaction] = field(default_factory=list)
    credits: list[Credit] = field(default_factory=list)

    def total_expenses(self) -> float:
        """Return the sum of all expense costs."""
        return sum(transaction.cost for transaction in self.expenses)


@dataclass
class ParsedExpenseRow:
    """One expense line read back out of a parsed CSV.

    ``category_ids`` is empty for rows still marked ``-`` (uncategorized), which
    is how reconcile detects that a human categorized a row by hand.
    """

    date: str
    cost: float
    remark: str
    category_ids: list[int]
    source: str
