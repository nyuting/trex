"""The healthcare listing, ``healthcare<year>.csv``: each medical charge beside its IHP claim.

`trex summarize` rebuilds it on every run, and `read_healthcare_marks` reads back
what the user wrote in its `match` column so each rebuild keeps it. On a charge
paired with a claim the user gives a verdict on the pair: ``checked`` or
``wrong``. On a charge no claim covers, which reads ``review``, the user gives a
mark: ``to claim`` or ``not claimable``. A charge whose remark matches
not_claimable.csv is marked ``not claimable`` until the user marks it otherwise.
"""

from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime
from itertools import takewhile
from pathlib import Path

from trex.cells import format_amount
from trex.config import get_not_claimable_file
from trex.constants import CATEGORIES, HEALTHCARE_CATEGORY_ID, UNCATEGORIZED_LABEL
from trex.healthcare.claims import Claim, read_all_claims
from trex.healthcare.matching import (
    DEFAULT_WINDOW_DAYS,
    MARK_NOT_CLAIMABLE,
    MARK_REVIEW,
    MARK_TO_CLAIM,
    MATCH_CHECK,
    MATCH_CHECKED,
    MATCH_COPAY,
    MATCH_EXACT,
    MATCH_NO_CHARGE,
    VERDICT_WRONG,
    ChargeKey,
    ClaimMatch,
    ClaimVerdicts,
    charge_key,
    match_claims,
    read_remark_regex,
)
from trex.log import get_logger
from trex.models import Category, Transaction, category_ids

logger = get_logger(__name__)

#: Columns of the listing: a card charge, the IHP claim covering it, or both. The last,
#: `not_ref`, appears only once some charge has a ref marked wrong.
# fmt: off
HEALTHCARE_COLUMNS = [
    "date", "ihp", "ref", "patient", "provider", "claim_type", "card", "remark", "category",
    "cost", "incurred", "payable", "claim_status", "match", "not_ref",
]
# fmt: on
#: The user's marks on charges no claim covers: charge -> to claim or not claimable.
UnclaimedMarks = dict[ChargeKey, str]
#: The `match` cell of a charge no claim covers, in listing order.
_UNCLAIMED_CELLS = (MARK_REVIEW, MARK_TO_CLAIM, MARK_NOT_CLAIMABLE)
#: Separator between the refs of the `not_ref` cell.
_REF_JOIN = ";"
_HEALTHCARE_TOTAL = "total"
_HEALTHCARE_UNCLAIMED = "unclaimed"
_ELSEWHERE_UNCLAIMED = "unclaimed, filed outside HEALTHCARE"
_HEALTHCARE_NOT_CLAIMABLE = "not claimable"
_LISTING_DATE_FORMAT = "%Y-%m-%d"


def healthcare_listing_name(year: int) -> str:
    """Return the listing filename, e.g. ``healthcare2026.csv``."""
    return f"{CATEGORIES[HEALTHCARE_CATEGORY_ID].lower()}{year}.csv"


def update_healthcare_listing(
    year: int,
    path: Path,
    charges: list[Transaction],
    statements_dir: Path | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    not_claimable_file: Path | None = None,
) -> Path | None:
    """Write the listing at `path`: each charge with the IHP claim covering it.

    `charges` are the year's medical charges in SGD, by date: every HEALTHCARE
    expense plus those that look medical but are filed elsewhere, which the
    listing's category column shows. Claims come from every IHP export in
    `statements_dir` (see `read_all_claims`); a charge no claim matches has no
    ref, which marks it as never claimed. Claims incurred in `year` that no
    charge matches are listed too. An unclaimed charge the user has not marked
    is marked not claimable if its remark matches `not_claimable_file`
    (not_claimable.csv by default). With no charges, a file left over from an
    earlier run is deleted instead and None is returned.
    """
    if not charges:
        if path.exists():
            path.unlink()
            logger.info("removed %s, the year no longer has any such rows", path)
        return None

    claims = read_all_claims(statements_dir)
    verdicts, marks = drop_stale_marks(*read_healthcare_marks(path), charges, claims)
    not_claimable = read_remark_regex(not_claimable_file or get_not_claimable_file())
    if not_claimable:
        for charge in charges:
            if not_claimable.search(charge.remark):
                marks.setdefault(charge_key(charge), MARK_NOT_CLAIMABLE)
    matches, unmatched = match_claims(charges, claims, window_days, verdicts)
    listed = [(charge, matches.get(index)) for index, charge in enumerate(charges)]
    write_healthcare_csv(
        listed, [claim for claim in unmatched if claim.incurred.year == year], path, verdicts, marks
    )
    cells = Counter(_match_cell(charge, match, marks) for charge, match in listed)
    logger.info(
        "wrote %s: %d of %d medical charges unclaimed (%d not yet marked, %d to claim, "
        "%d not claimable), %d to check by eye (%d exact, %d co-payments, %d checked), "
        "%d filed outside HEALTHCARE; %.2f SGD of HEALTHCARE unclaimed",
        path,
        sum(cells[cell] for cell in _UNCLAIMED_CELLS),
        len(charges),
        *(cells[cell] for cell in _UNCLAIMED_CELLS),
        cells[MATCH_CHECK],
        cells[MATCH_EXACT],
        cells[MATCH_COPAY],
        cells[MATCH_CHECKED],
        sum(not _in_healthcare(charge) for charge in charges),
        _listing_totals(listed, marks)[_HEALTHCARE_UNCLAIMED],
    )
    return path


def read_healthcare_marks(path: str | Path) -> tuple[ClaimVerdicts, UnclaimedMarks]:
    """Return the verdicts and marks the user wrote in a healthcare listing; empty if none.

    A charge row whose `match` cell reads ``checked`` confirms its ref, one
    reading ``wrong`` rejects it, and every ref in its `not_ref` cell is
    rejected too. A charge with no ref reading ``to claim`` or ``not claimable``
    is marked so. Older listings without a `not_ref` column are read as well.
    """
    path = Path(path)
    verdicts: ClaimVerdicts = {}
    marks: UnclaimedMarks = {}
    if not path.exists():
        return verdicts, marks
    for row in read_healthcare_csv(path):
        if not row.get("remark") or not row.get("cost"):
            continue
        key = ChargeKey(row["date"], row["card"], row["remark"], row["cost"])
        cell = row.get("match", "").strip().lower()
        ref = row.get("ref", "").strip()
        if ref and cell in (MATCH_CHECKED, VERDICT_WRONG):
            verdicts[(key, ref)] = cell
        elif not ref and cell in (MARK_TO_CLAIM, MARK_NOT_CLAIMABLE):
            marks[key] = cell
        for wrong in row.get("not_ref", "").split(_REF_JOIN):
            if wrong.strip():
                verdicts[(key, wrong.strip())] = VERDICT_WRONG
    return verdicts, marks


def read_healthcare_csv(path: str | Path) -> list[dict[str, str]]:
    """Return the healthcare listing's charge lines as column -> cell, in file order.

    The blank line before the totals ends them.
    """
    with open(path, newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        return [dict(zip(header, row, strict=False)) for row in takewhile(bool, reader)]


def drop_stale_marks(
    verdicts: ClaimVerdicts,
    marks: UnclaimedMarks,
    charges: list[Transaction],
    claims: list[Claim],
) -> tuple[ClaimVerdicts, UnclaimedMarks]:
    """Return the verdicts and marks that still apply, warning about each one dropped.

    A re-parse that changes a charge's remark or amount, or a claim missing from
    every export, leaves the user's mark with nothing to apply to.
    """
    keys = {charge_key(charge) for charge in charges}
    refs = {claim.reference for claim in claims}

    def kept(key: ChargeKey, ref: str, verdict: str) -> bool:
        if key in keys and (not ref or ref in refs):
            return True
        logger.warning(
            "dropping the %s mark on %s %s %s %r%s: the %s is gone; check that row again",
            verdict,
            key.date,
            key.card,
            key.cost,
            key.remark,
            f" for claim {ref}" if ref else "",
            "charge" if key not in keys else "claim",
        )
        return False

    return (
        {(key, ref): v for (key, ref), v in sorted(verdicts.items()) if kept(key, ref, v)},
        {key: mark for key, mark in sorted(marks.items()) if kept(key, "", mark)},
    )


def write_healthcare_csv(
    charges: list[tuple[Transaction, ClaimMatch | None]],
    unmatched_claims: list[Claim],
    path: str | Path,
    verdicts: ClaimVerdicts | None = None,
    marks: UnclaimedMarks | None = None,
) -> None:
    """Write the listing: one table of charges and the IHP claims covering them.

    Charges no claim covers come first: unmarked (``review``), then marked to claim, then
    marked not claimable (see `marks`), each by remark then date.
    Then those whose match needs checking by eye, then the settled ones (exact,
    co-payment, checked), each by plan, patient, remark and date. A charge's
    `not_ref` cell keeps the refs `verdicts` says are wrong for it, so
    `read_healthcare_marks` finds them on the next run. Claims no charge
    accounts for (panel visits, cash, another card) follow as rows of their
    own, matched `MATCH_NO_CHARGE`, by plan, patient and visit date.

    The file closes with totals: all HEALTHCARE charges (agreeing with the
    summary), their unclaimed part, their not-claimable part (left out of
    unclaimed), and, if there are any, the unclaimed charges filed elsewhere.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    marks = marks or {}
    wrong_refs: dict[ChargeKey, list[str]] = {}
    for (key, ref), verdict in sorted((verdicts or {}).items()):
        if verdict == VERDICT_WRONG:
            wrong_refs.setdefault(key, []).append(ref)

    rows = []
    for transaction, match in charges:
        cell = _match_cell(transaction, match, marks)
        not_refs = _REF_JOIN.join(wrong_refs.get(charge_key(transaction), []))
        row = {**_listing_cells(transaction, match), "match": cell, "not_ref": not_refs}
        rows.append((_listing_order(transaction, match, cell), row))
    rows.sort(key=lambda order_row: order_row[0])
    unmatched = sorted(unmatched_claims, key=lambda c: (c.plan, c.patient, c.incurred))

    columns = HEALTHCARE_COLUMNS if wrong_refs else HEALTHCARE_COLUMNS[:-1]
    with open(path, "w", newline="") as handle:
        table = csv.DictWriter(handle, columns, extrasaction="ignore", lineterminator="\n")
        table.writeheader()
        table.writerows(row for _, row in rows)
        table.writerows(_listing_cells(None, ClaimMatch(c, MATCH_NO_CHARGE)) for c in unmatched)
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow([])
        for label, amount in _listing_totals(charges, marks).items():
            writer.writerow([label, f"{amount:.2f}"])


def _match_cell(transaction: Transaction, match: ClaimMatch | None, marks: UnclaimedMarks) -> str:
    """Return a charge's `match` cell: its match kind, else its mark, else ``review``."""
    return match.kind if match else marks.get(charge_key(transaction), MARK_REVIEW)


def _listing_cells(transaction: Transaction | None, match: ClaimMatch | None) -> dict[str, str]:
    """Return one listing row by column, without `match`; whichever side is missing is blank.

    A charge's own cells are its `charge_key`, so `read_healthcare_marks` rebuilds
    the same key from them. A charge's date wins over its claim's.
    """
    cells: dict[str, str] = {}
    if match:
        claim = match.claim
        cells |= {
            "date": claim.incurred.strftime(_LISTING_DATE_FORMAT),
            "ihp": claim.plan,
            "ref": claim.reference,
            "patient": claim.patient,
            "provider": claim.provider,
            "claim_type": claim.claim_type,
            "incurred": format_amount(claim.incurred_amount),
            "payable": format_amount(claim.payable_amount),
            "claim_status": claim.status,
            "match": match.kind,
        }
    if transaction:
        key = charge_key(transaction)
        cells |= {
            "date": key.date,
            "card": key.card,
            "remark": key.remark,
            "category": _category_names(transaction.category),
            "cost": key.cost,
        }
    return cells


def _category_names(category: Category) -> str:
    """Return a category cell by name, e.g. ``KIDS,HEALTHCARE``; uncategorized when None."""
    ids = category_ids(category)
    return ",".join(CATEGORIES.get(id_, str(id_)) for id_ in ids) or UNCATEGORIZED_LABEL


def _listing_order(
    transaction: Transaction, match: ClaimMatch | None, cell: str
) -> tuple[int, str, str, str, datetime]:
    """Return a charge's sort key: its group, plan and patient (when matched), remark, date.

    Unclaimed charges group by their `match` cell, in `_UNCLAIMED_CELLS` order.
    """
    remark = transaction.remark.lower()
    if match is None:
        return (_UNCLAIMED_CELLS.index(cell), "", "", remark, transaction.date)
    group = len(_UNCLAIMED_CELLS) + (0 if match.kind == MATCH_CHECK else 1)
    return (group, match.claim.plan, match.claim.patient, remark, transaction.date)


def _listing_totals(
    charges: list[tuple[Transaction, ClaimMatch | None]], marks: UnclaimedMarks
) -> dict[str, float]:
    """Return the listing's closing rows, label -> SGD.

    Unclaimed counts charges no claim covers, less those marked not claimable.
    The row for charges filed outside HEALTHCARE appears only when there are any.
    """
    cells = [(t, _in_healthcare(t), _match_cell(t, m, marks)) for t, m in charges]

    def unclaimed(in_healthcare: bool) -> float:
        claimable = (MARK_REVIEW, MARK_TO_CLAIM)
        return sum(t.cost for t, here, cell in cells if here == in_healthcare and cell in claimable)

    totals = {
        _HEALTHCARE_TOTAL: sum(t.cost for t, here, _ in cells if here),
        _HEALTHCARE_UNCLAIMED: unclaimed(True),
        _HEALTHCARE_NOT_CLAIMABLE: sum(
            t.cost for t, here, cell in cells if here and cell == MARK_NOT_CLAIMABLE
        ),
    }
    if not all(here for _, here, _ in cells):
        totals[_ELSEWHERE_UNCLAIMED] = unclaimed(False)
    return totals


def _in_healthcare(transaction: Transaction) -> bool:
    return HEALTHCARE_CATEGORY_ID in category_ids(transaction.category)
