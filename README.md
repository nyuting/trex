# trex

Turn credit-card PDF statements into categorized monthly expense summaries.

Three stages, each runnable on its own:

```
data/statements/*.pdf  ──extract──▶  data/extracted/*.csv
                       ──parse────▶  data/parsed/*.csv      (categorized, manually editable)
                       ──summarize▶  data/summary/summary<year>.csv
                                     data/summary/summary<year>_<NNmon>.csv
                                     data/summary/healthcare<year>.csv
```

Supported issuers, with the cards each statement covers: Chase (CHASE), DBS PayLah
(PLG and PLY, one wallet per statement), UOB (AMEX / VISA / ONE / LADY in one
statement).

## Install

```bash
make setup        # uv sync --extra dev, and activate the git hooks
make check        # lint, type-check and test
```

Requires [uv](https://docs.astral.sh/uv/) and Python 3.10+ (3.12 is pinned for
development). The only runtime dependency is `pdfplumber`; `pip install -e ".[dev]"`
still works without uv.

## Use

```bash
trex extract                       # every PDF in data/statements -> data/extracted
trex parse UOB05 Chase05           # extract, categorize and write data/parsed/<name>.csv
trex parse PLG05 --year 2026       # --year is used only if the statement is undated
trex parse UOB06 --commit          # also commit its CSVs and cat.csv (skipped if unbalanced)
trex parse UOB06 Chase06 --commit-balanced  # commit only the balanced ones; still exits 1
trex reconcile UOB05               # turn category fixes in data/parsed/UOB05.csv into rules (below)
trex summarize --year 2026 --check # category x month table, then verify its total
trex summarize --accept-totals     # take changed statement totals (see below)
trex summarize --fail-on-uncategorized --fail-on-multi-category  # exit 1 if any are left
```

`trex parse` warns when rows are still filed under several categories; narrow
them and reconcile, as below.

Commands work from any directory — paths resolve from the repo root, or from
`TREX_DATA_DIR` if set.

The same functions are importable:

```python
from trex import parse_statement, summarize

parse_statement("UOB05")
summarize(year=2026)
```

## Fixing a miscategorized transaction

Parsed CSVs are meant to be edited: fix a category by hand, then `trex
reconcile` turns the fix into a `cat.csv` rule so future statements get it right.

Each expense in `data/parsed/<name>.csv` is one fixed-width row:

```
   1  01/03     18.40    1    UOB-ONE  ACME COFFEE HOUSE
   #  date       cost  categories  card  remark
```

The **categories** cell is the only one to edit. It holds category ids
(`1` = DINING … `10` = GIFTS; see below), comma-separated when a rule files the
row under several: those rows sit in the `multi-category` section and are
quoted, e.g. `"   6  01/25     63.75  2,7    UOB-ONE  SUNDRY SHOP 88"`; the
quotes can stay. `-` means no rule matched. Don't move rows between sections
(the re-parse re-files them), and don't add, remove or re-price rows.

1. Edit the categories cell of every wrong row, in one statement or several.
2. `trex reconcile UOB05` (or `trex reconcile UOB05 Chase05 …`: edits across
   statements are folded into the rules together). Each edited row is compared
   with what the current rules make of `data/extracted/UOB05.csv`, the rules
   are updated, and the file is re-parsed with them.
3. Read the log (CHANGED and ADDED rules, rows the re-parse moved, person
   transfers left for you) and `git diff data/`.

What an edit does:

| You change the cell | cat.csv | Does a later `trex parse` keep it? |
| --- | --- | --- |
| `1` → `4` | the rule that matched becomes `4` | yes |
| `2,7` → `7` (narrowing) | unchanged: a choice for this row only | yes, carried over from the parsed file |
| `2,7` → `4,7` | the rule's `2` becomes `4`, `7` kept | yes |
| `2,7` → `4` | the rule gains `4`; the merchant evidently varies | yes, carried over |
| one rule's rows → `8` here, `10` there | the rule becomes `8,10` | yes |
| `-` → `3` | a new exact-match rule for that remark | yes |
| `SEND MONEY TO …` → anything | nothing; the remark is listed for `cat_personal.csv` | **no**, until you add that rule manually |

Because the re-parse uses the updated rules, other rows those rules match can
change category too; each such move is logged.

A statement whose total changed is skipped, since that means rows were added,
lost or re-priced rather than recategorized. The other statements still
reconcile, and the command exits 1.

Add `--commit` to commit the reconciled parsed CSVs and `cat.csv` (nothing
else), or `--push` to commit and push; `trex parse` takes the same flags. To
undo a reconcile: `git checkout -- data/parsed/UOB05.csv data/categories/cat.csv`.
In this public repo `data/parsed/` is gitignored, so these flags commit
`cat.csv` alone; they are meant for a private copy that tracks `data/`.

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
header `regex,category,card` if you want one.

Maintenance commands:

```bash
trex rules regroup         # merge rules describing the same merchant
trex rules suggest-brands  # tokens worth adding to brands.csv
trex rules sort-brands
```

`brands.csv` holds merchant names used to recognize that `ACME - ORCHARD` and
`ACME BISHAN 04` are the same shop, so they can share one rule.

Categories: DINING, GROCERIES, TRANSPORT, KIDS, GANSHUN, YUTING, HOME,
HEALTHCARE, HOLIDAY, GIFTS — defined in `src/trex/constants.py`.

The `cat.csv` published here is a real working ruleset. Rules for paying an
individual rather than a business belong in `cat_personal.csv` and are not
published.

## Data layout

```
data/
  statements/   input PDFs — private, gitignored, never committed
  extracted/    raw transcription of each PDF — gitignored
  parsed/       categorized output, one file per statement — gitignored
  summary/      gitignored. summary<year>.csv (category x month), and summary<year>_01jan.csv …:
                each month's expenses and credits across every card, in SGD;
                healthcare<year>.csv: every HEALTHCARE expense of the year, to
                check insurance claims against.
                Rebuilt by every `trex summarize` — fix categories in parsed/, not here.
                statement_totals<year>.csv: each statement's spending, a row per
                month (as in the file name: Chase01, PLY0102 -> 01) and a column
                per card, Chase in USD and SGD. Recorded once, then guarded: if a
                statement later adds up differently, summarize warns, keeps the
                recorded value and exits 1 until run with --accept-totals.
                summarize also balance-checks every statement of the year from
                extracted/ (Chase, UOB, PayLah: previous − credits + spending =
                printed closing balance), logs OK/FAIL per statement, and exits 1
                on any FAIL.
  categories/   cat.csv and brands.csv (versioned); cat_personal.csv (gitignored)
```

Chase amounts are USD and converted at `EXCHANGE_RATE_USD2SGD` in
`src/trex/config.py`; everything else is already SGD.

## Development

```bash
make check        # ruff, mypy and pytest: what the pre-push hook and CI run
make fmt          # ruff format + ruff check --fix
uv run pytest     # tests only; no statement PDFs required
```

`make setup` also turns on the git hooks: pre-commit blocks statement PDFs and
lints staged files, commit-msg enforces Conventional Commits, pre-push runs
`make check`. CI runs `make check` on Python 3.10 and 3.12.

Tests run against `tests/fixtures/data` via `TREX_DATA_DIR`, so they never touch
your real statements.
