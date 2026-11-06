"""Anonymised IHP claims exports, written the way the portal writes them."""

from __future__ import annotations

HEADER = [
    "NRIC",
    "Claimant Name",
    "Reference Number ",
    "Incurred Date ",
    "Received Date ",
    "Service Provider / Clinic ",
    "Claim Type Category ",
    "Benefit ",
    "Claim Reason ",
    "Admin Remarks ",
    "Incurred Amount ($) ",
    "Payable Amount ($) ",
    "Claim Status ",
    "",
]


def export_line(
    reference="R00000000001",
    visit="05/01/2026",
    received="07/01/2026",
    provider="SUNNY SMILES DENTAL PTE LTD",
    benefit="IHP PANEL DENTAL",
    incurred="80.00",
    payable="80.00",
    status="Panel ",
    patient="PATIENT A",
):
    """Return one anonymised export line."""
    cells = ["*****000A", patient, reference, visit, received, provider, "", benefit, "", ""]
    return "\t".join([*cells, incurred, payable, status])


def write_export(directory, name, *lines):
    """Write an export as the portal does: title line, header, claims, CRLF endings."""
    path = directory / name
    text = "\r\n".join([" Claim History ", "\t".join(HEADER), *lines]) + "\r\n"
    path.write_bytes(text.encode("utf-8"))
    return path
