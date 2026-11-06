"""Maintenance passes over the rule files: merge rules, sort and suggest brands.

These are housekeeping commands run manually (``trex rules ...``), not part of
the parse pipeline.
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from trex.categorize.brands import (
    BRAND_KEY_PREFIX,
    BrandMatcher,
    build_group_regex,
    escape_literal,
    normalize_brand,
    pick_representative_remark,
    read_brand_rows,
    unescape_literal,
    write_brand_rows,
)
from trex.categorize.rules import CatRule, CatRules, read_rule_file
from trex.config import get_brands_file, get_cat_file
from trex.log import get_logger

logger = get_logger(__name__)

#: Shortest common prefix a group may be fused on. Below this the fused pattern
#: is nearly all ``.*`` and swallows unrelated merchants — which is how
#: ``J-?? JAPANESE FOOD M??`` once became ``J-.* Singapore``.
MIN_FUSED_PREFIX = 6


@dataclass
class _RuleGroupCandidate:
    """A cat.csv rule annotated for merging: its literal form, key and category ids."""

    pattern: str
    category_ids: set[int]
    card_ids: set[int]
    literal: str | None
    compiled: re.Pattern[str] | None
    key: str = field(default="")


class _UnionFind:
    """Disjoint-set over rule indices, used to merge rules into groups."""

    def __init__(self, size: int) -> None:
        self._parent = list(range(size))

    def find(self, item: int) -> int:
        while self._parent[item] != item:
            self._parent[item] = self._parent[self._parent[item]]
            item = self._parent[item]
        return item

    def union(self, a: int, b: int) -> None:
        root_a, root_b = self.find(a), self.find(b)
        if root_a != root_b:
            self._parent[root_a] = root_b

    def groups(self) -> list[list[int]]:
        """Return the members of each set, as lists of indices."""
        grouped: defaultdict[int, list[int]] = defaultdict(list)
        for item in range(len(self._parent)):
            grouped[self.find(item)].append(item)
        return list(grouped.values())


def regroup_cat_file(path: str | Path | None = None) -> int:
    """Merge cat.csv rules that describe the same merchant. Returns the new rule count.

    Two rules merge when they share a normalized remark key, or when one is a
    regex that already matches the other's literal text. Each merged group is
    rewritten as a single pattern that still matches every original remark.
    """
    path = Path(path) if path is not None else get_cat_file()
    candidates = _read_candidates(path)
    if not candidates:
        logger.info("no rules to regroup in %s", path)
        return 0

    merged = _merge_candidates(candidates)
    _write_rules(path, merged)
    logger.info("regrouped %s: %d rows -> %d rules", path, len(candidates), len(merged))
    return len(merged)


def _read_candidates(path: Path) -> list[_RuleGroupCandidate]:
    """Read cat.csv into merge candidates; its malformed rows are dropped, as on any load."""
    candidates: list[_RuleGroupCandidate] = []
    for rule in read_rule_file(path):
        literal = unescape_literal(rule.pattern)
        compiled = rule.regex if literal is None else None
        candidates.append(
            _RuleGroupCandidate(
                rule.pattern, set(rule.category_ids), rule.card_ids, literal, compiled
            )
        )
    return candidates


def _merge_candidates(candidates: list[_RuleGroupCandidate]) -> list[CatRule]:
    """Union candidates by shared key and by regex coverage, then fuse each group."""
    matcher = BrandMatcher()
    groups = _UnionFind(len(candidates))

    first_index_for_key: dict[str, int] = {}
    for index, candidate in enumerate(candidates):
        candidate.key = matcher.normalize_remark_key(candidate.literal or candidate.pattern)
        seen_at = first_index_for_key.setdefault(candidate.key, index)
        if seen_at != index:
            groups.union(index, seen_at)

    for index, candidate in enumerate(candidates):
        if candidate.compiled is None:
            continue
        for other_index, other in enumerate(candidates):
            if index == other_index or other.literal is None:
                continue
            if candidate.compiled.match(other.literal):
                groups.union(index, other_index)

    merged: list[CatRule] = []
    for members in groups.groups():
        merged.extend(_fuse_group([candidates[i] for i in members]))
    return merged


def _fuse_group(members: list[_RuleGroupCandidate]) -> list[CatRule]:
    """Collapse one merge group into a rule covering all its remarks.

    Returns the members unchanged when fusing them would produce a pattern too
    generic to be safe (see `MIN_FUSED_PREFIX`).
    """
    literals = [m.literal for m in members if m.literal is not None]
    regexes = [m.pattern for m in members if m.literal is None]

    category_ids: set[int] = set()
    card_ids: set[int] = set()
    brand: str | None = None
    for member in members:
        category_ids |= member.category_ids
        card_ids |= member.card_ids
        if brand is None and member.key.startswith(BRAND_KEY_PREFIX):
            brand = member.key[len(BRAND_KEY_PREFIX) :]

    if _is_too_generic(literals, regexes, brand):
        logger.info(
            "left %d rules unfused: common prefix %r is shorter than %d characters",
            len(members),
            os.path.commonprefix(literals),
            MIN_FUSED_PREFIX,
        )
        return [CatRule(m.pattern, sorted(m.category_ids), m.card_ids) for m in members]

    return [CatRule(_fuse_pattern(literals, regexes, brand), sorted(category_ids), card_ids)]


def _is_too_generic(literals: list[str], regexes: list[str], brand: str | None) -> bool:
    """True if fusing this group would rest on too short a common prefix.

    A group containing regexes becomes an alternation rather than a
    prefix + ``.*``, so it is never at risk. Neither is a group with no common
    prefix at all and a brand to anchor on. Everything else is judged on the
    prefix alone: ``Disney Plus`` and ``DISNEY PLUS`` share only ``D``, and
    fusing them yields ``D.*``.
    """
    if regexes or len(literals) < 2:
        return False
    prefix = os.path.commonprefix(literals)
    if brand and not prefix:
        return False
    return len(prefix) < MIN_FUSED_PREFIX


def _fuse_pattern(literals: list[str], regexes: list[str], brand: str | None) -> str:
    """Return one pattern matching every literal and every regex in the group."""
    if not regexes:
        return build_group_regex(sorted(set(literals)), brand=brand)

    escaped = [escape_literal(literal) for literal in sorted(set(literals))]
    alternatives = drop_regexes_covered_by_others(list(dict.fromkeys([*regexes, *escaped])))
    if len(alternatives) == 1:
        return alternatives[0]
    return "(?:" + "|".join(alternatives) + ")"


def drop_regexes_covered_by_others(patterns: list[str]) -> list[str]:
    """Return `patterns` with any pattern another one already matches removed."""
    compiled = [_compile_or_none(pattern) for pattern in patterns]
    samples = [pick_representative_remark(pattern) for pattern in patterns]

    def covers(broader: int, narrower: int) -> bool:
        regex, sample = compiled[broader], samples[narrower]
        return regex is not None and sample is not None and regex.match(sample) is not None

    kept: list[int] = []
    for index in range(len(patterns)):
        if any(patterns[index] == patterns[j] or covers(j, index) for j in kept):
            continue
        kept = [j for j in kept if not (covers(index, j) and not covers(j, index))]
        kept.append(index)
    return [patterns[index] for index in kept]


def _compile_or_none(pattern: str) -> re.Pattern[str] | None:
    try:
        return re.compile(pattern)
    except re.error:
        return None


def _write_rules(path: Path, rules: list[CatRule]) -> None:
    """Write rules to cat.csv in canonical order (first category, then pattern)."""
    rule_set = CatRules(path)
    rule_set.rules = rules
    rule_set.write(path)


# --- brands.csv maintenance ---------------------------------------------


def sort_brands_file(path: str | Path | None = None) -> int:
    """Rewrite brands.csv sorted by normalized brand name. Returns the brand count."""
    path = Path(path) if path is not None else get_brands_file()
    header, rows = read_brand_rows(path)
    rows.sort(key=lambda row: normalize_brand(row[0]))
    write_brand_rows(path, header, rows)
    logger.info("sorted %s: %d brands", path, len(rows))
    return len(rows)


def suggest_brands(
    min_keys: int = 3,
    min_length: int = 4,
    top: int = 30,
    cat_path: str | Path | None = None,
) -> list[tuple[int, str, str]]:
    """Return tokens worth adding to brands.csv, most valuable first.

    A token qualifies when it appears in at least `min_keys` distinct remark
    groups and is not already a brand — adding it collapses those groups into
    one rule. Each entry is (distinct group count, token, example remark).
    """
    cat_path = Path(cat_path) if cat_path is not None else get_cat_file()
    matcher = BrandMatcher()

    remarks = [c.literal for c in _read_candidates(cat_path) if c.literal is not None]
    if not remarks:
        return []

    known = {normalize_brand(brand) for brand in matcher.brands}
    keys_by_token: defaultdict[str, set[str]] = defaultdict(set)
    examples_by_token: defaultdict[str, set[str]] = defaultdict(set)

    for remark in remarks:
        key = matcher.normalize_remark_key(remark)
        if key.startswith(BRAND_KEY_PREFIX):
            continue
        for token in re.split(r"[^A-Za-z0-9'&]+", remark):
            if len(token) < min_length or token.isdigit() or normalize_brand(token) in known:
                continue
            keys_by_token[token.upper()].add(key)
            examples_by_token[token.upper()].add(remark)

    candidates = sorted(
        ((len(keys), token) for token, keys in keys_by_token.items() if len(keys) >= min_keys),
        reverse=True,
    )
    return [
        (count, token, sorted(examples_by_token[token])[0]) for count, token in candidates[:top]
    ]
