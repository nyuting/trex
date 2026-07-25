"""Small shared text helpers used by both the rule files and the parsed CSVs."""

from __future__ import annotations


def parse_num_list(cell: str | None) -> list[int]:
    """Return the integers in a comma-separated cell such as ``"1,4"``.

    Non-numeric fragments are dropped, so ``"-"`` and ``""`` both yield ``[]``.
    """
    cell = (cell or "").strip()
    if not cell:
        return []
    return [int(part) for part in cell.split(",") if part.strip().isdigit()]


def format_num_list(numbers: list[int] | set[int] | tuple[int, ...]) -> str:
    """Return the canonical comma-separated cell for a collection of ids."""
    return ",".join(str(n) for n in sorted(numbers))
