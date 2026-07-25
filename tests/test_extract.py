"""PDF row reconstruction and the per-issuer line extractors.

The extractors are driven with synthetic PageRow lists rather than PDFs, so the
suite needs no statement files (real ones are private and never committed).
"""

from __future__ import annotations

import pytest

from trex import extract
from trex.extract import chase, paylah, pdf, uob


def rows(*pairs) -> list[list[pdf.PageRow]]:
    """Build a one-page document from (text, amount) pairs."""
    return [[pdf.PageRow(text, amount) for text, amount in pairs]]


# --- dispatch -----------------------------------------------------------


@pytest.mark.parametrize(
    "filename, prefix",
    [("Chase05.pdf", "Chase"), ("PLG05.pdf", "PLG"), ("PLY0405.pdf", "PLY"), ("UOB05.pdf", "UOB")],
)
def test_issuer_is_detected_from_the_filename(filename, prefix):
    assert extract.detect_issuer_prefix(filename) == prefix


def test_unknown_issuer_is_rejected():
    with pytest.raises(ValueError, match="unknown statement type"):
        extract.detect_issuer_prefix("Barclays01.pdf")


@pytest.mark.parametrize(
    "filename, expected",
    [("UOB05.pdf", "UOB05"), ("PLY0405.pdf", "PLY0405"), ("/a/b/Chase01.pdf", "Chase01")],
)
def test_short_name_keeps_letters_and_digits(filename, expected):
    assert extract.short_name(filename) == expected


def test_short_name_rejects_a_nameless_file():
    with pytest.raises(ValueError, match="can't derive short name"):
        extract.short_name("scan.pdf")


# --- shared row handling ------------------------------------------------


def test_amounts_are_recognized_with_and_without_indicators():
    assert pdf.AMOUNT_RE.match("1,234.56")
    assert pdf.AMOUNT_RE.match("-12.00")
    assert pdf.AMOUNT_RE.match("12.00 CR")
    assert not pdf.AMOUNT_RE.match("12.0")
    assert not pdf.AMOUNT_RE.match("ACME 12")


def test_blank_rows_are_skipped():
    assert list(pdf.iter_rows(rows(("", ""), ("  ", ""), ("ACME", "5.00")))) == [
        (["ACME", "5.00"], "ACME", "5.00")
    ]


def test_a_row_without_an_amount_reports_none():
    ((cells, description, amount),) = pdf.iter_rows(rows(("SECTION HEADER", "")))
    assert (cells, description, amount) == (["SECTION HEADER"], "SECTION HEADER", "")


@pytest.mark.parametrize(
    "cells, expected",
    [
        (["a", "b"], "a,b"),
        (["", "b"], '"",b'),
        (["a", ""], "a,"),
        (["a,b", "c"], '"a,b",c'),
        (['say "hi"', "c"], '"say ""hi""",c'),
    ],
)
def test_csv_lines_quote_only_where_needed(cells, expected):
    assert pdf.format_csv_line(cells) == expected


def test_find_in_cells_searches_every_cell_not_just_the_first():
    match = pdf.find_in_cells(["noise", "Statement Date  31 JAN 2026"], uob.STATEMENT_DATE_RE)
    assert match is None or match.group(1) == "31 JAN 2026"


# --- Chase --------------------------------------------------------------


def test_chase_emits_period_then_activity_transactions():
    lines = chase.extract_chase_lines(
        rows(
            ("Opening/Closing Date 12/15/25 - 01/14/26", ""),
            ("Transaction Merchant Name", ""),
            ("01/03 ACME COFFEE", "18.40"),
            ("01/07 GREENMART", "96.30"),
            ("Totals Year-to-Date", ""),
            ("01/09 NOT ACTIVITY", "5.00"),
        )
    )
    assert lines == ["12/15/25 - 01/14/26", "01/03,ACME COFFEE,18.40", "01/07,GREENMART,96.30"]


def test_chase_skips_credits():
    lines = chase.extract_chase_lines(
        rows(
            ("Transaction Merchant Name", ""),
            ("01/05 PAYMENT THANK YOU", "-500.00"),
            ("01/06 ACME", "10.00"),
        )
    )
    assert lines == ["01/06,ACME,10.00"]


def test_chase_keeps_foreign_currency_and_exchange_rate_lines():
    lines = chase.extract_chase_lines(
        rows(
            ("Transaction Merchant Name", ""),
            ("01/03 JAPANESE YEN", ""),
            ("1,500.00 X 0.0090 (EXCHG RATE)", ""),
            ("01/03 TOKYO HOTEL", "13.50"),
        )
    )
    assert lines == [
        '"",01/03JAPANESE YEN,',
        '"","1,500.00 X 0.0090 (EXCHG RATE)",',
        "01/03,TOKYO HOTEL,13.50",
    ]


def test_chase_ignores_rows_before_the_activity_section():
    assert chase.extract_chase_lines(rows(("01/03 ACME", "10.00"))) == []


# --- PayLah -------------------------------------------------------------


def test_paylah_emits_statement_date_then_transactions():
    lines = paylah.extract_paylah_lines(
        rows(("31 Jan 2026 Account Summary", ""), ("05 Jan ACME COFFEE", "18.40 DB"))
    )
    assert lines == ["31 Jan 2026", "05 Jan,ACME COFFEE,18.40,DB"]


def test_paylah_statement_date_can_be_suppressed():
    lines = paylah.extract_paylah_lines(
        rows(("31 Jan 2026 Account Summary", ""), ("05 Jan ACME", "1.00 DB")),
        include_statement_date=False,
    )
    assert lines == ["05 Jan,ACME,1.00,DB"]


def test_paylah_keeps_reference_lines_as_their_own_row():
    lines = paylah.extract_paylah_lines(
        rows(("05 Jan ACME", "18.40 DB"), ("REF NO:. 12345", "")),
        include_statement_date=False,
    )
    assert lines == ["05 Jan,ACME,18.40,DB", '"",REF NO:. 12345,,']


def test_paylah_ignores_rows_with_no_credit_debit_indicator():
    assert paylah.extract_paylah_lines(rows(("05 Jan ACME", "18.40")), False) == []


# --- UOB ----------------------------------------------------------------


def test_uob_emits_statement_date_sections_and_transactions():
    lines = uob.extract_uob_lines(
        rows(
            ("Statement Date  31 JAN 2026", ""),
            ("UOB ONE CARD", ""),
            ("03 JAN 04 JAN ACME COFFEE", "18.40"),
            ("TOTAL BALANCE FOR UOB ONE CARD", "18.40"),
        )
    )
    assert lines == [
        "Statement Date,31 JAN 2026",
        "UOB ONE CARD",
        "03 JAN,04 JAN,ACME COFFEE,18.40",
        "TOTAL BALANCE FOR UOB ONE CARD,18.40",
    ]


def test_uob_deduplicates_a_section_continued_on_the_next_page():
    lines = uob.extract_uob_lines(
        rows(
            ("UOB ONE CARD", ""),
            ("03 JAN 04 JAN ACME", "1.00"),
            ("UOB ONE CARD", ""),
            ("05 JAN 06 JAN GREENMART", "2.00"),
        )
    )
    assert lines.count("UOB ONE CARD") == 1


def test_uob_section_total_reopens_the_next_section_header():
    lines = uob.extract_uob_lines(
        rows(
            ("UOB ONE CARD", ""),
            ("TOTAL BALANCE FOR UOB ONE CARD", "1.00"),
            ("UOB ONE CARD", ""),
        )
    )
    assert lines.count("UOB ONE CARD") == 2


def test_uob_keeps_previous_balance_and_reference_rows():
    lines = uob.extract_uob_lines(rows(("PREVIOUS BALANCE", "500.00"), ("Ref No. : 987", "")))
    assert lines == ['"",,PREVIOUS BALANCE,500.00', '"",,Ref No. : 987,']
