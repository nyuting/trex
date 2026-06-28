import csv
import glob
import os
import re
from collections import Counter
from datetime import datetime

CATEGORIES = {
    1: 'DINING',
    2: 'GROCERIES',
    3: 'TRANSPORT',
    4: 'KIDS',
    5: 'GANSHUN',
    6: 'YUTING',
    7: 'HOME',
    8: 'HEALTHCARE',
    9: 'HOLIDAY',
    10: 'GIFTS',
}

SOURCES_NUMBERING = {
    1: 'CHASE',
    2: 'PLG',
    3: 'PLY',
    4: 'UOB-AMEX',
    5: 'UOB-VISA',
    6: 'UOB-ONE',
    7: 'UOB-LADY',
}
SOURCE_TO_ID = {v: k for k, v in SOURCES_NUMBERING.items()}

UOB_HEADERS = {
    'UOB ABSOLUTE CASHBACK AMEX': 'UOB-AMEX',
    'PREFERRED PLATINUM VISA':    'UOB-VISA',
    'UOB ONE CARD':               'UOB-ONE',
    "LADY'S SOLITAIRE CARD":      'UOB-LADY',
}

CAT_FILE = 'categories/cat.csv'
CAT_PERSONAL_FILE = 'categories/cat_personal.csv'
IN_DIR = 'statements'
TAB_DIR = 'statements_tabula'
OUT_DIR = 'statements_parsed'
PRIOR_DIR = 'statements_parsed_prior'


def _parseNumList(s):
    s = (s or '').strip()
    if not s:
        return []
    return [int(x) for x in s.split(',') if x.strip().isdigit()]

_CAT_HEADER = ['regex', 'category', 'source']
# Each rule carries an 'origin': 'shared' rules live in cat.csv (publishable);
# 'personal' rules (PayNow/transfer payees etc.) live in cat_personal.csv,
# which is git-ignored. Each file is flushed independently.
_CAT_RULES = []  # [{pattern, regex, cats: [int], srcs: set[int], origin: str}]
_CAT_FILES = {'shared': CAT_FILE, 'personal': CAT_PERSONAL_FILE}
_CAT_DIRTY = {'shared': False, 'personal': False}

_PERSONAL_RE = re.compile(r'^(PAYNOW|SEND MONEY TO|SEND EGIFT)\b', re.IGNORECASE)
_BARE_MOBILE_RE = re.compile(r'^(?:\.\*)?[89]\d{7}')


def _classifyOrigin(remark):
    """Decide which file a freshly auto-added rule belongs to. Transfer
    payees and bare mobile numbers are personal; everything else is shared."""
    return 'personal' if (_PERSONAL_RE.match(remark) or
                          _BARE_MOBILE_RE.match(remark)) else 'shared'


def _loadCatRules():
    """Read categories/cat.csv and categories/cat_personal.csv
    (regex,category,source). Both cells may be multi-value comma lists. The
    source cell records where a txn was seen (informational), not a match
    filter — every rule is tried for every source. cat_personal.csv is
    optional (absent in a fresh clone)."""
    global _CAT_HEADER, _CAT_RULES, _CAT_DIRTY
    _CAT_RULES = []
    _CAT_DIRTY = {'shared': False, 'personal': False}
    for origin, path in _CAT_FILES.items():
        if not os.path.exists(path):
            continue
        with open(path) as f:
            reader = csv.reader(f)
            _CAT_HEADER = next(reader)
            for row in reader:
                if len(row) != 3:
                    continue
                pattern, cat_str, src_str = row
                cats = _parseNumList(cat_str)
                if not cats:
                    continue
                try:
                    rgx = re.compile(pattern)
                except re.error:
                    continue
                _CAT_RULES.append({
                    'pattern': pattern,
                    'regex': rgx,
                    'cats': cats,
                    'srcs': set(_parseNumList(src_str)),
                    'origin': origin,
                })


_loadCatRules()


def _markCatDirty(origin):
    _CAT_DIRTY[origin] = True


def flushCatCsv():
    """Re-sort each cat file by (first category, regex case-insensitive) and
    write it back if any of its rules were mutated since load."""
    for origin, path in _CAT_FILES.items():
        if not _CAT_DIRTY[origin]:
            continue
        rules = sorted((r for r in _CAT_RULES if r['origin'] == origin),
                       key=lambda r: (r['cats'][0], r['pattern'].lower()))
        with open(path, 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(_CAT_HEADER)
            for r in rules:
                cat_cell = ','.join(str(c) for c in r['cats'])
                src_cell = ','.join(str(s) for s in sorted(r['srcs']))
                w.writerow([r['pattern'], cat_cell, src_cell])
        _CAT_DIRTY[origin] = False
        print(f'flushed {path}')


def sortRemark(source, cost, remark):
    """Match remark against cat.csv rules. Returns int (single-cat rule),
    tuple of ints (multi-cat rule, needs user resolution), or None.
    Side effects: appends source-id to a matched rule when seen for the
    first time; auto-creates a DINING rule for paylah/UOB <$25 fallbacks."""
    sid = SOURCE_TO_ID.get(source)
    if sid is None:
        return None
    for rule in _CAT_RULES:
        if rule['regex'].match(remark):
            if sid not in rule['srcs']:
                rule['srcs'].add(sid)
                _markCatDirty(rule['origin'])
            return rule['cats'][0] if len(rule['cats']) == 1 else tuple(rule['cats'])
    # auto-categorize fallbacks: chase -> holiday (no rule saved); else dining.
    if sid == 1:
        return 9
    if cost < 25:
        default_cat, label = 1, 'dining'
    else:
        return None
    pattern = re.escape(remark)
    for rule in _CAT_RULES:
        if rule['pattern'] == pattern:
            if sid not in rule['srcs']:
                rule['srcs'].add(sid)
                _markCatDirty(rule['origin'])
            return rule['cats'][0] if len(rule['cats']) == 1 else tuple(rule['cats'])
    origin = _classifyOrigin(remark)
    _CAT_RULES.append({
        'pattern': pattern,
        'regex': re.compile(pattern),
        'cats': [default_cat],
        'srcs': {sid},
        'origin': origin,
    })
    _markCatDirty(origin)
    print(f'  AUTO-ADDED {label} rule [{source}]: {remark!r}')
    return default_cat


def formatExpenseRow(n, date, cost, remark, category, source):
    src = source if source else '-'
    cat = str(category) if category is not None else '-'
    date_str = date.strftime("%m/%d") if date is not None else ''
    return f'{n:>4}  {date_str:>5}  {cost:>8.2f}  {cat:>3}  {src:>9}  {remark}'


def _formatTotalRow(cost, cat_str, remark):
    return f'{"":>4}  {"":>5}  {cost:>8.2f}  {cat_str:>3}  {"Total":>9}  {remark}'


def _detectSource(name):
    upper = name.upper()
    if upper.startswith('CHASE'):
        return 'Chase'
    if upper.startswith('PLY') or upper.startswith('PLG'):
        return 'Paylah'
    if upper.startswith('UOB'):
        return 'UOB'
    raise ValueError(f'unknown statement source for name: {name!r}')


def _emptyData(name, source):
    return {
        'name': name,
        'source': source,           # 'Chase' | 'Paylah' | 'UOB'
        'stmt_date': None,
        'expenses': [],             # (date, cost, remark, category, src_label)
        'credits':  [],             # (date, cost, remark, kind, src_label)
    }


def _parseCost(s):
    s = s.replace(',', '')
    is_cr = s.endswith('CR')
    if is_cr:
        s = s[:-2]
    return float(s), is_cr


def _adjustYear(d, stmt_date):
    """If stmt_date is set, pick the year so that d <= stmt_date (handles
    Dec txns appearing in a Jan statement)."""
    if stmt_date is None:
        return d.replace(year=datetime.now().year)
    cand = d.replace(year=stmt_date.year)
    if cand > stmt_date:
        cand = cand.replace(year=stmt_date.year - 1)
    return cand

def _parseStmtDate(first_row, source):
    if not first_row:
        return None
    try:
        if source == 'UOB' and len(first_row) >= 2 and first_row[0] == 'Statement Date':
            s = re.sub(r'\s+', ' ', first_row[1].strip())
            return datetime.strptime(s, '%d %b %Y')
        if source == 'Chase':
            m = re.match(r'\s*\d{2}/\d{2}/\d{2}\s*-\s*(\d{2}/\d{2}/\d{2})\s*$', first_row[0])
            if m:
                return datetime.strptime(m.group(1), '%m/%d/%y')
        if source == 'Paylah':
            return datetime.strptime(first_row[0].strip(), '%d %b %Y')
    except ValueError:
        return None
    return None


def _parseChaseRow(fields, stmt_date):
    if len(fields) != 3:
        return None
    try:
        d = datetime.strptime(fields[0], '%m/%d')
        cost = float(fields[2])
    except ValueError:
        return None
    d = _adjustYear(d, stmt_date)
    remark = fields[1]
    return d, cost, remark, sortRemark('CHASE', cost, remark)


def _parsePaylahRow(fields, stmt_date):
    if len(fields) != 4 or 'CR' in fields[3]:
        return None
    try:
        d = datetime.strptime(fields[0], '%d %b')
        cost = float(fields[2])
    except ValueError:
        return None
    d = _adjustYear(d, stmt_date)
    return d, cost, fields[1]


def _parseUobRow(fields, stmt_date):
    if len(fields) != 4:
        return None
    try:
        d = datetime.strptime(fields[1], '%d %b')
        cost, is_cr = _parseCost(fields[3])
    except ValueError:
        return None
    d = _adjustYear(d, stmt_date)
    return d, cost, fields[2], is_cr


def _parseChaseTabula(name, rows, stmt_date):
    data = _emptyData(name, 'Chase')
    data['stmt_date'] = stmt_date
    for fields in rows:
        parsed = _parseChaseRow(fields, stmt_date)
        if parsed is None:
            continue
        date, cost, remark, cat = parsed
        data['expenses'].append((date, cost, remark, cat, 'CHASE'))
    return data


def _parsePaylahTabula(name, rows, stmt_date):
    data = _emptyData(name, 'Paylah')
    data['stmt_date'] = stmt_date
    src = name[:3].upper()  # 'PLG' or 'PLY'
    for fields in rows:
        parsed = _parsePaylahRow(fields, stmt_date)
        if parsed is None:
            continue
        date, cost, remark = parsed
        data['expenses'].append((date, cost, remark, sortRemark(src, cost, remark), src))
    return data


def _parseUOBTabula(name, rows, stmt_date):
    data = _emptyData(name, 'UOB')
    data['stmt_date'] = stmt_date
    current = None
    seen = set()
    for fields in rows:
        if len(fields) == 1 and fields[0] in UOB_HEADERS:
            s = UOB_HEADERS[fields[0]]
            if s not in seen:
                seen.add(s)
                current = s
            continue
        if current is None:
            continue
        parsed = _parseUobRow(fields, stmt_date)
        if parsed is None:
            continue
        date, cost, remark, is_cr = parsed
        if is_cr:
            kind = 'payment' if 'PAYMT THRU E-BANK/HOMEB/CYBERB' in remark else 'refund'
            data['credits'].append((date, cost, remark, kind, current))
        else:
            data['expenses'].append((date, cost, remark, sortRemark(current, cost, remark), current))
    return data

def _parseTabula(name):
    source = _detectSource(name)
    paths = sorted(glob.glob(f'{TAB_DIR}/tabula-{name}*.csv'))
    if not paths:
        raise FileNotFoundError(f'no tabula CSV for {name!r}')
    with open(paths[0]) as f:
        rows = list(csv.reader(f))
    stmt_date = _parseStmtDate(rows[0] if rows else None, source)
    body = rows[1:] if stmt_date else rows
    if source == 'Chase':
        return _parseChaseTabula(name, body, stmt_date)
    if source == 'Paylah':
        return _parsePaylahTabula(name, body, stmt_date)
    return _parseUOBTabula(name, body, stmt_date)


# ---- output ------------------------------------------------------------

def _printVerbose(data):
    for n, (date, cost, remark, category, source) in enumerate(data['expenses'], start=1):
        print(formatExpenseRow(n, date, cost, remark, category, source))


def _saveCsv(data):
    """Write a category-grouped CSV: credits at top (UOB only), then for
    each category section a label row, transactions sorted by remark+date,
    and a per-category subtotal. Ends with a grand Total row."""
    os.makedirs(OUT_DIR, exist_ok=True)
    path = f'{OUT_DIR}/{data["name"]}.csv'

    by_cat = {}
    multi = []
    for date, cost, remark, cat, src in data['expenses']:
        if isinstance(cat, tuple):
            multi.append((date, cost, remark, cat, src))
        else:
            by_cat.setdefault(cat, []).append((date, cost, remark, cat, src))

    grand = 0.0
    with open(path, 'w', newline='') as f:
        w = csv.writer(f)
        if data['stmt_date']:
            w.writerow(['Statement Date', ' ' + data['stmt_date'].strftime('%Y-%m-%d')])
            w.writerow([])
        w.writerow(['date', 'cost', 'remark', 'category', 'source'])

        for date, cost, remark, kind, src in data['credits']:
            w.writerow([date.strftime('%m/%d'), f'{-cost:.2f}', remark, kind, src or ''])
        if data['credits']:
            w.writerow([])

        cat_keys = sorted(c for c in by_cat if c is not None)
        if None in by_cat:
            cat_keys.append(None)
        n = 0
        for cat in cat_keys:
            label = f'{cat} {CATEGORIES[cat]}' if cat in CATEGORIES else 'uncategorized'
            w.writerow([label])
            entries = sorted(by_cat[cat], key=lambda r: (r[2].lower(), r[0]))
            sub = 0.0
            for date, cost, remark, c, src in entries:
                n += 1
                w.writerow([formatExpenseRow(n, date, cost, remark, c, src)])
                sub += cost
            cat_str = str(cat) if cat is not None else '-'
            remark_str = CATEGORIES[cat] if cat in CATEGORIES else 'uncategorized'
            w.writerow([_formatTotalRow(sub, cat_str, remark_str)])
            w.writerow([])
            grand += sub

        if multi:
            w.writerow(['multi-category'])
            entries = sorted(multi, key=lambda r: (r[2].lower(), r[0]))
            sub = 0.0
            for date, cost, remark, c, src in entries:
                n += 1
                cat_str = ','.join(str(x) for x in c)
                w.writerow([formatExpenseRow(n, date, cost, remark, cat_str, src)])
                sub += cost
            w.writerow([_formatTotalRow(sub, '', 'multi-category')])
            w.writerow([])
            grand += sub

        w.writerow([_formatTotalRow(grand, '', '')])
    print(f'wrote {path}')
    return grand


def _checkAgainstPrior(data):
    name = data['name']
    path = f'{PRIOR_DIR}/{name}.csv'
    if not os.path.exists(path):
        return

    def key(d, cost, remark):
        return (d, round(abs(cost), 2), remark)

    new_counter = Counter()
    for date, cost, remark, _, _ in data['expenses']:
        new_counter[key(date.strftime('%m/%d'), cost, remark)] += 1
    for date, cost, remark, _, _ in data['credits']:
        new_counter[key(date.strftime('%m/%d'), cost, remark)] += 1

    prior_counter = Counter()
    with open(path) as f:
        for row in csv.reader(f):
            if len(row) == 1:
                parsed = _parseExpenseRow(row[0])
                if parsed is None:
                    continue
                date, cost, _, _, remark = parsed
                prior_counter[key(date, cost, remark)] += 1
            elif len(row) >= 3 and re.match(r'^\d{2}/\d{2}$', row[0] or ''):
                try:
                    cost = float(row[1])
                except ValueError:
                    continue
                prior_counter[key(row[0], cost, row[2])] += 1

    if not prior_counter:
        return

    missing = prior_counter - new_counter
    extra = new_counter - prior_counter
    if missing:
        print(f'\n  WARN [{name}]: {sum(missing.values())} prior rows missing from new output:')
        for k, n in list(missing.items())[:20]:
            print(f'    -{n}x  {k}')
        if len(missing) > 20:
            print(f'    ... and {len(missing) - 20} more')
    elif extra:
        print(f'\n  OK [{name}]: all {sum(prior_counter.values())} prior rows present (new has {sum(extra.values())} additional)')
    else:
        print(f'\n  OK [{name}]: all {sum(prior_counter.values())} prior rows present in new output')


# ---- reconciliation ----------------------------------------------------

_EXPENSE_ROW_RE = re.compile(
    r'^\s*(\d+)\s+(\d{2}/\d{2})\s+([\d.]+)\s+(\S+)\s+(\S+)\s+(.*)$'
)


def _parseExpenseRow(s):
    """Parse a single-column row written by formatExpenseRow. Returns
    (date, cost, cat_cell, src, remark) or None."""
    m = _EXPENSE_ROW_RE.match(s)
    if not m:
        return None
    _, date, cost_s, cat_cell, src, remark = m.groups()
    try:
        cost = float(cost_s)
    except ValueError:
        return None
    return date, cost, cat_cell, src, remark


def _readParsedStmtDate(path):
    """Read the 'Statement Date, YYYY-MM-DD' header from a parsed CSV
    written by _saveCsv. Returns datetime or None."""
    with open(path) as f:
        for row in csv.reader(f):
            if row and row[0] == 'Statement Date' and len(row) >= 2:
                try:
                    return datetime.strptime(row[1].strip(), '%Y-%m-%d')
                except ValueError:
                    return None
            if row and row[0] == 'date':
                return None
    return None


def _readParsedCsv(path):
    """Read an expense csv produced by _saveCsv. Yields dicts for expense
    rows only (skips section headers, subtotals, credits). Uncategorized
    rows (cat cell '-') are included with cats=[] so reconcileUpdate can
    detect edits that categorize them."""
    rows = []
    with open(path) as f:
        for row in csv.reader(f):
            if len(row) != 1:
                continue
            parsed = _parseExpenseRow(row[0])
            if parsed is None:
                continue
            date, cost, cat_cell, src, remark = parsed
            cats = _parseNumList(cat_cell)
            rows.append({
                'date': date,
                'cost': cost,
                'remark': remark,
                'cats': cats,
                'src': '' if src == '-' else src,
            })
    return rows


def reconcileUpdate(name):
    """Reconcile statements_parsed/{name}-update.csv against {name}.csv.

    Edits must be made to the per-row cat cell (the column before the src
    column), not to section headers — section headers are ignored.

    For each row whose category list changed, find the cat.csv rule that
    originally matched the remark and append the user's chosen categories
    so the row surfaces in the multi-category section next time. Rows that
    had no rule match (uncategorized) are logged and skipped — add a regex
    to cat.csv by hand. Then re-sorts cat.csv, overwrites {name}.csv with
    the -update version, and removes -update.csv."""
    orig_path = f'{OUT_DIR}/{name}.csv'
    upd_path = f'{OUT_DIR}/{name}-update.csv'
    if not os.path.exists(upd_path):
        print(f'no -update.csv for {name}')
        return
    if not os.path.exists(orig_path):
        print(f'no original {orig_path}')
        return

    orig_rows = _readParsedCsv(orig_path)
    upd_rows = _readParsedCsv(upd_path)
    orig_total = sum(r['cost'] for r in orig_rows)
    upd_total = sum(r['cost'] for r in upd_rows)
    if round(orig_total, 2) != round(upd_total, 2):
        print(f'  WARN: total changed {orig_total:.2f} -> {upd_total:.2f} '
              f'(diff {upd_total - orig_total:+.2f}); aborting reconcile')
        return
    upd_by_key = {(r['date'], r['cost'], r['remark']): r for r in upd_rows}

    appended = []      # (rule_idx, pattern, new_cat)
    skipped = []       # (remark, src, new_cats) — no rule matched
    unchanged = 0
    for o in orig_rows:
        key = (o['date'], o['cost'], o['remark'])
        u = upd_by_key.get(key)
        if u is None:
            continue
        if sorted(o['cats']) == sorted(u['cats']):
            unchanged += 1
            continue
        idx = next((i for i, r in enumerate(_CAT_RULES)
                    if r['regex'].match(o['remark'])), None)
        if idx is None:
            skipped.append((o['remark'], o['src'], u['cats']))
            continue
        rule = _CAT_RULES[idx]
        for c in u['cats']:
            if c not in rule['cats']:
                rule['cats'].append(c)
                _markCatDirty(rule['origin'])
                appended.append((idx, rule['pattern'], c))

    if appended:
        print(f'\n  APPENDED categories to existing rules:')
        for idx, pattern, cat in appended:
            print(f'    +{cat} -> /{pattern}/')
    if skipped:
        print(f'\n  UNMATCHED (add a regex to cat.csv by hand):')
        for remark, src, cats in skipped:
            print(f'    cats={cats} src={src}: {remark}')
    print(f'  ({unchanged} rows unchanged, {len(appended)} appends, {len(skipped)} unmatched)')

    flushCatCsv()
    regroupUpdate(name)
    os.remove(upd_path)
    print(f'reconciled {name}: regrouped {orig_path}, removed -update.csv')


def regroupUpdate(name):
    """Re-parse {name}-update.csv and rewrite {name}.csv so entries whose
    category list was edited (e.g. a multi-category row narrowed to a
    single category) are placed under the correct section. Pure regrouping
    — does not touch cat.csv. Leaves -update.csv in place."""
    upd_path = f'{OUT_DIR}/{name}-update.csv'
    orig_path = f'{OUT_DIR}/{name}.csv'
    if not os.path.exists(upd_path):
        print(f'no -update.csv for {name}')
        return
    if not os.path.exists(orig_path):
        print(f'no original {orig_path}')
        return

    with open(upd_path) as f:
        rows = list(csv.reader(f))

    stmt_date = _readParsedStmtDate(upd_path)

    def _toDate(s):
        d = datetime.strptime(s, '%m/%d')
        return _adjustYear(d, stmt_date)

    expenses = []
    for r in _readParsedCsv(upd_path):
        cat = r['cats'][0] if len(r['cats']) == 1 else tuple(r['cats'])
        src = r['src'] or None
        expenses.append((_toDate(r['date']), r['cost'], r['remark'], cat, src))

    credits = []
    for row in rows:
        if len(row) >= 5 and re.match(r'^\d{2}/\d{2}$', row[0] or ''):
            try:
                signed = float(row[1])
            except ValueError:
                continue
            credits.append((_toDate(row[0]), -signed, row[2], row[3], row[4] or None))

    data = {
        'name': name,
        'source': _detectSource(name),
        'stmt_date': stmt_date,
        'expenses': expenses,
        'credits': credits,
    }
    _saveCsv(data)


# ---- public entrypoint -------------------------------------------------

def parseStatement(name, year=None, verbose=False, checkPrior=False):
    """Convert a statement PDF to tabula-<name>.csv, then parse and write a
    category-grouped <name>.csv.

    name: a statement short-name (e.g. 'UOB02', 'Chase01'); resolved to a
        PDF in statements/ matching {name}*.pdf.
    year: ignored if the tabula CSV has a recognisable Statement Date row
        (UOB), period header (Chase), or stmt-date line (Paylah). Falls
        back to the current year only when none is detectable.
    verbose: print each transaction in parse order, then the contents of
        the generated category-grouped CSV.
    checkPrior: cross-check against statements_parsed_prior/ to flag any
        missing transactions.
    """

    from pdf2csv import convert, short_name
    paths = glob.glob(f'{IN_DIR}/{name}*.pdf')
    if not paths:
        raise FileNotFoundError(f'no PDF in {IN_DIR}/ matching {name!r}')
    src = paths[0]
    name = short_name(src)
    os.makedirs(TAB_DIR, exist_ok=True)
    convert(src, f'{TAB_DIR}/tabula-{name}.csv')

    data = _parseTabula(name)
    if year is not None and data['stmt_date'] is None:
        data['stmt_date'] = datetime(year, 12, 31)
        for i, e in enumerate(data['expenses']):
            data['expenses'][i] = (_adjustYear(e[0], data['stmt_date']),) + e[1:]
        for i, c in enumerate(data['credits']):
            data['credits'][i] = (_adjustYear(c[0], data['stmt_date']),) + c[1:]

    if verbose:
        _printVerbose(data)
    grand = _saveCsv(data)
    print(f'  {len(data["expenses"])} txns, grand total {grand:.2f}')
    if verbose:
        with open(f'{OUT_DIR}/{name}.csv') as f:
            print(f.read())
    if checkPrior:
        _checkAgainstPrior(data)
    flushCatCsv()
    return data
