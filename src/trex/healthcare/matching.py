"""Which card charges are medical, and which IHP claim covers each."""

from __future__ import annotations

import csv
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import NamedTuple

from trex.constants import HEALTHCARE_KEYWORDS
from trex.healthcare.claims import STATUS_PANEL, STATUS_TRANSFERRED, Claim
from trex.models import Transaction, to_cents

#: A charge matched to a claim of the same amount around its date.
MATCH_EXACT = "exact"
#: A charge near a claim's date that differs in amount but may be the same visit.
MATCH_CHECK = "check"
#: A charge that is a panel clinic's co-payment: the claim's bill less what the plan paid.
MATCH_COPAY = "copay"
#: A charge the user confirmed belongs to a claim, by marking a `MATCH_CHECK` row.
MATCH_CHECKED = "checked"
#: A claim no card charge accounts for: a panel visit, or paid in cash or on another card.
MATCH_NO_CHARGE = "no charge"
#: The user's verdict that a charge does not belong to a claim it was paired with.
VERDICT_WRONG = "wrong"
#: The user's mark on a charge no claim covers: still to be claimed.
MARK_TO_CLAIM = "to claim"
#: The user's mark on a charge no claim covers: nothing to claim (a gym pass, a premium).
MARK_NOT_CLAIMABLE = "not claimable"
#: Written on a charge no claim covers and the user has not marked yet; never read back.
MARK_REVIEW = "review"


#: Days a card may date a charge before the visit (paying upfront) and after it.
DAYS_BEFORE_VISIT = 1
DEFAULT_WINDOW_DAYS = 7
#: A charge within this fraction of a nearby claim's amount may be the same visit.
NEAR_MISS_FRACTION = 0.10
#: A panel co-payment may differ from the bill less the payable by this fraction of
#: the bill: clinics round a percentage co-pay.
COPAY_FRACTION = 0.01
#: Words in a provider's name too common to say two names are the same place.
# fmt: off
GENERIC_NAME_WORDS = frozenset([
    "THE", "AND", "PTE", "LTD", "LLP", "GROUP", "SINGAPORE", "CLINIC", "CLINICS", "CENTRE",
    "CENTER", "MEDICAL", "SURGERY", "HEALTH", "HEALTHCARE", "SPECIALIST", "SPECIALISTS",
])
# fmt: on
MEDICAL_REMARK_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(word) for word in HEALTHCARE_KEYWORDS) + r")\b", re.IGNORECASE
)


class ChargeKey(NamedTuple):
    """A charge's identity in the healthcare listing: its cells as the listing writes them."""

    #: YYYY-MM-DD
    date: str
    card: str
    remark: str
    #: two decimals
    cost: str


#: The user's verdicts on charge-claim pairs: (charge, claim ref) -> checked or wrong.
ClaimVerdicts = dict[tuple[ChargeKey, str], str]


@dataclass(frozen=True)
class ClaimMatch:
    """The claim that accounts for one charge, and how sure the match is."""

    claim: Claim
    #: one of the ``MATCH_*`` kinds
    kind: str


def charge_key(transaction: Transaction) -> ChargeKey:
    """Return the key a charge is recognised by across rebuilds of the healthcare listing."""
    t = transaction
    return ChargeKey(t.date.strftime("%Y-%m-%d"), t.card or "", t.remark, f"{t.cost:.2f}")


def looks_medical(remark: str, not_medical: re.Pattern[str] | None) -> bool:
    """True if a card remark names a hospital, clinic or other medical merchant.

    A remark `not_medical` matches never is, whatever keyword it holds: pass
    not_medical.csv's regex (see `read_remark_regex`), or None for none.
    """
    if not_medical and not_medical.search(remark):
        return False
    return bool(MEDICAL_REMARK_RE.search(remark))


def read_remark_regex(path: Path) -> re.Pattern[str] | None:
    """Return one case-insensitive regex of a CSV's ``regex`` column; None if it has none.

    A missing file has none. A file without a ``regex`` column, or a row that is
    not a valid regex, raises ValueError naming the file.
    """
    if not path.exists():
        return None
    with open(path, newline="") as handle:
        reader = csv.DictReader(handle)
        if "regex" not in (reader.fieldnames or []):
            raise ValueError(f"{path}: no regex column")
        patterns = [(row["regex"] or "").strip() for row in reader]
    patterns = [pattern for pattern in patterns if pattern]
    for pattern in patterns:
        try:
            re.compile(pattern)
        except re.error as error:
            raise ValueError(f"{path}: invalid regex {pattern!r}: {error}") from error
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE) if patterns else None


def match_claims(
    charges: list[Transaction],
    claims: list[Claim],
    window_days: int = DEFAULT_WINDOW_DAYS,
    verdicts: ClaimVerdicts | None = None,
) -> tuple[dict[int, ClaimMatch], list[Claim]]:
    """Return (charge index -> the claim covering it, the claims no charge matched).

    In order, each pass taking only charges and claims the earlier ones left:

    1. `verdicts` the user gave: a pair marked checked is matched `MATCH_CHECKED`
       (a claim may take several such charges); a pair marked wrong is never
       proposed by any pass below.
    2. `MATCH_EXACT`: the claim's bill equals the charge to the cent, incurred
       from `window_days` before the charge to a day after; closest date first,
       each claim used once.
    3. `MATCH_COPAY`: a panel claim at the provider the remark names, whose bill
       less the payable is the charge to within 1% of the bill — the co-payment.
    4. `MATCH_CHECK`: the nearest-dated claim in the window that shares a word or
       the initials of the provider's name (SGH, NHCS), or else is within 10% of
       it — unless the remark names some other claimed provider. One claim may
       explain several such charges, as when a bill is split across cards.

    Transferred claims are left out: the claim they moved to is the real one.
    """
    claims = [claim for claim in claims if claim.status != STATUS_TRANSFERRED]
    index_by_ref = {claim.reference: ki for ki, claim in enumerate(claims)}
    keys = [charge_key(charge) for charge in charges]
    matches: dict[int, ClaimMatch] = {}
    used: set[int] = set()

    wrong: set[tuple[int, int]] = set()
    for ci, key in enumerate(keys):
        for (verdict_key, ref), verdict in (verdicts or {}).items():
            if verdict_key != key or ref not in index_by_ref:
                continue
            ki = index_by_ref[ref]
            if verdict == VERDICT_WRONG:
                wrong.add((ci, ki))
            elif ci not in matches:
                matches[ci] = ClaimMatch(claims[ki], MATCH_CHECKED)
                used.add(ki)

    def assign(kind: str, fits: Callable[[Transaction, Claim], bool]) -> None:
        pairs = sorted(
            (abs((charge.date - claim.incurred).days), ci, ki)
            for ci, charge in enumerate(charges)
            if ci not in matches
            for ki, claim in enumerate(claims)
            if ki not in used
            and (ci, ki) not in wrong
            and _in_window(charge, claim, window_days)
            and fits(charge, claim)
        )
        for _, ci, ki in pairs:
            if ci in matches or ki in used:
                continue
            matches[ci] = ClaimMatch(claims[ki], kind)
            used.add(ki)

    assign(
        MATCH_EXACT, lambda charge, claim: to_cents(charge.cost) == to_cents(claim.incurred_amount)
    )
    assign(MATCH_COPAY, _is_copay)

    leftover = [(ki, claim) for ki, claim in enumerate(claims) if ki not in used]
    known_keys = set().union(*(provider_name_keys(claim.provider) for claim in claims))
    for ci, charge in enumerate(charges):
        if ci in matches:
            continue
        candidates = [(ki, claim) for ki, claim in leftover if (ci, ki) not in wrong]
        nearest = _nearest_possible_claim(charge, candidates, window_days, known_keys)
        if nearest is not None:
            matches[ci] = ClaimMatch(claims[nearest], MATCH_CHECK)
            used.add(nearest)

    return matches, [claim for ki, claim in enumerate(claims) if ki not in used]


def _in_window(charge: Transaction, claim: Claim, window_days: int) -> bool:
    """True if the charge is dated from a day before the visit to `window_days` after it."""
    earliest = claim.incurred - timedelta(days=DAYS_BEFORE_VISIT)
    return earliest <= charge.date <= claim.incurred + timedelta(days=window_days)


def _is_copay(charge: Transaction, claim: Claim) -> bool:
    """True if the charge is this panel claim's co-payment at the provider its remark names."""
    if claim.status != STATUS_PANEL or claim.payable_amount is None:
        return False
    if not set(_words(charge.remark)) & provider_name_keys(claim.provider):
        return False
    copay = claim.incurred_amount - claim.payable_amount
    return abs(charge.cost - copay) <= COPAY_FRACTION * claim.incurred_amount


def _nearest_possible_claim(
    charge: Transaction, claims: list[tuple[int, Claim]], window_days: int, known_keys: set[str]
) -> int | None:
    """Return the index of the nearest-dated claim that may be this charge's visit, or None.

    A remark naming a provider some claim names (`known_keys`) is only ever
    paired with a claim from that provider, however close another's amount is.
    """
    remark_words = set(_words(charge.remark))
    names_provider = bool(remark_words & known_keys)

    def may_be_visit(claim: Claim) -> bool:
        if remark_words & provider_name_keys(claim.provider):
            return True
        close = (
            abs(charge.cost - claim.incurred_amount) <= NEAR_MISS_FRACTION * claim.incurred_amount
        )
        return close and not names_provider

    candidates = [
        (abs((charge.date - claim.incurred).days), abs(charge.cost - claim.incurred_amount), ki)
        for ki, claim in claims
        if _in_window(charge, claim, window_days) and may_be_visit(claim)
    ]
    return min(candidates)[2] if candidates else None


def provider_name_keys(provider: str) -> set[str]:
    """Return the words a card remark may use for a provider: distinctive words and initials.

    ``NATIONAL HEART CENTRE SINGAPORE`` gives ``{"NATIONAL", "HEART", "NHCS"}``.
    """
    words = _words(provider)
    keys = {word for word in words if len(word) >= 3 and word not in GENERIC_NAME_WORDS}
    initials = "".join(word[0] for word in words if word.isalpha() and word != "AND")
    if len(initials) >= 3:
        keys.add(initials)
    return keys


def _words(text: str) -> list[str]:
    """Return the upper-cased runs of letters and digits in `text`."""
    return re.findall(r"[A-Z0-9]+", text.upper())
