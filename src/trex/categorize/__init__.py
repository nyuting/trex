"""Assigning categories to transaction remarks.

Typical use is the module-level convenience pair, which shares one lazily loaded
rule set for a whole run::

    category = classify_remark("UOB-ONE", 12.50, "ACME COFFEE HOUSE")
    save_rules_if_changed()  # persist any rules that classifying added

Anything that needs isolation (tests, alternate rule files) should build its own
`CatRules` instead.
"""

from __future__ import annotations

from trex.categorize.brands import BrandMatcher, read_brands
from trex.categorize.regroup import regroup_cat_file, sort_brands_file, suggest_brands
from trex.categorize.rules import CatRule, CatRules
from trex.models import Category

__all__ = [
    "BrandMatcher",
    "CatRule",
    "CatRules",
    "classify_remark",
    "save_rules_if_changed",
    "get_rules",
    "read_brands",
    "regroup_cat_file",
    "reset_rules",
    "sort_brands_file",
    "suggest_brands",
]

_rules: CatRules | None = None


def get_rules() -> CatRules:
    """Return the shared rule set, loading cat.csv on first use."""
    global _rules
    if _rules is None:
        _rules = CatRules().load()
    return _rules


def reset_rules() -> None:
    """Drop the shared rule set so the next call reloads it (used after a regroup)."""
    global _rules
    _rules = None


def classify_remark(card: str | None, cost: float, remark: str) -> Category:
    """Return the category for a transaction using the shared rule set."""
    return get_rules().classify_remark(card, cost, remark)


def save_rules_if_changed() -> bool:
    """Write the shared rule set back to cat.csv if it changed."""
    return get_rules().save_if_changed()
