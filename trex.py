import csv
import glob
import os

from datetime import datetime

from stmt import (
    CATEGORIES,
    OUT_DIR,
    _adjustYear,
    _parseExpenseRow,
    _parseNumList,
    _readParsedStmtDate,
)

EXCHANGE_RATE_USD2SGD = 1.33


def _iterParsedRows(year=None, summary_name='summary.csv'):
    """Yield (month, cost, src, cats) for each expense row across all
    statements_parsed/*.csv, filtered to `year` (defaults to the current
    year). Each row's full year is inferred from its file's Statement
    Date via _adjustYear."""
    if year is None:
        year = datetime.now().year
    cats_set = set(CATEGORIES)
    paths = sorted(glob.glob(f'{OUT_DIR}/*.csv'))
    for path in paths:
        base = os.path.basename(path)
        if base.endswith('-update.csv') or base == summary_name:
            continue
        stmt_date = _readParsedStmtDate(path)
        with open(path) as f:
            for row in csv.reader(f):
                if len(row) != 1:
                    continue
                parsed = _parseExpenseRow(row[0])
                if parsed is None:
                    continue
                date, cost, cat_cell, src, _ = parsed
                try:
                    d = _adjustYear(datetime.strptime(date, '%m/%d'), stmt_date)
                except ValueError:
                    continue
                if d.year != year:
                    continue
                rcats = [c for c in _parseNumList(cat_cell) if c in cats_set]
                if not rcats:
                    continue
                yield d.month, cost, src, rcats


def summarize(out_name=None, year=None):
    """Consolidate statements_parsed/*.csv into a category x month table.

    Rows: categories 1..10 (+ Total). Cols: months 1..12 (+ Total).
    Multi-category rows split cost evenly across their categories.
    Filters to `year` (defaults to current year) and writes to
    statements_parsed/summary{year}.csv unless out_name is given.
    """
    if year is None:
        year = datetime.now().year
    if out_name is None:
        out_name = f'summary{year}.csv'
    cats = sorted(CATEGORIES)  # 1..10
    table = {c: [0.0] * 12 for c in cats}

    for month, cost, src, rcats in _iterParsedRows(year=year, summary_name=out_name):
        if src == 'CHASE':
            cost *= EXCHANGE_RATE_USD2SGD
        share = cost / len(rcats)
        for c in rcats:
            table[c][month - 1] += share

    months = [f'{m:02d}' for m in range(1, 13)]
    col_totals = [0.0] * 12
    grand = 0.0
    rows_out = []
    for c in cats:
        vals = table[c]
        rtot = sum(vals)
        grand += rtot
        for i, v in enumerate(vals):
            col_totals[i] += v
        rows_out.append((c, vals, rtot))

    label_w = max(len(f'{c} {CATEGORIES[c]}') for c in cats)
    label_w = max(label_w, len('Total'))
    cell_w = 9

    def fmt_cell(v):
        return f'{v:>{cell_w}.2f}' if v else f'{"":>{cell_w}}'

    print(f'{year}')
    header = f'{"":<{label_w}}  ' + '  '.join(f'{m:>{cell_w}}' for m in months) + f'  {"Total":>{cell_w}}'
    print(header)
    for c, vals, rtot in rows_out:
        label = f'{c} {CATEGORIES[c]}'
        print(f'{label:<{label_w}}  ' + '  '.join(fmt_cell(v) for v in vals) + f'  {rtot:>{cell_w}.2f}')
    print(f'{"Total":<{label_w}}  ' + '  '.join(f'{v:>{cell_w}.2f}' for v in col_totals) + f'  {grand:>{cell_w}.2f}')

    out_path = f'{OUT_DIR}/{out_name}'
    with open(out_path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['category'] + months + ['Total'])
        for c, vals, rtot in rows_out:
            w.writerow([f'{c} {CATEGORIES[c]}'] + [f'{v:.2f}' for v in vals] + [f'{rtot:.2f}'])
        w.writerow(['Total'] + [f'{v:.2f}' for v in col_totals] + [f'{grand:.2f}'])
    print(f'wrote {out_path}')
    return out_path


def checkSummaryTotal(summary_name='summary.csv', year=None, tol=0.01):
    """Re-sum statements_parsed/*.csv with the same scope as summarize()
    (CHASE->SGD, drop uncategorized; filter by year if given, else drop
    Dec) and compare to the grand-Total cell of summary.csv. Prints both
    totals and the diff; returns (recomputed, summary_total, diff)."""
    recomputed = 0.0
    for _month, cost, src, _rcats in _iterParsedRows(year=year, summary_name=summary_name):
        if src == 'CHASE':
            cost *= EXCHANGE_RATE_USD2SGD
        recomputed += cost

    summary_total = None
    summary_path = f'{OUT_DIR}/{summary_name}'
    with open(summary_path) as f:
        for row in csv.reader(f):
            if row and row[0] == 'Total':
                summary_total = float(row[-1])
                break
    if summary_total is None:
        raise ValueError(f'no Total row in {summary_path}')

    diff = recomputed - summary_total
    status = 'OK' if abs(diff) < tol else 'MISMATCH'
    print(f'  {status}: parsed-sum={recomputed:.2f}  summary={summary_total:.2f}  diff={diff:+.2f}')
    return recomputed, summary_total, diff
