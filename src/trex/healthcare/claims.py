"""Insurance claims: read IHP claims-history exports.

The IHP portal exports a claims history as ``.xls`` that is really tab-separated
text: a title line, a header line, then one claim per line. Exports are saved
in the statements directory as ``IHP<plan><MM>.xls`` — the plan letter says whose
account filed the claims (``G``, ``Y``) and MM the month it was downloaded. They
never enter the statement pipeline; `trex summarize` reads them to mark each
line of the healthcare listing with the claim that covers it (see
`trex.healthcare.matching`).
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from trex.config import get_statements_dir
from trex.log import get_logger
from trex.models import to_cents
from trex.parse.rows import parse_cost

logger = get_logger(__name__)


#: An export's filename: plan letter, then the two-digit download month.
EXPORT_NAME_RE = re.compile(r"^IHP([A-Z])(\d{2})\.xls$", re.IGNORECASE)
EXPORT_GLOB = "[Ii][Hh][Pp]*.xls"
DATE_FORMAT = "%d/%m/%Y"
REFERENCE_RE = re.compile(r"^[A-Z]\d+$")

#: The header cell that marks the header line; the title line above it is skipped.
REFERENCE_COLUMN = "Reference Number"
#: The export's columns `_read_claim` reads, by their header text.
REQUIRED_COLUMNS = (
    "Claimant Name",
    REFERENCE_COLUMN,
    "Incurred Date",
    "Received Date",
    "Service Provider / Clinic",
    "Claim Type Category",
    "Benefit",
    "Incurred Amount ($)",
    "Payable Amount ($)",
    "Claim Status",
)

#: What the portal prints for an amount not decided yet, e.g. payable while processing.
UNDECIDED = "-"
#: What the portal prints for an approved claim, followed by the date it was processed.
PROCESSED_PREFIX = "ProcessedDate"
STATUS_APPROVED = "approved"
STATUS_TRANSFERRED = "transferred"
STATUS_PANEL = "panel"
#: Every status the portal defines, lower-cased. Anything else fails the read.
CLAIM_STATUSES = frozenset(
    {
        STATUS_APPROVED,
        "rejected",
        "pending",
        "pending insurer",
        "pending with new document(s) uploaded",
        "processing",
        "processing in progress",
        STATUS_TRANSFERRED,
        STATUS_PANEL,
        "payroll",
        "log issued",
    }
)


@dataclass(frozen=True)
class Claim:
    """One insurance claim from an IHP claims-history export: a visit already claimed."""

    reference: str
    #: whose IHP account filed it — the letter after ``IHP`` in the filename (``G``, ``Y``)
    plan: str
    patient: str
    incurred: datetime
    received: datetime
    provider: str
    claim_type: str
    #: the bill, which is what a card would have been charged
    incurred_amount: float
    #: None while the insurer has not decided it (printed ``-``)
    payable_amount: float | None
    #: lower case, e.g. ``approved``, ``panel``, ``pending``, ``rejected``
    status: str
    #: when an approved claim was processed
    processed: datetime | None = None


def read_all_claims(statements_dir: Path | None = None) -> list[Claim]:
    """Return the claims in every IHP export, one per reference number.

    A claim in several exports is taken from the most recent one — the export
    whose latest received or processed date is latest — so a status that moved
    on (pending to approved) is the one kept. No exports gives [].
    """
    statements_dir = statements_dir or get_statements_dir()
    exports = [read_ihp_export(path) for path in sorted(statements_dir.glob(EXPORT_GLOB))]
    claims: dict[str, Claim] = {}
    for export in sorted((e for e in exports if e), key=_export_as_of):
        claims.update((claim.reference, claim) for claim in export)
    return sorted(claims.values(), key=lambda claim: (claim.incurred, claim.reference))


def read_ihp_export(path: str | Path) -> list[Claim]:
    """Return the claims in one IHP claims-history export, in file order.

    Raises ValueError naming the file (and the claim, where there is one) for a
    filename that names no plan, a missing column, an unreadable date or amount,
    an unknown status, a payable amount above the bill, or a repeated reference.
    """
    path = Path(path)
    name = EXPORT_NAME_RE.match(path.name)
    if not name:
        raise ValueError(f"{path.name}: not an IHP export name (IHP<plan><MM>.xls)")
    plan = name.group(1).upper()

    with open(path, newline="", encoding="utf-8-sig") as handle:
        lines = [[cell.strip() for cell in row] for row in csv.reader(handle, delimiter="\t")]
    header_index = next((i for i, row in enumerate(lines) if REFERENCE_COLUMN in row), None)
    if header_index is None:
        raise ValueError(f"{path.name}: no header line with {REFERENCE_COLUMN!r}")
    header = lines[header_index]
    missing = [column for column in REQUIRED_COLUMNS if column not in header]
    if missing:
        raise ValueError(f"{path.name}: missing columns {missing}")

    claims: list[Claim] = []
    seen: set[str] = set()
    for row in lines[header_index + 1 :]:
        if not any(row):
            continue
        claim = _read_claim(dict(zip(header, row, strict=False)), plan, path.name)
        if claim.reference in seen:
            raise ValueError(f"{path.name}: claim {claim.reference} is listed twice")
        seen.add(claim.reference)
        claims.append(claim)
    logger.info("%s: %d claims", path.name, len(claims))
    return claims


def _read_claim(cells: dict[str, str], plan: str, source: str) -> Claim:
    """Return one export line, keyed by header, as a Claim; raises ValueError naming
    what is wrong. A short line lacks its last columns, which read as blank."""

    def cell(column: str) -> str:
        return cells.get(column, "")

    reference = cell(REFERENCE_COLUMN)
    where = f"{source}: claim {reference or '(no reference)'}"
    if not REFERENCE_RE.match(reference):
        raise ValueError(f"{where}: unreadable reference number")
    try:
        incurred_amount = parse_cost(cell("Incurred Amount ($)"))[0]
        payable_cell = cell("Payable Amount ($)")
        payable_amount = None if payable_cell == UNDECIDED else parse_cost(payable_cell)[0]
        incurred = datetime.strptime(cell("Incurred Date"), DATE_FORMAT)
        received = datetime.strptime(cell("Received Date"), DATE_FORMAT)
        status, processed = _read_status(cell("Claim Status"))
    except ValueError as error:
        raise ValueError(f"{where}: {error}") from error
    if payable_amount is not None and to_cents(payable_amount) > to_cents(incurred_amount):
        raise ValueError(f"{where}: payable {payable_amount:.2f} exceeds the bill")
    return Claim(
        reference=reference,
        plan=plan,
        patient=cell("Claimant Name"),
        incurred=incurred,
        received=received,
        provider=cell("Service Provider / Clinic"),
        claim_type=cell("Benefit") or cell("Claim Type Category"),
        incurred_amount=incurred_amount,
        payable_amount=payable_amount,
        status=status,
        processed=processed,
    )


def _read_status(cell: str) -> tuple[str, datetime | None]:
    """Return (status, processed date) for a Claim Status cell."""
    if cell.startswith(PROCESSED_PREFIX):
        return STATUS_APPROVED, datetime.strptime(
            cell[len(PROCESSED_PREFIX) :].strip(), DATE_FORMAT
        )
    status = " ".join(cell.split()).lower()
    if status not in CLAIM_STATUSES:
        raise ValueError(f"unknown claim status {cell!r}")
    return status, None


def _export_as_of(claims: list[Claim]) -> datetime:
    """Return the latest date an export's claims mention: roughly when it was downloaded."""
    return max(max(claim.received, claim.processed or claim.received) for claim in claims)
