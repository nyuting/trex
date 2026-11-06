"""Conversions between a CSV cell and its value, shared by the rule files and the parsed CSVs."""

from __future__ import annotations

from collections.abc import Iterable


def parse_id_list(cell: str | None) -> list[int]:
    """Return the integers in a comma-separated cell such as ``"1,4"``.

    Non-numeric fragments are dropped, so ``"-"`` and ``""`` both yield ``[]``.
    """
    cell = (cell or "").strip()
    if not cell:
        return []
    return [int(part) for part in cell.split(",") if part.strip().isdigit()]


def format_id_list(ids: Iterable[int]) -> str:
    """Return the comma-separated cell for `ids`, in the order given.

    Order is kept because a category list's first id is its primary category;
    sort a set before passing it in.
    """
    return ",".join(str(id_) for id_ in ids)


def format_amount(amount: float | None) -> str:
    """Return an amount as a two-decimal cell, or ``""`` for None."""
    return "" if amount is None else f"{amount:.2f}"
