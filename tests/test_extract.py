"""PDF row reconstruction and the per-issuer line extractors.

The extractors are driven with synthetic PageRow lists rather than PDFs, so the
suite needs no statement files (real ones are private and never committed).
"""

from __future__ import annotations

import logging

import pytest

from trex import extract
from trex.extract import chase, paylah, pdf, uob


def rows(*pairs) -> list[pdf.PageRow]:
    """Build a document's rows from (text, amount) pairs."""
    return [pdf.PageRow(text, amount) for text, amount in pairs]


CHASE_HEADER = ("Opening/Closing Date 12/15/25 - 01/14/26", "")
PAYLAH_HEADER = ("31 Jan 2026 Account Summary", "")
UOB_HEADER = ("Statement Date  31 JAN 2026", "")


def chase_body(body: list[pdf.PageRow]) -> list[str]:
    """Extract `body` under a Chase header row and return the lines after the header."""
    return chase.extract_chase_lines(rows(CHASE_HEADER) + body)[1:]


def paylah_body(body: list[pdf.PageRow]) -> list[str]:
    """Extract `body` under a PayLah header row and return the lines after the header."""
    return paylah.extract_paylah_lines(rows(PAYLAH_HEADER) + body)[1:]


def uob_body(body: list[pdf.PageRow]) -> list[str]:
    """Extract `body` under a UOB header row and return the lines after the header."""
    return uob.extract_uob_lines(rows(UOB_HEADER) + body)[1:]


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


def test_the_header_is_split_off_and_rows_above_it_are_kept():
    match, rest = pdf.split_off_header(
        rows(("Purchases +$1.00", ""), CHASE_HEADER, ("01/03 ACME", "1.00")), chase.PERIOD_RE
    )
    assert match.group(0) == "12/15/25 - 01/14/26"
    assert rest == rows(("Purchases +$1.00", ""), ("01/03 ACME", "1.00"))


@pytest.mark.parametrize(
    "extract_lines",
    [chase.extract_chase_lines, paylah.extract_paylah_lines, uob.extract_uob_lines],
)
def test_a_statement_without_its_header_is_rejected(extract_lines):
    with pytest.raises(ValueError, match="no statement header"):
        extract_lines(rows(("ACME", "1.00")))


def test_find_in_row_searches_the_text_then_the_amount():
    row = pdf.PageRow("Opening/Closing Date 12/15/25 - 01/14/26", "")
    assert pdf.find_in_row(row, chase.PERIOD_RE).group(0) == "12/15/25 - 01/14/26"
    row = pdf.PageRow("noise", "31 Jan 2026 Account Summary")
    assert pdf.find_in_row(row, paylah.STATEMENT_DATE_RE).group(1) == "31 Jan 2026"
    assert pdf.find_in_row(pdf.PageRow("noise", ""), paylah.STATEMENT_DATE_RE) is None


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


class FakePdf:
    """Stands in for pdfplumber.open(): pages that record being closed."""

    def __init__(self, *pages: FakePage):
        self.pages = list(pages)
        for page in self.pages:
            page.closed = False
            page.dedupe_chars = lambda page=page: page
            page.close = lambda page=page: setattr(page, "closed", True)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_rows_run_on_across_pages_and_every_page_is_closed(monkeypatch):
    first, second = FakePage(word("ACME", 10)), FakePage(word("GREENMART", 10))
    monkeypatch.setattr(pdf.pdfplumber, "open", lambda _path: FakePdf(first, second))
    assert pdf.read_pdf_rows("X01.pdf") == [pdf.PageRow("ACME", ""), pdf.PageRow("GREENMART", "")]
    assert first.closed and second.closed


def test_a_pdf_with_no_text_is_rejected(monkeypatch):
    monkeypatch.setattr(pdf.pdfplumber, "open", lambda _path: FakePdf(FakePage(), FakePage()))
    with pytest.raises(ValueError, match="X01.pdf has no text layer"):
        pdf.read_pdf_rows("X01.pdf")


def test_a_word_gap_keeps_a_cell_and_a_wide_gap_splits_it():
    acme = word("ACME", 10)  # x1 = 30
    coffee = word("COFFEE", 30 + pdf.CELL_GAP)  # exactly CELL_GAP: same cell
    amount = word("18.40", coffee["x1"] + pdf.CELL_GAP + 0.5)
    assert pdf._to_page_row([acme, coffee, amount]) == pdf.PageRow("ACME COFFEE", "18.40")


def test_only_the_last_wide_gap_can_split_off_the_amount():
    # "05 JAN" is its own column, but only the rightmost one is the amount.
    words = [word("05", 10), word("JAN", 25), word("ACME", 100), word("18.40", 300)]
    assert pdf._to_page_row(words) == pdf.PageRow("05 JAN ACME", "18.40")
    # Text after the last gap stays text, even with an amount before it.
    words = [word("ACME", 10), word("18.40", 100), word("SGD", 300)]
    assert pdf._to_page_row(words) == pdf.PageRow("ACME 18.40 SGD", "")


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
    lines = chase_body(
        rows(
            ("Transaction Merchant Name", ""),
            ("01/05 PAYMENT THANK YOU", "-500.00"),
            ("01/06 ACME", "10.00"),
        )
    )
    assert lines == ["01/05,PAYMENT THANK YOU,-500.00", "01/06,ACME,10.00"]


def test_chase_keeps_foreign_currency_and_exchange_rate_lines():
    lines = chase_body(
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
    assert chase_body(rows(("01/03 ACME", "10.00"))) == []


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


def test_paylah_keeps_reference_lines_as_their_own_row():
    lines = paylah_body(rows(("05 Jan ACME", "18.40 DB"), ("REF NO:. 12345", "")))
    assert lines == ["05 Jan,ACME,18.40,DB", '"",REF NO:. 12345,,']


def test_paylah_ignores_rows_with_no_credit_debit_indicator():
    assert paylah_body(rows(("05 Jan ACME", "18.40"))) == []


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
    lines = uob_body(
        rows(
            ("UOB ONE CARD", ""),
            ("03 JAN 04 JAN ACME", "1.00"),
            ("UOB ONE CARD", ""),
            ("05 JAN 06 JAN GREENMART", "2.00"),
        )
    )
    assert lines.count("UOB ONE CARD") == 1


def test_uob_section_total_reopens_the_next_section_header():
    lines = uob_body(
        rows(
            ("UOB ONE CARD", ""),
            ("TOTAL BALANCE FOR UOB ONE CARD", "1.00"),
            ("UOB ONE CARD", ""),
        )
    )
    assert lines.count("UOB ONE CARD") == 2


def test_uob_keeps_previous_balance_and_reference_rows():
    lines = uob_body(rows(("PREVIOUS BALANCE", "500.00"), ("Ref No. : 987", "")))
    assert lines == ['"",,PREVIOUS BALANCE,500.00', '"",,Ref No. : 987,']


UNI_ROW = "UNI$ - 12,000.00 1,250.00 0.00 10.00- 13,240.00 0.00 31/12/2027"


def test_uob_emits_the_uni_row_once_without_the_expiring_columns():
    lines = uob_body(
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
    lines = uob_body(rows((text, last_cell)))
    assert lines == ['UNI$,"12,000.00","1,250.00",0.00,10.00-,"13,240.00"']


def test_uob_reads_a_uni_row_with_no_expiring_points():
    text = UNI_ROW.rsplit(" ", 2)[0]
    assert uob_body(rows((text, ""))) == ['UNI$,"12,000.00","1,250.00",0.00,10.00-,"13,240.00"']


def test_uob_flags_a_uni_row_in_an_unexpected_layout(caplog):
    with caplog.at_level(logging.WARNING, logger="trex.extract"):
        lines = uob_body(rows(("UNI$ - 12,000.00 1,250.00 0.00 13,240.00", "")))
    assert lines == ["UNI$"]
    assert "UNI$ row not in the expected layout" in caplog.text
    assert "12,000.00" not in caplog.text


def test_uob_replaces_a_flagged_uni_row_with_a_readable_one_on_a_later_page():
    lines = uob_body(rows(("UNI$ - garbled", ""), (UNI_ROW, ""), ("UNI$ - garbled", "")))
    assert lines == ['UNI$,"12,000.00","1,250.00",0.00,10.00-,"13,240.00"']


def test_uob_ignores_the_rewards_summary_list_and_notes():
    lines = uob_body(
        rows(
            ("Rewards Summary", ""),
            ("Lady's Savings Account Bonus 2X UNI$1234", ""),
            ("03 AUG 2026 ACME RETAIL SG UNI$-1234", ""),
            ("05 AUG 2026 ADD UNI$ - MEMBERSHIP FEE REV UNI$12345", ""),
            ("Denomination of UNI$ are expressed in points, Cash Rebate is in SGD.", ""),
        )
    )
    assert lines == []
