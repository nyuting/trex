"""Matching claims to charges: exact amounts, co-payments, near misses, verdicts."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pytest

from trex.healthcare import matching
from trex.healthcare.claims import Claim
from trex.healthcare.matching import (
    MATCH_CHECK,
    MATCH_CHECKED,
    MATCH_COPAY,
    MATCH_EXACT,
    charge_key,
)
from trex.models import Transaction


def claim(reference="R1", visit=datetime(2026, 1, 5), amount=80.0, provider="X", status="panel"):
    return Claim(reference, "Y", "PATIENT A", visit, visit, provider, "", amount, amount, status)


def charge(day, cost, remark="CLINIC", month=1):
    return Transaction(datetime(2026, month, day), cost, remark, 8, "UOB-ONE")


def test_a_charge_matches_the_claim_for_its_exact_amount_after_the_visit():
    matches, unmatched = matching.match_claims([charge(8, 80.0)], [claim()])
    assert matches[0].kind == MATCH_EXACT
    assert unmatched == []


def test_a_charge_outside_the_window_is_unclaimed():
    matches, unmatched = matching.match_claims([charge(20, 80.0)], [claim()], window_days=7)
    assert matches == {}
    assert [c.reference for c in unmatched] == ["R1"]


def test_two_visits_of_the_same_amount_each_take_their_own_claim():
    visits = [claim("R1", datetime(2026, 1, 5)), claim("R2", datetime(2026, 1, 9))]
    matches, _ = matching.match_claims([charge(9, 80.0), charge(5, 80.0)], visits)
    assert (matches[0].claim.reference, matches[1].claim.reference) == ("R2", "R1")


def test_a_differing_amount_at_the_same_provider_is_a_near_miss():
    sgh = claim(amount=200.0, provider="SINGAPORE GENERAL HOSPITAL")
    matches, unmatched = matching.match_claims([charge(5, 120.0, "SGH MEDICAL CENTRE")], [sgh])
    assert matches[0].kind == MATCH_CHECK
    assert unmatched == []


def test_a_close_amount_is_a_near_miss_and_a_far_one_is_not():
    matches, _ = matching.match_claims([charge(5, 75.0), charge(6, 10.0)], [claim()])
    assert matches[0].kind == MATCH_CHECK
    assert 1 not in matches


def test_a_transferred_claim_matches_nothing():
    matches, unmatched = matching.match_claims([charge(5, 80.0)], [claim(status="transferred")])
    assert (matches, unmatched) == ({}, [])


def test_provider_keys_are_distinctive_words_and_initials():
    assert matching.provider_name_keys("NATIONAL HEART CENTRE SINGAPORE") == {
        "NATIONAL",
        "HEART",
        "NHCS",
    }


@pytest.mark.parametrize(
    ("remark", "medical"),
    [
        ("SGH PHARMACY", True),
        ("SATA COMMHEALTH", True),
        ("PARKWAY EAST HOSPITAL", True),
        ("CLINIQUE COUNTER", False),
    ],
)
def test_looks_medical(remark, medical):
    assert matching.looks_medical(remark, None) is medical


@pytest.mark.parametrize(
    "remark",
    [
        "OMISE*SWIM SCHOOL SGH",
        "RedMan at Parkway Singapore",
        "SF - SGH SINGAPORE",
        "CAFE@PARKWAY PARADE Singapore",
    ],
)
def test_a_not_medical_regex_overrides_the_keywords(tmp_path, remark):
    path = tmp_path / "not_medical.csv"
    patterns = [r"^OMISE\*", r"\bREDMAN\b", r"\bSF\s*-\s*SGH\b", r"\bPARKWAY\s+PARADE\b"]
    path.write_text("\n".join(["regex,note", *(f"{pattern}," for pattern in patterns)]) + "\n")
    assert matching.looks_medical(remark, None)
    assert not matching.looks_medical(remark, matching.read_remark_regex(path))


def test_a_not_medical_file_without_a_regex_column_names_the_file(tmp_path):
    path = tmp_path / "not_medical.csv"
    path.write_text("pattern\nREDMAN\n")
    with pytest.raises(ValueError, match=r"not_medical\.csv: no regex column"):
        matching.read_remark_regex(path)


def test_an_invalid_not_claimable_regex_names_the_file(tmp_path):
    path = tmp_path / "not_claimable.csv"
    path.write_text("regex\nactivesg\ngym(\n")
    with pytest.raises(ValueError, match=r"not_claimable\.csv: invalid regex 'gym\('"):
        matching.read_remark_regex(path)


def test_a_close_amount_at_another_named_provider_is_not_a_near_miss():
    # The remark names Family Point, which has a claim elsewhere in the year, so a
    # panel visit at a different clinic for a similar amount is not this charge.
    family_point = claim("R1", datetime(2026, 1, 16), 184.0, "FAMILY POINT CLINIC & SURGERY")
    herbal = claim("R2", datetime(2026, 5, 22), 177.57, "KIN TECK TONG PTE LTD")
    matches, _ = matching.match_claims(
        [charge(28, 190.0, "PAYNOW FAMILY POI... NA", month=5)], [family_point, herbal]
    )
    assert matches == {}


# --- co-payments and verdicts ------------------------------------------------


def herbal(reference="R1", payable=142.80, status="panel"):
    """Return a panel TCM visit billed 168.00, the plan paying `payable`."""
    visit = claim(reference, amount=168.0, provider="KIN TECK TONG (JEM)", status=status)
    return replace(visit, payable_amount=payable)


@pytest.mark.parametrize("cost", [25.20, 25.21, 26.80])
def test_a_panel_co_payment_matches_its_claim(cost):
    matches, unmatched = matching.match_claims(
        [charge(5, cost, "SMP**KIN TECK TONG - JEM SG")], [herbal()]
    )
    assert matches[0].kind == MATCH_COPAY
    assert unmatched == []


def test_a_co_payment_off_by_more_than_one_percent_is_only_a_check():
    matches, _ = matching.match_claims([charge(5, 30.0, "KIN TECK TONG")], [herbal()])
    assert matches[0].kind == MATCH_CHECK


def test_only_a_panel_claim_has_a_co_payment():
    matches, _ = matching.match_claims(
        [charge(5, 25.20, "KIN TECK TONG")], [herbal(status="approved")]
    )
    assert matches[0].kind == MATCH_CHECK


def test_a_co_payment_must_name_the_provider():
    matches, _ = matching.match_claims([charge(5, 25.20, "SOME CAFE")], [herbal()])
    assert matches == {}


def test_a_checked_pair_is_kept_whatever_the_amounts():
    paid = charge(9, 478.50, "DENTAL GROUP")
    matches, unmatched = matching.match_claims(
        [paid], [claim("R7")], verdicts={(charge_key(paid), "R7"): "checked"}
    )
    assert (matches[0].kind, matches[0].claim.reference) == (MATCH_CHECKED, "R7")
    assert unmatched == []


def test_a_wrong_pair_is_never_proposed_again():
    paid = charge(5, 75.0)
    verdicts = {(charge_key(paid), "R1"): "wrong"}
    matches, unmatched = matching.match_claims([paid], [claim("R1")], verdicts=verdicts)
    assert matches == {}
    assert [c.reference for c in unmatched] == ["R1"]


def test_a_wrong_pair_falls_through_to_the_next_candidate():
    paid = charge(6, 80.0)
    visits = [claim("R1", datetime(2026, 1, 5)), claim("R2", datetime(2026, 1, 2))]
    verdicts = {(charge_key(paid), "R1"): "wrong"}
    matches, _ = matching.match_claims([paid], visits, verdicts=verdicts)
    assert (matches[0].kind, matches[0].claim.reference) == (MATCH_EXACT, "R2")
