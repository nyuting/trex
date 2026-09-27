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
    "filename, issuer",
    [
        ("Chase05.pdf", "Chase"),
        ("CHASE01.pdf", "Chase"),
        ("PLG05.pdf", "Paylah"),
        ("PLY0405.pdf", "Paylah"),
        ("UOB05.pdf", "UOB"),
        ("/a/b/uob05.pdf", "UOB"),
    ],
)
def test_issuer_is_detected_from_the_filename(filename, issuer):
    assert extract.detect_issuer(filename) == issuer


def test_unknown_issuer_is_rejected():
    with pytest.raises(ValueError, match="unknown issuer"):
        extract.detect_issuer("Barclays01.pdf")


@pytest.mark.parametrize(
    "filename, expected",
    [("UOB05.pdf", "UOB05"), ("PLY0405.pdf", "PLY0405"), ("/a/b/Chase01.pdf", "Chase01")],
)
def test_short_name_keeps_letters_and_digits(filename, expected):
    assert extract.derive_short_name(filename) == expected


def test_short_name_rejects_a_nameless_file():
    with pytest.raises(ValueError, match="can't derive short name"):
        extract.derive_short_name("scan.pdf")


def test_extract_all_skips_unrecognised_filenames(tmp_path, monkeypatch, caplog):
    for name in ("UOB05.pdf", "IHPG_1.pdf", "Barclays01.pdf"):
        (tmp_path / name).touch()
    extracted = []
    monkeypatch.setattr(extract, "extract_pdf_to_csv", lambda pdf: extracted.append(pdf) or pdf)

    assert extract.extract_all_pdfs(tmp_path) == [tmp_path / "UOB05.pdf"]
    assert extracted == [tmp_path / "UOB05.pdf"]
    assert "skipping IHPG_1.pdf" in caplog.text
    assert "skipping Barclays01.pdf" in caplog.text


# --- shared row handling ------------------------------------------------


def test_amounts_are_recognized_with_and_without_indicators():
    assert pdf.AMOUNT_RE.match("1,234.56")
    assert pdf.AMOUNT_RE.match("-12.00")
    assert pdf.AMOUNT_RE.match("12.00 CR")
    assert pdf.AMOUNT_RE.match(".81")
    assert not pdf.AMOUNT_RE.match("12.0")
    assert not pdf.AMOUNT_RE.match("ACME 12")


def test_blank_rows_are_skipped():
    assert list(pdf.iter_row_cells(rows(("", ""), ("  ", ""), ("ACME", "5.00")))) == [
        (["ACME", "5.00"], "ACME", "5.00")
    ]


def test_a_row_without_an_amount_reports_none():
    ((cells, description, amount),) = pdf.iter_row_cells(rows(("SECTION HEADER", "")))
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
    assert match.group(1) == "31 JAN 2026"


# --- word geometry (points) ---------------------------------------------


def word(text: str, x0: float, top: float = 100.0, width: float | None = None) -> dict:
    """A pdfplumber word dict; 5 points per character unless `width` is given."""
    return {"text": text, "x0": x0, "x1": x0 + (width or 5.0 * len(text)), "top": top}


class FakePage:
    """Stands in for a pdfplumber Page: hands back words in the given order."""

    def __init__(self, *words: dict):
        self.words = list(words)

    def extract_words(self, **_):
        return self.words


def test_words_within_the_row_tolerance_share_a_row():
    page = FakePage(word("ACME", 10, top=100.0), word("12.00", 300, top=100.0 + pdf.ROW_TOLERANCE))
    assert [[w["text"] for w in row] for row in pdf.group_words_into_rows(page)] == [
        ["ACME", "12.00"]
    ]


def test_words_beyond_the_row_tolerance_start_a_new_row():
    page = FakePage(
        word("ACME", 10, top=100.0), word("GREENMART", 10, top=100.0 + pdf.ROW_TOLERANCE + 0.5)
    )
    assert len(pdf.group_words_into_rows(page)) == 2


def test_rows_come_back_top_to_bottom_and_left_to_right():
    page = FakePage(
        word("5.00", 300, top=120.0),
        word("GREENMART", 10, top=120.0),
        word("12.00", 300, top=100.0),
        word("ACME", 10, top=100.0),
    )
    rows_ = pdf.group_words_into_rows(page)
    assert [[w["text"] for w in row] for row in rows_] == [["ACME", "12.00"], ["GREENMART", "5.00"]]


def test_a_word_gap_keeps_a_cell_and_a_wide_gap_splits_it():
    acme = word("ACME", 10)  # x1 = 30
    coffee = word("COFFEE", 30 + pdf.CELL_GAP)  # exactly CELL_GAP: same cell
    amount = word("18.40", coffee["x1"] + pdf.CELL_GAP + 0.5)
    assert pdf.split_row_into_cells([acme, coffee, amount]) == ["ACME COFFEE", "18.40"]


def test_only_a_trailing_amount_is_peeled_off_a_row():
    assert pdf._to_page_row([word("ACME", 10), word("18.40", 300)]) == pdf.PageRow("ACME", "18.40")
    assert pdf._to_page_row([word("CR", 10), word("18.40 CR", 300)]).amount == "18.40 CR"
    # A row ending in a date, like UOB's UNI$ line, keeps everything as text.
    row = pdf._to_page_row([word("UNI$ - 1.00", 10), word("31/12/2026", 300)])
    assert row == pdf.PageRow("UNI$ - 1.00 31/12/2026", "")


def test_a_lone_amount_is_text_not_an_amount():
    assert pdf._to_page_row([word("18.40", 300)]) == pdf.PageRow("18.40", "")


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


def test_chase_keeps_credits():
    lines = chase.extract_chase_lines(
        rows(
            ("Transaction Merchant Name", ""),
            ("01/05 PAYMENT THANK YOU", "-500.00"),
            ("01/06 ACME", "10.00"),
        )
    )
    assert lines == ["01/05,PAYMENT THANK YOU,-500.00", "01/06,ACME,10.00"]


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


def test_chase_account_summary_follows_the_period_though_it_precedes_it_on_the_page():
    lines = chase.extract_chase_lines(
        rows(
            ("New Balance", ""),
            ("Previous Balance $1,500.00", ""),
            ("Payment, Credits -$1,500.00", ""),
            ("Purchases +$18.40", ""),
            ("New Balance $18.40", ""),
            ("Opening/Closing Date 12/15/25 - 01/14/26", ""),
            ("Messages for details. New Balance: $18.40", ""),
            ("Transaction Merchant Name", ""),
            ("01/03 ACME COFFEE", "18.40"),
        )
    )
    assert lines == [
        "12/15/25 - 01/14/26",
        'Previous Balance,"1,500.00"',
        '"Payment, Credits","-1,500.00"',
        "Purchases,18.40",
        "New Balance,18.40",
        "01/03,ACME COFFEE,18.40",
    ]


# --- PayLah -------------------------------------------------------------


def test_paylah_emits_statement_date_then_transactions():
    lines = paylah.extract_paylah_lines(
        rows(("31 Jan 2026 Account Summary", ""), ("05 Jan ACME COFFEE", "18.40 DB"))
    )
    assert lines == ["31 Jan 2026", "05 Jan,ACME COFFEE,18.40,DB"]


def test_paylah_balances_follow_the_statement_date():
    lines = paylah.extract_paylah_lines(
        rows(
            ("31 Jan 2026 Account Summary", ""),
            ("PREVIOUS BALANCE", "1,000.00 CR"),
            ("05 Jan ACME COFFEE", "18.40 DB"),
            ("Total :", "981.60 CR"),
        )
    )
    assert lines == [
        "31 Jan 2026",
        'PREVIOUS BALANCE,"1,000.00 CR"',
        "CLOSING BALANCE,981.60 CR",
        "05 Jan,ACME COFFEE,18.40,DB",
    ]


def test_paylah_reads_the_newer_carried_forward_total_and_ignores_the_summary_total():
    lines = paylah.extract_paylah_lines(
        rows(
            ("31 Jul 2026 Account Summary", ""),
            ("Total: 50.00 CR", ""),
            ("PREVIOUS BALANCE", "68.40 CR"),
            ("05 Jul ACME COFFEE", "18.40 DB"),
            ("Total Balance Carried Forward:", "50.00 CR"),
        )
    )
    assert lines[1:3] == ["PREVIOUS BALANCE,68.40 CR", "CLOSING BALANCE,50.00 CR"]


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


UNI_ROW = "UNI$ - 12,000.00 1,250.00 0.00 10.00- 13,240.00 0.00 31/12/2027"


def test_uob_emits_the_uni_row_once_without_the_expiring_columns():
    lines = uob.extract_uob_lines(
        rows(
            ("TOTAL BALANCE FOR UOB ONE CARD", "1.00"),
            ("Rewards Summary", ""),
            ("Card Number Previous Earned Used Adjustment Current Expiring Expiry", ""),
            (UNI_ROW, ""),
            (UNI_ROW, ""),
        )
    )
    assert lines == [
        "TOTAL BALANCE FOR UOB ONE CARD,1.00",
        'UNI$,"12,000.00","1,250.00",0.00,10.00-,"13,240.00"',
    ]


def test_uob_finds_the_uni_row_when_its_last_cell_lands_in_the_amount_column():
    text, _, last_cell = UNI_ROW.rpartition(" ")
    lines = uob.extract_uob_lines(rows((text, last_cell)))
    assert lines == ['UNI$,"12,000.00","1,250.00",0.00,10.00-,"13,240.00"']


def test_uob_ignores_the_rewards_summary_list_and_notes():
    lines = uob.extract_uob_lines(
        rows(
            ("Rewards Summary", ""),
            ("Lady's Savings Account Bonus 2X UNI$1234", ""),
            ("03 AUG 2026 ACME RETAIL SG UNI$-1234", ""),
            ("05 AUG 2026 ADD UNI$ - MEMBERSHIP FEE REV UNI$12345", ""),
            ("Denomination of UNI$ are expressed in points, Cash Rebate is in SGD.", ""),
        )
    )
    assert lines == []
