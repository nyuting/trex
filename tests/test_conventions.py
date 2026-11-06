"""Rules about the package as a whole, rather than about one module.

CLAUDE.md defers to these: dependencies run one way, in the order `RANKS`
gives, and each module's docstring says what it owns.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent.parent / "src" / "trex"

#: The dependency order, lowest first. A module may import from a lower rank or
#: from inside its own package, never sideways or up. A new module goes here.
RANKS = [
    {"config", "constants", "cells", "log", "models"},
    {"extract", "serialize", "categorize", "vcs"},
    {"parse"},
    {"healthcare", "reconcile"},
    {"summary"},
    {"__init__"},
    {"cli"},
]
RANK = {unit: rank for rank, units in enumerate(RANKS) for unit in units}
MODULES = sorted(PACKAGE.rglob("*.py"))


def unit_of(path: Path) -> str:
    """Return the top-level module or subpackage a file belongs to, e.g. ``parse``."""
    first = path.relative_to(PACKAGE).parts[0]
    return first.removesuffix(".py")


UNITS = {unit_of(path) for path in MODULES}


def imported_units(tree: ast.Module) -> set[str]:
    """Return the top-level trex units a module imports, function-level imports included."""
    units = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module == "trex":
            names = [f"trex.{alias.name}" for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        else:
            continue
        for name in names:
            parts = name.split(".")
            if parts[0] != "trex":
                continue
            # `from trex import __version__` names the package root, not a module.
            is_unit = len(parts) > 1 and parts[1] in UNITS
            units.add(parts[1] if is_unit else "__init__")
    return units


@pytest.mark.parametrize("path", MODULES, ids=lambda p: str(p.relative_to(PACKAGE)))
def test_imports_point_down_the_ranks(path):
    unit = unit_of(path)
    assert unit in RANK, f"{unit} is not in RANKS; place it in tests/test_conventions.py"
    upward = sorted(
        target
        for target in imported_units(ast.parse(path.read_text()))
        if target != unit and RANK.get(target, len(RANKS)) >= RANK[unit]
    )
    assert not upward, f"{unit} (rank {RANK[unit]}) imports {upward}, which are not below it"


@pytest.mark.parametrize("path", MODULES, ids=lambda p: str(p.relative_to(PACKAGE)))
def test_every_module_says_what_it_owns(path):
    assert ast.get_docstring(ast.parse(path.read_text())), "add a module docstring"


def test_every_ranked_unit_exists():
    assert set(RANK) <= UNITS, f"stale entries in RANKS: {sorted(set(RANK) - UNITS)}"
