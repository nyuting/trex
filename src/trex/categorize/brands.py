"""brands.csv and remark normalization.

A "key" is the stable part of a remark — the merchant, with branch codes, city
suffixes and reference numbers stripped. Remarks sharing a key describe the same
merchant and so can share one cat.csv rule.
"""

from __future__ import annotations

import csv
import os
import re
from pathlib import Path

from trex.config import get_brands_file
from trex.log import get_logger

logger = get_logger(__name__)

BRAND_KEY_PREFIX = "BRAND:"

#: Regex metacharacters that `escape_literal` protects and `unescape_literal` reverses.
REGEX_METACHARACTERS = set(r".^$*+?{}[]\|()")

#: What a backslash may precede in a rule that is still plain text. Wider than
#: `REGEX_METACHARACTERS` because manually written rules also escape spaces and
#: hyphens, and a pattern rejected here is treated as an opaque regex and so
#: never gets grouped.
ESCAPABLE_CHARACTERS = REGEX_METACHARACTERS | {" ", "-"}

#: A trailing PayNow/phone/transaction reference, which varies per transaction
#: and so is not part of the merchant's identity.
TRAILING_REFERENCE = re.compile(r"\s*\d{6,}\s*$")


def read_brands(path: str | Path | None = None) -> list[str]:
    """Return the brand names in brands.csv, in file order."""
    path = Path(path) if path is not None else get_brands_file()
    with open(path, newline="") as handle:
        reader = csv.reader(handle)
        next(reader, None)
        return [row[0] for row in reader if row and row[0]]


def normalize_brand(name: str) -> str:
    """Return a brand name with whitespace/apostrophes removed, lowercased.

    Used for comparison and sorting so ``"Mc Donald's"`` and ``"McDonalds"``
    collapse to the same token.
    """
    return re.sub(r"[\s']+", "", name).lower()


def build_brand_pattern(brand: str) -> re.Pattern[str]:
    """Return a case-insensitive pattern matching `brand` however it is spaced.

    A trailing ``*`` in the brand name means "must end at a word boundary",
    which stops e.g. ``FAIRPRICE`` matching ``FAIRPRICEXPRESS``.
    """
    parts = re.split(r"[\s']+", brand)
    pattern = r"\b" + r"[\s']*".join(re.escape(part) for part in parts if part)
    if "*" in brand:
        pattern += r"(?:[^a-zA-Z0-9]|$)"
    return re.compile(pattern, re.IGNORECASE)


class BrandMatcher:
    """Matches remarks against the brand list, longest brand name first."""

    def __init__(self, brands: list[str] | None = None) -> None:
        self.brands = brands if brands is not None else read_brands()
        self._patterns = [
            (brand, build_brand_pattern(brand))
            for brand in sorted(self.brands, key=lambda b: -len(normalize_brand(b)))
        ]

    def match(self, remark: str) -> str | None:
        """Return the brand appearing in `remark`, or None."""
        for brand, pattern in self._patterns:
            if pattern.search(remark):
                return brand
        return None

    def normalize_remark_key(self, remark: str) -> str:
        """Return the grouping key for a remark.

        A brand hit wins outright. Otherwise strip the varying tail: PayNow
        reference digits, ``" - branch"`` suffixes, a trailing ``Singapore``,
        and trailing ``-code`` fragments.
        """
        brand = self.match(remark)
        if brand:
            return f"{BRAND_KEY_PREFIX}{brand}"
        if remark.startswith(("SEND MONEY TO ", "SEND EGIFT ")):
            return remark
        if remark.startswith("PAYNOW "):
            return _paynow_key(remark)

        stripped = re.sub(r"\s+Singapore\s*$", "", remark, flags=re.IGNORECASE).strip()
        if " - " in stripped:
            return stripped.split(" - ", 1)[0].strip()
        parenthesized = re.match(r"^\d+-\S+\s+\((.+)\)$", stripped)
        if parenthesized:
            return f"({parenthesized.group(1)})"
        if "-" in stripped:
            return stripped.rsplit("-", 1)[0].strip()
        return TRAILING_REFERENCE.sub("", stripped).strip()


def _paynow_key(remark: str) -> str:
    """Return a PayNow remark truncated before its elided reference (``...``).

    A phone number written straight after the payee's name is dropped too, so
    ``PAYNOW TO LU HUI97124705`` and ``PAYNOW TO LU HUI 97124705`` agree.
    """
    truncated = remark.split("...", 1)[0]
    if "-" in truncated:
        truncated = truncated.split("-", 1)[0]
    return TRAILING_REFERENCE.sub("", truncated).strip()


# --- literal <-> regex conversion ---------------------------------------


def escape_literal(text: str) -> str:
    """Return `text` escaped so it matches itself as a regex.

    Deliberately narrower than `re.escape`: only true metacharacters are
    escaped, which keeps cat.csv readable and makes `unescape_literal` exact.
    """
    return "".join("\\" + char if char in REGEX_METACHARACTERS else char for char in text)


def unescape_literal(pattern: str) -> str | None:
    """Return the plain text `pattern` matches, or None if it is a real regex."""
    out: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "\\":
            if index + 1 >= len(pattern) or pattern[index + 1] not in ESCAPABLE_CHARACTERS:
                return None
            out.append(pattern[index + 1])
            index += 2
            continue
        if char in REGEX_METACHARACTERS:
            return None
        out.append(char)
        index += 1
    return "".join(out)


def pick_representative_remark(pattern: str) -> str | None:
    """Return a string `pattern` would match, or None if it is too complex.

    Used to test whether one pattern subsumes another without enumerating
    everything either could match.
    """
    simplified = re.sub(r"\.[*+]", "", pattern)
    out: list[str] = []
    index = 0
    while index < len(simplified):
        char = simplified[index]
        if char == "\\":
            if index + 1 >= len(simplified) or simplified[index + 1] not in ESCAPABLE_CHARACTERS:
                return None
            out.append(simplified[index + 1])
            index += 2
            continue
        if char == ".":
            out.append("a")
            index += 1
            continue
        if char in REGEX_METACHARACTERS:
            return None
        out.append(char)
        index += 1
    return "".join(out)


def build_group_regex(remarks: list[str], brand: str | None = None) -> str:
    """Return one regex covering every remark in `remarks`.

    Built from their common prefix and suffix with ``.*`` between; when there is
    no common prefix but a known brand, the brand anchors the pattern instead.
    """
    if len(remarks) == 1:
        return escape_literal(remarks[0])

    common_prefix = os.path.commonprefix(remarks)
    common_suffix = os.path.commonprefix([remark[::-1] for remark in remarks])[::-1]

    # Prefix and suffix must not overlap on the shortest remark.
    shortest = min(len(remark) for remark in remarks)
    overlap = len(common_prefix) + len(common_suffix) - shortest
    if overlap > 0:
        common_suffix = common_suffix[overlap:]

    if not common_prefix and brand:
        head = ".*" + escape_literal(brand)
    else:
        head = escape_literal(common_prefix)

    parts = [head, ".*"]
    if common_suffix:
        parts.append(escape_literal(common_suffix))
    return "".join(parts)
