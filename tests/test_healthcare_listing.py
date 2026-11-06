"""The healthcare listing `trex summarize` writes: medical charges beside their IHP claims."""

from __future__ import annotations

import csv
import logging
from datetime import datetime

import pytest

from trex import summary
from trex.constants import HEALTHCARE_CATEGORY_ID
from trex.healthcare.claims import Claim
from trex.healthcare.listing import (
    healthcare_listing_name,
    read_healthcare_csv,
    read_healthcare_marks,
    write_healthcare_csv,
)
from trex.healthcare.matching import ClaimMatch

from .ihp import export_line, write_export
from .statements import build, buy, write_statement


def healthcare_path(summary_dir):
    return summary_dir / healthcare_listing_name(2026)


def read_healthcare(summary_dir):
    """Return the charge lines of the 2026 healthcare listing, [] if there is none."""
    path = healthcare_path(summary_dir)
    return read_healthcare_csv(path) if path.exists() else []


def test_the_healthcare_file_is_named_by_year():
    assert healthcare_listing_name(2026) == "healthcare2026.csv"


def test_the_healthcare_listing_holds_every_card_and_month_and_nothing_else(
    parsed_dir, summary_dir
):
    write_statement(
        parsed_dir,
        buy(1, 20, 80.0, "CLINIC JAN", 8),
        buy(2, 5, 20.0, "LUNCH", 1),
        name="UOB02",
        statement_date=datetime(2026, 2, 10),
    )
    write_statement(
        parsed_dir,
        buy(3, 3, 45.0, "PHARMACY MAR", 8, "PLG"),
        name="PLG03",
        issuer="PayLah",
        statement_date=datetime(2026, 3, 31),
    )
    summary.summarize(year=2026, parsed_dir=parsed_dir)

    rows = read_healthcare(summary_dir)
    assert [(row["remark"], row["date"], row["card"]) for row in rows] == [
        ("CLINIC JAN", "2026-01-20", "UOB-ONE"),
        ("PHARMACY MAR", "2026-03-03", "PLG"),
    ]
    built = build(parsed_dir)
    listed = sum(float(row["cost"]) for row in rows)
    assert listed == pytest.approx(built.row_total(HEALTHCARE_CATEGORY_ID))


def test_a_shared_healthcare_row_is_listed_at_its_full_cost(parsed_dir, summary_dir):
    write_statement(parsed_dir, buy(1, 6, 30.0, "FAMILY DOCTOR", (4, 8)))
    summary.summarize(year=2026, parsed_dir=parsed_dir)
    [row] = read_healthcare(summary_dir)
    assert (row["cost"], row["category"]) == ("30.00", "KIDS,HEALTHCARE")


def test_a_year_without_healthcare_writes_no_listing_and_drops_a_stale_one(parsed_dir, summary_dir):
    write_statement(parsed_dir, buy(1, 5, 10.0, "CLINIC", 8))
    summary.summarize(year=2026, parsed_dir=parsed_dir)
    assert healthcare_path(summary_dir).exists()

    write_statement(parsed_dir, buy(1, 5, 10.0, "LUNCH", 1))
    summary.summarize(year=2026, parsed_dir=parsed_dir)
    assert not healthcare_path(summary_dir).exists()


def test_with_no_ihp_exports_every_healthcare_charge_is_unclaimed(parsed_dir, summary_dir):
    write_statement(parsed_dir, buy(1, 5, 10.0, "CLINIC", 8))
    summary.summarize(year=2026, parsed_dir=parsed_dir)
    [row] = read_healthcare(summary_dir)
    assert (row["ihp"], row["ref"], row["match"]) == ("", "", "review")
    assert "unclaimed,10.00" in healthcare_path(summary_dir).read_text()


def test_healthcare_charges_are_marked_with_the_claim_covering_them(
    parsed_dir, summary_dir, tmp_path
):
    statements_dir = tmp_path / "statements"
    statements_dir.mkdir()
    write_export(
        statements_dir,
        "IHPY10.xls",
        export_line("R00000000001", "05/01/2026", "05/01/2026"),
        export_line(
            "R00000000002",
            "20/03/2026",
            "20/03/2026",
            incurred="15.00",
            payable="15.00",
            status="Pending ",
        ),
    )
    write_statement(
        parsed_dir, buy(1, 6, 80.0, "SUNNY SMILES DENTAL", 8), buy(1, 20, 30.0, "SGH", 8)
    )
    summary.summarize(year=2026, parsed_dir=parsed_dir, statements_dir=statements_dir)

    rows = read_healthcare(summary_dir)
    assert [(row["remark"], row["ihp"], row["ref"], row["match"]) for row in rows] == [
        ("SGH", "", "", "review"),
        ("SUNNY SMILES DENTAL", "Y", "R00000000001", "exact"),
        ("", "Y", "R00000000002", "no charge"),
    ]
    matched, no_charge = rows[1], rows[2]
    assert (matched["provider"], matched["cost"], matched["incurred"]) == (
        "SUNNY SMILES DENTAL PTE LTD",
        "80.00",
        "80.00",
    )
    assert (no_charge["date"], no_charge["cost"], no_charge["claim_status"]) == (
        "2026-03-20",
        "",
        "pending",
    )


def test_a_medical_charge_filed_elsewhere_is_warned_about(parsed_dir, caplog):
    write_statement(parsed_dir, buy(1, 5, 25.0, "SGH PHARMACY", 6), buy(1, 6, 9.0, "LUNCH", 1))
    with caplog.at_level(logging.WARNING, logger="trex.summary"):
        summary.summarize(year=2026, parsed_dir=parsed_dir)
    warnings = [r.getMessage() for r in caplog.records if "looks medical" in r.getMessage()]
    assert len(warnings) == 1
    assert "SGH PHARMACY" in warnings[0]


def test_not_medical_csv_keeps_a_merchant_out_of_the_warning_and_the_listing(
    parsed_dir, summary_dir, caplog
):
    (parsed_dir.parent / "categories").mkdir()
    (parsed_dir.parent / "categories" / "not_medical.csv").write_text("regex\n\\bSF - SGH\\b\n")
    write_statement(parsed_dir, buy(1, 5, 6.0, "SF - SGH", 1), buy(1, 6, 25.0, "SGH PHARMACY", 6))
    with caplog.at_level(logging.WARNING, logger="trex.summary"):
        summary.summarize(year=2026, parsed_dir=parsed_dir)
    assert "SF - SGH" not in caplog.text
    assert [row["remark"] for row in read_healthcare(summary_dir)] == ["SGH PHARMACY"]


def test_a_medical_charge_filed_elsewhere_is_listed_but_kept_out_of_the_totals(
    parsed_dir, summary_dir
):
    write_statement(parsed_dir, buy(1, 5, 25.0, "SGH PHARMACY", 6), buy(1, 6, 40.0, "DENTIST", 8))
    summary.summarize(year=2026, parsed_dir=parsed_dir)
    rows = read_healthcare(summary_dir)
    assert [(row["remark"], row["category"]) for row in rows] == [
        ("DENTIST", "HEALTHCARE"),
        ("SGH PHARMACY", "PERSON_B"),
    ]
    text = healthcare_path(summary_dir).read_text()
    assert "\ntotal,40.00\nunclaimed,40.00\n" in text
    assert '"unclaimed, filed outside HEALTHCARE",25.00' in text


# --- marks the user sets --------------------------------------------------


def summarize_with_a_near_miss(parsed_dir, tmp_path, *charges):
    """Write one pending 100.00 dental claim and `charges`, then summarize; return the dir."""
    statements_dir = tmp_path / "statements"
    statements_dir.mkdir(exist_ok=True)
    pending = export_line(
        visit="05/01/2026", incurred="100.00", payable="100.00", status="Pending "
    )
    write_export(statements_dir, "IHPY10.xls", pending)
    write_statement(parsed_dir, *charges)
    summary.summarize(year=2026, parsed_dir=parsed_dir, statements_dir=statements_dir)
    return statements_dir


def mark_check_row(summary_dir, verdict):
    """Set the `match` cell of every row the listing left at `check`, as the user would."""
    _mark_rows(summary_dir, verdict, "match", "check")


def mark_row(summary_dir, remark, verdict):
    """Set the `match` cell of every row whose remark is `remark`, as the user would."""
    _mark_rows(summary_dir, verdict, "remark", remark)


def _mark_rows(summary_dir, verdict, column, value):
    """Set `match` to `verdict` on the charge lines whose `column` holds `value`."""
    path = healthcare_path(summary_dir)
    with open(path, newline="") as handle:
        lines = list(csv.reader(handle))
    header = lines[0]
    for line in lines[1:]:
        if not line:  # the totals follow a blank line
            break
        if line[header.index(column)] == value:
            line[header.index("match")] = verdict
    with open(path, "w", newline="") as handle:
        csv.writer(handle, lineterminator="\n").writerows(lines)


def test_a_checked_row_stays_checked_after_rebuilding(parsed_dir, summary_dir, tmp_path):
    dentist = buy(1, 6, 95.0, "SUNNY SMILES DENTAL", 8)
    statements_dir = summarize_with_a_near_miss(parsed_dir, tmp_path, dentist)
    [row] = read_healthcare(summary_dir)
    assert row["match"] == "check"

    mark_check_row(summary_dir, "checked")
    summary.summarize(year=2026, parsed_dir=parsed_dir, statements_dir=statements_dir)
    [row] = read_healthcare(summary_dir)
    assert (row["ref"], row["match"]) == ("R00000000001", "checked")


def test_a_wrong_row_stays_rejected_after_rebuilding(parsed_dir, summary_dir, tmp_path):
    dentist = buy(1, 6, 95.0, "SUNNY SMILES DENTAL", 8)
    statements_dir = summarize_with_a_near_miss(parsed_dir, tmp_path, dentist)
    mark_check_row(summary_dir, "wrong")
    for _ in range(2):
        summary.summarize(year=2026, parsed_dir=parsed_dir, statements_dir=statements_dir)
        charge, claim = read_healthcare(summary_dir)
        assert (charge["ref"], charge["match"], charge["not_ref"]) == ("", "review", "R00000000001")
        assert (claim["ref"], claim["match"]) == ("R00000000001", "no charge")


def test_a_mark_whose_charge_is_gone_is_warned_about(parsed_dir, summary_dir, tmp_path, caplog):
    dentist = buy(1, 6, 95.0, "SUNNY SMILES DENTAL", 8)
    statements_dir = summarize_with_a_near_miss(parsed_dir, tmp_path, dentist)
    mark_check_row(summary_dir, "checked")
    write_statement(parsed_dir, buy(1, 6, 96.0, "SUNNY SMILES DENTAL", 8))
    with caplog.at_level(logging.WARNING, logger="trex.summary"):
        summary.summarize(year=2026, parsed_dir=parsed_dir, statements_dir=statements_dir)
    assert "dropping the checked mark" in caplog.text
    [row] = read_healthcare(summary_dir)
    assert row["match"] == "check"


def test_a_wrong_mark_whose_claim_is_gone_is_dropped_once(
    parsed_dir, summary_dir, tmp_path, caplog
):
    dentist = buy(1, 6, 95.0, "SUNNY SMILES DENTAL", 8)
    statements_dir = summarize_with_a_near_miss(parsed_dir, tmp_path, dentist)
    mark_check_row(summary_dir, "wrong")
    summary.summarize(year=2026, parsed_dir=parsed_dir, statements_dir=statements_dir)
    (statements_dir / "IHPY10.xls").unlink()

    with caplog.at_level(logging.WARNING, logger="trex"):
        summary.summarize(year=2026, parsed_dir=parsed_dir, statements_dir=statements_dir)
    assert "dropping the wrong mark" in caplog.text
    assert "not_ref" not in read_healthcare(summary_dir)[0]

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="trex"):
        summary.summarize(year=2026, parsed_dir=parsed_dir, statements_dir=statements_dir)
    assert "dropping" not in caplog.text


def test_unclaimed_marks_survive_and_split_the_totals(parsed_dir, summary_dir):
    write_statement(
        parsed_dir,
        buy(1, 3, 2.5, "MYACTIVESG", 8),
        buy(1, 4, 40.0, "CLINIC B", 8),
        buy(1, 5, 9.0, "CLINIC C", 8),
    )
    summary.summarize(year=2026, parsed_dir=parsed_dir)
    path = healthcare_path(summary_dir)
    mark_row(summary_dir, "MYACTIVESG", "not claimable")
    mark_row(summary_dir, "CLINIC B", "to claim")

    for _ in range(2):
        summary.summarize(year=2026, parsed_dir=parsed_dir)
        rows = read_healthcare(summary_dir)
        assert [(row["remark"], row["match"]) for row in rows] == [
            ("CLINIC C", "review"),
            ("CLINIC B", "to claim"),
            ("MYACTIVESG", "not claimable"),
        ]
        assert "\nunclaimed,49.00\nnot claimable,2.50\n" in path.read_text()


def test_the_log_gives_the_unclaimed_total_the_listing_does(parsed_dir, summary_dir, caplog):
    (parsed_dir.parent / "categories").mkdir()
    (parsed_dir.parent / "categories" / "not_claimable.csv").write_text("regex\nactivesg\n")
    write_statement(
        parsed_dir,
        buy(1, 3, 2.5, "MYACTIVESG", 8),
        buy(1, 4, 40.0, "CLINIC B", 8),
        buy(1, 5, 7.0, "RAFFLES MEDICAL", 1),
    )
    with caplog.at_level(logging.INFO, logger="trex"):
        summary.summarize(year=2026, parsed_dir=parsed_dir)
    assert "\nunclaimed,40.00\n" in healthcare_path(summary_dir).read_text()
    assert "40.00 SGD of HEALTHCARE unclaimed" in caplog.text


def test_a_review_mark_is_not_read_back_as_a_verdict(parsed_dir, summary_dir):
    write_statement(parsed_dir, buy(1, 4, 40.0, "CLINIC B", 8))
    summary.summarize(year=2026, parsed_dir=parsed_dir)
    assert read_healthcare_marks(healthcare_path(summary_dir)) == ({}, {})


def test_not_claimable_csv_marks_matching_charges_unless_the_user_did(parsed_dir, summary_dir):
    (parsed_dir.parent / "categories").mkdir()
    (parsed_dir.parent / "categories" / "not_claimable.csv").write_text("regex\nactivesg\n")
    write_statement(
        parsed_dir,
        buy(1, 3, 2.5, "MYACTIVESG", 8),
        buy(1, 4, 2.5, "ACTIVESG GYM", 8),
        buy(1, 5, 9.0, "CLINIC C", 8),
    )
    summary.summarize(year=2026, parsed_dir=parsed_dir)
    rows = read_healthcare(summary_dir)
    assert [(row["remark"], row["match"]) for row in rows] == [
        ("CLINIC C", "review"),
        ("ACTIVESG GYM", "not claimable"),
        ("MYACTIVESG", "not claimable"),
    ]
    assert "\nunclaimed,9.00\nnot claimable,5.00\n" in healthcare_path(summary_dir).read_text()

    mark_row(summary_dir, "ACTIVESG GYM", "to claim")
    summary.summarize(year=2026, parsed_dir=parsed_dir)
    rows = read_healthcare(summary_dir)
    assert ("ACTIVESG GYM", "to claim") in [(row["remark"], row["match"]) for row in rows]


# --- listing order --------------------------------------------------------


def test_unclaimed_charges_are_grouped_by_remark_then_date(tmp_path):
    path = tmp_path / "healthcare2026.csv"
    charges = [
        buy(1, 3, 2.5, "MYACTIVESG"),
        buy(1, 4, 9.0, "CLINIC B"),
        buy(1, 5, 2.5, "myactivesg"),
    ]
    write_healthcare_csv([(charge, None) for charge in charges], [], path)
    rows = read_healthcare_csv(path)
    assert [(row["remark"], row["date"]) for row in rows] == [
        ("CLINIC B", "2026-01-04"),
        ("MYACTIVESG", "2026-01-03"),
        ("myactivesg", "2026-01-05"),
    ]


def test_matched_charges_are_grouped_by_plan_patient_remark_then_date(tmp_path):
    def paid(day, plan, patient, kind, remark="CLINIC"):
        when = datetime(2026, 1, day)
        claim = Claim(f"R{day}", plan, patient, when, when, "X", "", 10.0, 10.0, "panel")
        return buy(1, day, 10.0, remark), ClaimMatch(claim, kind)

    path = tmp_path / "healthcare2026.csv"
    write_healthcare_csv(
        [
            paid(1, "Y", "A", "exact"),
            paid(2, "G", "B", "exact"),
            paid(3, "G", "A", "copay"),
            paid(4, "G", "A", "exact"),
            paid(7, "G", "A", "exact", "ATRIA DENTAL"),
            paid(5, "Y", "A", "check"),
            paid(6, "G", "A", "check"),
        ],
        [],
        path,
    )
    assert [row["ref"] for row in read_healthcare_csv(path)] == [
        "R6",
        "R5",
        "R7",
        "R3",
        "R4",
        "R2",
        "R1",
    ]


def test_claims_without_a_charge_are_listed_by_plan_patient_and_date(tmp_path):
    def visit(reference, plan, patient, day):
        when = datetime(2026, 1, day)
        return Claim(reference, plan, patient, when, when, "X", "", 10.0, 10.0, "panel")

    path = tmp_path / "healthcare2026.csv"
    write_healthcare_csv(
        [(buy(1, 1, 5.0, "CLINIC", 8), None)],
        [visit("R4", "Y", "A", 1), visit("R3", "G", "B", 2), visit("R2", "G", "A", 9)]
        + [visit("R1", "G", "A", 3)],
        path,
    )
    rows = read_healthcare_csv(path)
    assert [row["ref"] for row in rows] == ["", "R1", "R2", "R3", "R4"]
