"""cat.csv: the regex -> category rules, and the matching engine over them.

`CatRules` owns all rule state. Nothing happens at import time — construct one
and call `load()`, so tests can run against a fixture file and the real cat.csv
is never touched by accident.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from trex.config import get_cat_file, get_personal_cat_file
from trex.constants import SOURCE_IDS_BY_LABEL
from trex.log import get_logger
from trex.models import Category
from trex.text import format_num_list, parse_num_list

logger = get_logger(__name__)

CAT_CSV_HEADER = ["regex", "category", "source"]

#: Chase spending is holiday spending by definition; no rule is recorded for it.
CHASE_SOURCE_ID = 1
CHASE_FALLBACK_CATEGORY = 9

#: Unmatched non-Chase transactions under this amount are assumed to be meals.
SMALL_PURCHASE_LIMIT = 25.0
SMALL_PURCHASE_CATEGORY = 1


@dataclass
class CatRule:
    """One row of cat.csv: a remark pattern, its categories, and where it was seen.

    ``source_ids`` is a record of which cards this pattern has shown up on. It is
    informational — every rule is tried against every source.
    """

    pattern: str
    category_ids: list[int]
    source_ids: set[int] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.regex = re.compile(self.pattern)

    def matches(self, remark: str) -> bool:
        """True if this rule's pattern matches the start of `remark`."""
        return self.regex.match(remark) is not None

    def as_category(self) -> Category:
        """Return a bare id for a single-category rule, else a tuple of ids."""
        if len(self.category_ids) == 1:
            return self.category_ids[0]
        return tuple(self.category_ids)

    def to_csv_row(self) -> list[str]:
        """Return this rule as a cat.csv row."""
        return [
            self.pattern,
            ",".join(str(c) for c in self.category_ids),
            format_num_list(self.source_ids),
        ]

    def sort_key(self) -> tuple[int, str]:
        """Return cat.csv's canonical ordering key: first category, then pattern."""
        return (self.category_ids[0], self.pattern.lower())


class CatRules:
    """The cat.csv rule set: load it, match remarks against it, write it back.

    `extra_paths` are read-only rule files — cat_personal.csv by default. Their
    rules match like any other, but `write` and `flush` only ever touch `path`,
    so personal rules never migrate into cat.csv.

    `classify_remark` may add rules, which sets `dirty`; call `flush()` once at
    the end of a run to persist them.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        extra_paths: Sequence[str | Path] | None = None,
    ) -> None:
        self.path = Path(path) if path is not None else get_cat_file()
        self.extra_paths = (
            [Path(extra) for extra in extra_paths]
            if extra_paths is not None
            else [get_personal_cat_file()]
        )
        self.rules: list[CatRule] = []
        self.extra_rules: list[CatRule] = []
        self.header: list[str] = list(CAT_CSV_HEADER)
        self.dirty = False

    def __len__(self) -> int:
        return len(self.rules) + len(self.extra_rules)

    def load(self) -> CatRules:
        """Read the rule files, skipping malformed rows. Returns self for chaining."""
        self.dirty = False
        with open(self.path) as handle:
            reader = csv.reader(handle)
            self.header = next(reader, list(CAT_CSV_HEADER))
            self.rules = self._read_rules(reader, self.path)
        logger.debug("loaded %d rules from %s", len(self.rules), self.path)

        self.extra_rules = []
        for extra_path in self.extra_paths:
            if not extra_path.is_file():
                logger.debug("no extra rule file at %s", extra_path)
                continue
            with open(extra_path) as handle:
                reader = csv.reader(handle)
                next(reader, None)
                rules = self._read_rules(reader, extra_path)
            logger.debug("loaded %d rules from %s", len(rules), extra_path)
            self.extra_rules.extend(rules)
        return self

    def _read_rules(self, reader: Iterable[list[str]], path: Path) -> list[CatRule]:
        """Return the rules in an already-headered CSV reader, skipping bad rows."""
        rules = []
        for line_number, row in enumerate(reader, start=2):
            rule = self._build_rule(row, line_number, path)
            if rule is not None:
                rules.append(rule)
        return rules

    def _build_rule(self, row: list[str], line_number: int, path: Path) -> CatRule | None:
        """Return a CatRule for a cat.csv row, or None (logged) if it is unusable."""
        if len(row) != 3:
            logger.debug("%s:%d skipped: expected 3 columns, got %d", path, line_number, len(row))
            return None
        pattern, category_cell, source_cell = row
        category_ids = parse_num_list(category_cell)
        if not category_ids:
            logger.debug("%s:%d skipped: no category ids in %r", path, line_number, category_cell)
            return None
        try:
            return CatRule(pattern, category_ids, set(parse_num_list(source_cell)))
        except re.error as error:
            logger.debug("%s:%d skipped: bad regex %r (%s)", path, line_number, pattern, error)
            return None

    def find(self, remark: str) -> CatRule | None:
        """Return the first rule matching `remark`, searching cat.csv then the extras."""
        for rule in (*self.rules, *self.extra_rules):
            if rule.matches(remark):
                return rule
        return None

    def classify_remark(self, source: str | None, cost: float, remark: str) -> Category:
        """Return the category (or categories) for a transaction.

        Side effects, both of which set `dirty`: records `source` on the rule
        that matched if it is new there, and adds a DINING rule for an unmatched
        small non-Chase purchase.
        """
        source_id = SOURCE_IDS_BY_LABEL.get(source or "")
        if source_id is None:
            return None

        matched = self.find(remark)
        if matched is not None:
            self._record_source(matched, source_id)
            return matched.as_category()

        if source_id == CHASE_SOURCE_ID:
            return CHASE_FALLBACK_CATEGORY
        if cost >= SMALL_PURCHASE_LIMIT:
            return None
        return self._add_small_purchase_rule(source, source_id, remark)

    def _record_source(self, rule: CatRule, source_id: int) -> None:
        """Note that `rule` has now been seen on this card, if it hadn't been."""
        if source_id not in rule.source_ids:
            rule.source_ids.add(source_id)
            self.dirty = True

    def _add_small_purchase_rule(self, source: str | None, source_id: int, remark: str) -> Category:
        """Add (or reuse) a literal DINING rule for an unmatched small purchase."""
        pattern = re.escape(remark)
        existing = next((rule for rule in self.rules if rule.pattern == pattern), None)
        if existing is not None:
            self._record_source(existing, source_id)
            return existing.as_category()

        self.rules.append(CatRule(pattern, [SMALL_PURCHASE_CATEGORY], {source_id}))
        self.dirty = True
        logger.info("  AUTO-ADDED dining rule [%s]: %r", source, remark)
        return SMALL_PURCHASE_CATEGORY

    def add_rule(self, pattern: str, category_ids: list[int], source_ids: set[int]) -> CatRule:
        """Add a rule explicitly and mark the set dirty."""
        rule = CatRule(pattern, category_ids, set(source_ids))
        self.rules.append(rule)
        self.dirty = True
        return rule

    def flush(self) -> bool:
        """Write the rules back sorted, if anything changed. Returns True if written."""
        if not self.dirty:
            return False
        self.write(self.path)
        self.dirty = False
        logger.info("flushed %s", self.path)
        return True

    def write(self, path: str | Path) -> None:
        """Write the cat.csv rules to `path` in canonical sorted order.

        Rules from `extra_paths` are deliberately excluded — they stay in the
        file they came from.
        """
        with open(path, "w", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(self.header)
            for rule in sorted(self.rules, key=CatRule.sort_key):
                writer.writerow(rule.to_csv_row())
