"""How often, how recently and for how much each categorization rule matches.

Computed on demand by ``trex rules stats`` rather than stored in cat.csv, so the
rule file changes only when a rule does, and re-parsing never double-counts.
The result is written to cat_stats.csv, which is untracked. Each row counts
toward the rule `CatRules.find` picks for it, the same first match
classification uses, whatever category the row ended up with.
"""

from __future__ import annotations

import csv
import statistics
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from trex.categorize.rules import CAT_CSV_HEADER, CatRule, CatRules
from trex.config import get_cat_stats_file

CAT_STATS_HEADER = [*CAT_CSV_HEADER, "count", "last_seen", "mean", "stdev"]


@dataclass
class RuleStats:
    rule: CatRule
    costs: list[float] = field(default_factory=list)
    last_seen: datetime | None = None

    @property
    def count(self) -> int:
        return len(self.costs)

    @property
    def mean(self) -> float:
        """The mean charge, 0.0 for a rule that never matched."""
        return statistics.fmean(self.costs) if self.costs else 0.0

    @property
    def stdev(self) -> float:
        """The sample standard deviation, 0.0 below two matches."""
        return statistics.stdev(self.costs) if len(self.costs) > 1 else 0.0


def compute_rule_stats(
    rows: Iterable[tuple[datetime, float, str]], rules: CatRules | None = None
) -> tuple[list[RuleStats], int]:
    """Return stats for every rule, in rule-file order, and the count of unmatched rows.

    `rows` are (date, cost, remark). Rules from cat_personal.csv are included.
    Unmatched rows are those no rule covers, such as Chase's holiday fallback
    and large uncategorized charges.
    """
    rules = rules if rules is not None else CatRules().load()
    stats_by_rule = {id(rule): RuleStats(rule) for rule in (*rules.rules, *rules.extra_rules)}
    unmatched = 0
    for date, cost, remark in rows:
        rule = rules.find(remark)
        if rule is None:
            unmatched += 1
            continue
        stats = stats_by_rule[id(rule)]
        stats.costs.append(cost)
        if stats.last_seen is None or date > stats.last_seen:
            stats.last_seen = date
    return list(stats_by_rule.values()), unmatched


def write_rule_stats(all_stats: Iterable[RuleStats], path: Path | None = None) -> Path:
    """Write cat_stats.csv: cat.csv's columns, then the stats. Returns the path written."""
    path = path or get_cat_stats_file()
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(CAT_STATS_HEADER)
        for stats in all_stats:
            writer.writerow(
                [
                    *stats.rule.to_csv_row(),
                    stats.count,
                    stats.last_seen.strftime("%Y-%m-%d") if stats.last_seen else "",
                    f"{stats.mean:.2f}",
                    f"{stats.stdev:.2f}",
                ]
            )
    return path
