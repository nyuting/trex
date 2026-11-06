"""IHP claims exports: reading, validation and merging exports."""

from __future__ import annotations

from datetime import datetime

import pytest

from trex.healthcare import claims

from .ihp import export_line, write_export


def test_an_export_is_read_by_column_name(tmp_path):
    path = write_export(
        tmp_path,
        "IHPY10.xls",
        export_line(),
        export_line(
            reference="H00000000002",
            visit="10/02/2026",
            received="12/02/2026",
            provider="BIG HOSPITAL",
            benefit="GROUP HOSPITAL & SURGICAL",
            incurred="12,345.60",
            payable="10,000.00",
            status="ProcessedDate 24/02/2026",
        ),
    )
    panel, approved = claims.read_ihp_export(path)
    assert (panel.reference, panel.plan, panel.patient, panel.status) == (
        "R00000000001",
        "Y",
        "PATIENT A",
        "panel",
    )
    assert (panel.incurred, panel.received) == (datetime(2026, 1, 5), datetime(2026, 1, 7))
    assert (panel.provider, panel.claim_type) == ("SUNNY SMILES DENTAL PTE LTD", "IHP PANEL DENTAL")
    assert (approved.incurred_amount, approved.payable_amount) == (12345.60, 10000.00)
    assert (approved.status, approved.processed) == ("approved", datetime(2026, 2, 24))


@pytest.mark.parametrize("status", ["Pending ", "Transferred ", "Rejected", "Pending Insurer"])
def test_printed_statuses_are_lower_cased(tmp_path, status):
    path = write_export(tmp_path, "IHPG10.xls", export_line(status=status))
    [read] = claims.read_ihp_export(path)
    assert read.status == status.strip().lower()
    assert read.plan == "G"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"status": "Lost in post"}, "unknown claim status"),
        ({"visit": "2026-01-05"}, "R00000000001"),
        ({"incurred": "abc"}, "R00000000001"),
        ({"payable": "90.00"}, "exceeds the bill"),
        ({"reference": ""}, "unreadable reference"),
    ],
)
def test_an_unreadable_claim_fails_naming_it(tmp_path, change, message):
    path = write_export(tmp_path, "IHPY10.xls", export_line(**change))
    with pytest.raises(ValueError, match=message):
        claims.read_ihp_export(path)


def test_an_undecided_payable_amount_is_none(tmp_path):
    path = write_export(tmp_path, "IHPG10.xls", export_line(payable="-", status="Processing"))
    [read] = claims.read_ihp_export(path)
    assert (read.payable_amount, read.status) == (None, "processing")


def test_a_reference_listed_twice_fails(tmp_path):
    path = write_export(tmp_path, "IHPY10.xls", export_line(), export_line())
    with pytest.raises(ValueError, match="listed twice"):
        claims.read_ihp_export(path)


def test_a_missing_column_fails(tmp_path):
    path = tmp_path / "IHPY10.xls"
    path.write_text("Claim History\nReference Number\tIncurred Date\n")
    with pytest.raises(ValueError, match="missing columns"):
        claims.read_ihp_export(path)


def test_a_file_not_named_as_an_export_fails(tmp_path):
    path = write_export(tmp_path, "claims.xls", export_line())
    with pytest.raises(ValueError, match="not an IHP export name"):
        claims.read_ihp_export(path)


def test_the_newest_export_wins_for_a_claim_in_several(tmp_path):
    # IHPY01 sorts first by name but was downloaded later, in the next January.
    write_export(tmp_path, "IHPY01.xls", export_line(status="ProcessedDate 03/01/2027"))
    write_export(tmp_path, "IHPY10.xls", export_line(status="Pending "))
    [read] = claims.read_all_claims(tmp_path)
    assert read.status == "approved"


def test_no_exports_means_no_claims(tmp_path):
    assert claims.read_all_claims(tmp_path) == []
