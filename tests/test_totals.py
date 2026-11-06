"""Statement totals: guarding each statement's recorded spending total."""

from __future__ import annotations

import csv

from trex.summary import rows, totals


def totals_path(data_dir):
    return data_dir / "summary" / "statement_totals2026.csv"


def read_rows(data_dir) -> dict[str, dict[str, str]]:
    with open(totals_path(data_dir)) as handle:
        return {row["month"]: row for row in csv.DictReader(handle)}


def reprice(data_dir, old: str, new: str) -> None:
    path = data_dir / "parsed" / "UOB01.csv"
    path.write_text(path.read_text().replace(old, new))


def test_a_first_run_records_every_card(data_dir):
    assert totals.update_statement_totals(2026) == []
    january = read_rows(data_dir)["01"]
    assert january["UOB-ONE"] == "230.59"
    assert january["UOB-VISA"] == "12.00"
    assert january["CHASE (USD)"] == ""
    assert january["Total (SGD)"] == "242.59"
    assert read_rows(data_dir)["02"]["Total (SGD)"] == ""


def test_a_changed_total_is_reported_and_the_recorded_one_kept(data_dir):
    totals.update_statement_totals(2026)
    reprice(data_dir, "     12.00    3   UOB-VISA", "     15.00    3   UOB-VISA")

    [change] = totals.update_statement_totals(2026)
    assert (change.month, change.card, change.recorded, change.current) == (
        1,
        "UOB-VISA",
        12.0,
        15.0,
    )
    assert read_rows(data_dir)["01"]["UOB-VISA"] == "12.00"
    # still reported on the next run, until accepted
    assert totals.update_statement_totals(2026) == [change]


def test_accepting_takes_the_new_total(data_dir):
    totals.update_statement_totals(2026)
    reprice(data_dir, "     12.00    3   UOB-VISA", "     15.00    3   UOB-VISA")

    assert len(totals.update_statement_totals(2026, accept=True)) == 1
    assert read_rows(data_dir)["01"]["UOB-VISA"] == "15.00"
    assert totals.update_statement_totals(2026) == []


def test_a_statement_that_disappears_is_reported(data_dir):
    totals.update_statement_totals(2026)
    (data_dir / "parsed" / "UOB01.csv").unlink()

    changes = totals.update_statement_totals(2026)
    assert {(c.card, c.current) for c in changes} == {("UOB-ONE", None), ("UOB-VISA", None)}
    assert read_rows(data_dir)["01"]["UOB-ONE"] == "230.59"


def test_chase_is_recorded_in_usd_with_an_sgd_column(data_dir, monkeypatch):
    monkeypatch.setattr(rows, "EXCHANGE_RATE_USD2SGD", 1.50)
    (data_dir / "parsed" / "Chase01.csv").write_text(
        "Statement Date, 2026-02-02\n\n"
        "date,cost,remark,category,card\n\n"
        "1 DINING\n"
        "   1  01/10    100.00    1      CHASE  ACME COFFEE\n"
    )
    totals.update_statement_totals(2026)
    january = read_rows(data_dir)["01"]
    assert january["CHASE (USD)"] == "100.00"
    assert january["CHASE (SGD)"] == "150.00"
    assert january["Total (SGD)"] == "392.59"  # 242.59 on UOB plus 150.00

    # a new exchange rate moves the SGD column but is not a changed total
    monkeypatch.setattr(rows, "EXCHANGE_RATE_USD2SGD", 1.60)
    assert totals.update_statement_totals(2026) == []
    assert read_rows(data_dir)["01"]["CHASE (SGD)"] == "160.00"
