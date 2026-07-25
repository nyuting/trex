# trex

Turn credit-card PDF statements into categorized monthly expense summaries.

Three stages, each runnable on its own:

```
data/statements/*.pdf  ──extract──▶  data/extracted/*.csv
                       ──parse────▶  data/parsed/*.csv      (categorized, hand-editable)
                       ──summarize▶  data/parsed/summary<year>.csv
```

Supported issuers: Chase, DBS PayLah (PLG / PLY), UOB (AMEX / VISA / ONE / LADY).

## Install

```bash
pip install -e ".[dev]"
```

Requires Python 3.10+. The only runtime dependency is `pdfplumber`.

## Use

```bash
trex extract                       # every PDF in data/statements -> data/extracted
trex parse UOB05 Chase05           # extract, categorize and write data/parsed/<name>.csv
trex parse PLG05 --year 2026       # --year is used only if the statement is undated
trex summarize --year 2026 --check # category x month table, then verify its total
```

Commands work from any directory — paths resolve from the repo root, or from
`TREX_DATA_DIR` if set.

The same functions are importable:

```python
from trex import parse_statement, summarize

parse_statement("UOB05")
summarize(year=2026)
```

## Fixing a miscategorized transaction

Parsed CSVs are meant to be edited. Rows land in a section per category, with
anything matching several categories in a `multi-category` section at the end.

1. `cp data/parsed/UOB05.csv data/parsed/UOB05-update.csv`
2. Edit the category cell (the column before the source column) of any wrong row.
   Leave section headers alone; don't add or remove rows.
3. `trex regroup UOB05` to preview where rows will land, or
   `trex reconcile UOB05` to commit: the corrected categories are appended to
   the `cat.csv` rule that matched each remark, so the fix applies to future
   statements too, and the `-update.csv` is consumed.

Reconcile refuses to run if the total changed — that means rows were added or
lost, which is a mistake rather than a recategorization. Rows whose remark
matches no rule are reported instead of guessed at; add a regex to `cat.csv` by
hand for those.

`trex rules pending` lists statements with an unreconciled `-update.csv`.

## Categorization rules

`data/categories/cat.csv` maps a remark regex to category ids and records which
cards it has been seen on. Two fallbacks apply when no rule matches: Chase
spending is HOLIDAY, and a purchase under $25 on any other card is DINING (which
also writes a new rule so it is explicit next time).

`cat_personal.csv` sits beside it and holds the same kind of rule for
person-to-person transfers — PayNow to a name, egifts, money sent to a phone
number. It is loaded and matched exactly like `cat.csv`, but nothing ever writes
to it, so the names in it stay out of the file the pipeline rewrites and out of
`trex rules regroup`. It is gitignored and ships absent — create it with the
header `regex,category,source` if you want one.

Maintenance commands:

```bash
trex rules regroup         # merge rules describing the same merchant
trex rules suggest-brands  # tokens worth adding to brands.csv
trex rules sort-brands
```

`brands.csv` holds merchant names used to recognize that `ACME - ORCHARD` and
`ACME BISHAN 04` are the same shop, so they can share one rule.

The `cat.csv` published here is a real working ruleset. Rules for paying an
individual rather than a business belong in `cat_personal.csv` and are not
published.

Categories: DINING, GROCERIES, TRANSPORT, KIDS, GANSHUN, YUTING, HOME,
HEALTHCARE, HOLIDAY, GIFTS — defined in `src/trex/constants.py`.

## Data layout

```
data/
  statements/   input PDFs — private, gitignored, never committed
  extracted/    raw transcription of each PDF — gitignored
  parsed/       categorized output, plus summary<year>.csv — gitignored
  categories/   cat.csv and brands.csv (versioned); cat_personal.csv (gitignored)
```

Chase amounts are USD and converted at `EXCHANGE_RATE_USD2SGD` in
`src/trex/config.py`; everything else is already SGD.

## Development

```bash
pytest                # 178 tests, no statement PDFs required
ruff check --fix .
ruff format .
```

Tests run against `tests/fixtures/data` via `TREX_DATA_DIR`, so they never touch
your real statements.
