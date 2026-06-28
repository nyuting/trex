import os
import re

import tabula

IN_DIR = 'statements'
TAB_DIR = 'statements_tabula'

# Naming:
#   txn = transaction
#   df  = pandas DataFrame (one per page, returned by tabula.read_pdf)

def fmt(cells):
    # match the prior tabula-GUI output: only the very first cell, when empty,
    # is rendered as "" — all other empty cells are bare commas
    out = []
    for i, c in enumerate(cells):
        if c == "":
            out.append('""' if i == 0 else "")
        elif "," in c or '"' in c:
            out.append('"' + c.replace('"', '""') + '"')
        else:
            out.append(c)
    return ",".join(out)

def iter_rows(dfs):
    """yield (non_empty_cells, desc, amount) for each non-empty row across all dfs"""
    for df in dfs:
        for row in df.itertuples(index=False):
            non_empty = [str(c).strip() for c in row if c == c and str(c).strip()]
            if not non_empty:
                continue
            desc = non_empty[0]
            amount = non_empty[-1] if len(non_empty) > 1 else ""
            yield non_empty, desc, amount


def find_in_cells(non_empty, regex):
    for c in non_empty:
        m = regex.match(c)
        if m:
            return m
    return None


# ----- Chase -----

chase_period_re = re.compile(r"^(\d{2}/\d{2}/\d{2}\s+-\s+\d{2}/\d{2}/\d{2})$")
chase_currency_re = re.compile(r"^\d{2}/\d{2}[A-Z][A-Z\s]*$")
chase_exchg_re = re.compile(r"^[\d,.]+\s+X\s+[\d.]+\s+\(EXCHG RATE\)$")
chase_txn_re = re.compile(r"^(\d{2}/\d{2})(.+)$")


def process_chase(dfs):
    lines = []
    seen_period = False
    in_activity = False

    for non_empty, desc, amount in iter_rows(dfs):
        if amount == desc:
            amount = ""

        if not seen_period:
            m = find_in_cells(non_empty, chase_period_re)
            if m:
                lines.append(fmt([m.group(1)]))
                seen_period = True
                continue

        if desc.startswith("Transaction Merchant"):
            in_activity = True
            continue
        if "Totals Year-to-Date" in desc or desc == "INTEREST CHARGES":
            in_activity = False
            continue
        if not in_activity:
            continue

        if chase_currency_re.match(desc) and not amount:
            lines.append(fmt(["", desc, ""]))
            continue

        if chase_exchg_re.match(desc) and not amount:
            lines.append(fmt(["", desc, ""]))
            continue

        # display payments/credits (negative amounts) in terminal but skip from output
        if amount.startswith("-"):
            print(f"  [skip credit] {desc} {amount}")
            continue

        m = chase_txn_re.match(desc)
        if m and amount:
            lines.append(fmt([m.group(1), m.group(2), amount]))
    return lines


# ----- PayLah (PLG / PLY) -----

paylah_stmt_date_re = re.compile(r"^(\d{2}\s\w{3}\s\d{4})\b")
paylah_ref_re = re.compile(r"^REF NO:\.\s.+$")
paylah_amount_re = re.compile(r"^(.+?)\s+(CR|DB)$")
paylah_date_re = re.compile(r"^(\d{2}\s\w{3})\s+(.+)$")


def process_paylah(dfs, include_stmt_date):
    lines = []
    seen_stmt_date = False

    for non_empty, desc, amount in iter_rows(dfs):
        if include_stmt_date and not seen_stmt_date:
            m = find_in_cells(non_empty, paylah_stmt_date_re)
            if m:
                lines.append(fmt([m.group(1)]))
                seen_stmt_date = True
                continue

        m = find_in_cells(non_empty, paylah_ref_re)
        if m:
            lines.append(fmt(["", m.group(0), "", ""]))
            continue

        m_amt = paylah_amount_re.match(non_empty[-1])
        if not m_amt:
            continue
        txn_amount, indicator = m_amt.groups()
        rest = " ".join(non_empty[:-1])
        m_date = paylah_date_re.match(rest)
        if not m_date:
            continue
        date, txn_desc = m_date.groups()
        lines.append(fmt([date, txn_desc, txn_amount, indicator]))
    return lines


# ----- UOB -----

uob_stmt_date_re = re.compile(r"^Statement Date\s+(.+)$")
uob_section_re = re.compile(r"^[A-Z][A-Z\s']+ (AMEX|VISA|CARD)$")
uob_total_re = re.compile(r"^TOTAL BALANCE FOR .+$")
uob_ref_re = re.compile(r"^Ref No\. : .+$")
uob_txn_re = re.compile(r"^(\d{2}\s\w{3})\s(\d{2}\s\w{3})\s(.+)$")


def process_uob(dfs):
    lines = []
    seen_stmt_date = False
    last_section = None

    for non_empty, desc, amount in iter_rows(dfs):
        if not seen_stmt_date:
            m = find_in_cells(non_empty, uob_stmt_date_re)
            if m:
                lines.append(fmt(["Statement Date", m.group(1)]))
                seen_stmt_date = True
                continue

        if uob_total_re.match(desc):
            lines.append(fmt([desc, amount]))
            last_section = None
            continue

        # section header (deduped across pages)
        if uob_section_re.match(desc) and (not amount or amount == desc):
            if desc != last_section:
                lines.append(fmt([desc]))
                last_section = desc
            continue

        if desc == "PREVIOUS BALANCE":
            lines.append(fmt(["", "", "PREVIOUS BALANCE", amount]))
            continue

        if uob_ref_re.match(desc):
            lines.append(fmt(["", "", desc, ""]))
            continue

        m = uob_txn_re.match(desc)
        if m:
            lines.append(fmt([m.group(1), m.group(2), m.group(3), amount]))
    return lines


# ----- dispatch -----

PROCESSORS = {
    "Chase": process_chase,
    "PLG": lambda dfs: process_paylah(dfs, include_stmt_date=True),
    "PLY": lambda dfs: process_paylah(dfs, include_stmt_date=True),
    "UOB": process_uob,
}

name_re = re.compile(r"^([A-Za-z]+\d+)")


def detect_type(path):
    name = os.path.basename(path)
    for prefix in PROCESSORS:
        if name.startswith(prefix):
            return prefix
    raise ValueError(f"unknown statement type for {name}")


def short_name(path):
    name = os.path.basename(path)
    m = name_re.match(name)
    if not m:
        raise ValueError(f"can't derive short name from {name}")
    return m.group(1)

def convert(src, dst):
    dfs = tabula.read_pdf(src, pages="all", stream=True, guess=False)
    lines = PROCESSORS[detect_type(src)](dfs)
    with open(dst, "w", newline="") as f:
        f.write("\r\n".join(lines) + "\r\n")
    print(f"wrote {dst}")


if __name__ == "__main__":
    import argparse
    import glob

    parser = argparse.ArgumentParser(description="Convert statement PDFs to tabula CSVs")
    parser.add_argument("--src", help=f"single PDF to convert (default: process all PDFs in {IN_DIR}/)")
    parser.add_argument("--dst", help=f"output CSV path (used with --src; defaults to {TAB_DIR}/tabula-<name>.csv)")
    parser.add_argument("--categorize", action="store_true", help="also run parseStatement to produce categorized CSVs")
    args = parser.parse_args()

    if args.src:
        pairs = [(args.src, args.dst or f"{TAB_DIR}/tabula-{short_name(args.src)}.csv")]
    else:
        pairs = [(s, f"{TAB_DIR}/tabula-{short_name(s)}.csv") for s in sorted(glob.glob(f"{IN_DIR}/*.pdf"))]

    for src, dst in pairs:
        convert(src, dst)

    if args.categorize:
        from stmt import parseStatement
        for src, _ in pairs:
            parseStatement(short_name(src))
