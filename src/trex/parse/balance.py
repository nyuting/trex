"""Checking a parsed statement against the balances the statement itself prints.

For each card: previous balance - credits + expenses must equal the stated total
balance (UOB's per-card TOTAL BALANCE, Chase's New Balance, PayLah's closing
Total). A PayLah wallet in credit prints CR balances, which read as negative,
so the same equation holds with top-ups as credits. A mismatch means a
row was lost or attributed to the wrong card somewhere between the PDF and the
parsed Statement.

A statement that prints a rewards-points line (UOB's UNI$) is checked the same
way: previous + earned - used + adjustment must equal the printed current.
"""

from __future__ import annotations

from dataclasses import dataclass

from trex.extract.uob import REWARDS_LABEL
from trex.models import Statement


@dataclass(frozen=True)
class BalanceMismatch:
    """One card whose parsed rows don't add up to its printed total balance."""

    card: str
    previous: float | None
    stated_total: float | None
    computed_total: float

    @property
    def difference(self) -> float | None:
        """Return stated minus computed, or None when there is no stated total."""
        if self.stated_total is None:
            return None
        return self.stated_total - self.computed_total

    def describe(self) -> str:
        """Return a one-line explanation suitable for a warning."""
        if self.stated_total is None:
            return f"{self.card}: no TOTAL BALANCE line; computed {self.computed_total:.2f}"
        if self.previous is None:
            return (
                f"{self.card}: no PREVIOUS BALANCE line; stated {self.stated_total:.2f}, "
                f"computed {self.computed_total:.2f} from rows alone"
            )
        return (
            f"{self.card}: stated {self.stated_total:.2f}, computed "
            f"{self.computed_total:.2f}, off by {self.difference:.2f}"
        )


def check_card_totals(statement: Statement) -> list[BalanceMismatch]:
    """Return every card whose rows don't reconcile to its printed total.

    Cards that have rows but no printed balances are reported too, and so is a
    rewards line that doesn't add up, under the card name ``UNI$``. A statement
    whose issuer prints neither (empty ``statement.balances``, no
    ``statement.rewards``) returns []. Amounts are compared in whole cents. Has
    no side effects.
    """
    mismatches = _check_rewards(statement)
    if not statement.balances:
        return mismatches

    cards = {t.card for t in statement.expenses if t.card}
    cards |= {c.card for c in statement.credits if c.card}
    for card in sorted(set(statement.balances) | cards):
        balance = statement.balances.get(card)
        computed = statement.card_computed_balance(card)
        if balance is None or balance.cents_off(computed) != 0:
            previous = balance.previous if balance else None
            stated_total = balance.stated_total if balance else None
            mismatches.append(BalanceMismatch(card, previous, stated_total, computed))
    return mismatches


def _check_rewards(statement: Statement) -> list[BalanceMismatch]:
    """Return the rewards line as a mismatch if it doesn't add up, else []."""
    rewards = statement.rewards
    if rewards is None or rewards.cents_off() == 0:
        return []
    computed = rewards.computed_current()
    return [BalanceMismatch(REWARDS_LABEL, rewards.previous, rewards.current, computed)]
