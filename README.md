# trex

Turn credit-card PDF statements into categorized monthly expense summaries.

Three stages, each runnable on its own:

```
data/statements/*.pdf  ──extract──▶  data/extracted/*.csv
                       ──parse────▶  data/parsed/*.csv      (categorized, manually editable)
                       ──summarize▶  data/summary/summary<year>.csv
                                     data/summary/summary<year>_<NNmon>.csv
                                     data/summary/healthcare<year>.csv
data/statements/IHP*.xls ─summarize▶  (IHP claims, marked on healthcare<year>.csv)
```

Supported issuers, with the cards each statement covers: Chase (CHASE), DBS PayLah
(PLG and PLY, one wallet per statement), UOB (AMEX / VISA / ONE / LADY in one
statement).

## Install

```bash
make setup        # uv sync --extra dev, and activate the git hooks
```

Requires [uv](https://docs.astral.sh/uv/) and Python 3.10+ (3.12 is pinned for
development). The only runtime dependency is `pdfplumber`; `pip install -e ".[dev]"`
still works without uv. See [Development](#development) for the checks.

## Use

```bash
trex extract                       # every PDF in data/statements -> data/extracted
trex parse UOB05 Chase05           # extract, categorize and write data/parsed/<name>.csv
trex parse PLG05 --year 2026       # --year is used only if the statement is undated
trex parse UOB06 --commit          # also commit its CSVs and cat.csv (skipped if unbalanced)
trex parse UOB06 Chase06 --commit-balanced  # commit only the balanced ones; still exits 1
trex reconcile UOB05               # turn category fixes in data/parsed/UOB05.csv into rules (below)
trex summarize --year 2026         # category x month table; exit 1 if its total is off or
                                   # any row is uncategorized or multi-category
trex summarize --no-fail-on-uncategorized  # relax a check (also --no-check,
                                   # --no-fail-on-multi-category)
trex summarize --accept-totals     # take changed statement totals (see below)
```

`trex parse` warns when rows are still filed under several categories; narrow
them and reconcile, as below.

Commands work from any directory — paths resolve from the repo root, or from
`TREX_DATA_DIR` if set.

The same functions are importable:

```python
from trex import parse_statement, summarize
from trex.log import configure_logging

configure_logging()  # show trex's messages; verbose=True adds debug detail
parse_statement("UOB05")
summarize(year=2026)
```

trex reports through `logging`, which the CLI sets up to print to stderr.
Imported, nothing sets it up, so call `configure_logging()` once or only
warnings and errors show.

## New statements

Once a month's PDFs are in `data/statements/`:

```bash
trex parse Chase09 PLG09               # 1. extract, categorize, balance-check
# 2. fix wrong categories in data/parsed/<name>.csv (below), then:
trex reconcile Chase09 PLG09           #    only if you edited something
trex summarize --push                  # 3. summarize, then commit and push the month
```

`trex parse` exits 1 naming any statement that fails its balance check.
`trex summarize` records the new statements' totals in
`statement_totals<year>.csv`; put any new IHP export in `data/statements/`
first so the healthcare listing marks the claims. With `--commit` (or `--push`
to also push) it then commits, in one `chore(data): summarize <year>` commit,
every extracted, parsed and summary CSV and `cat.csv` that differs from HEAD,
and nothing else, so code edits in progress stay out. It commits only if every
check passes. Committing a step at a time with `trex parse`'s or `trex
reconcile`'s `--commit` works too. In this public repo `data/` is gitignored
apart from `data/categories/`, so these flags commit `cat.csv` alone; they are
meant for a private copy that tracks `data/`.

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

Add `--commit` to commit the reconciled statements' CSVs and `cat.csv` (nothing
else), or `--push` to commit and push; `trex parse` and `trex summarize` take
the same flags. To
undo a reconcile: `git checkout -- data/parsed/UOB05.csv data/categories/cat.csv`.

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
trex rules stats           # count, last seen, mean and spread per rule -> cat_stats.csv (untracked)
```

`brands.csv` holds merchant names used to recognize that `ACME - ORCHARD` and
`ACME BISHAN 04` are the same shop, so they can share one rule.

Categories: DINING, GROCERIES, TRANSPORT, KIDS, PERSON_A, PERSON_B, HOME,
HEALTHCARE, HOLIDAY, GIFTS — defined in `src/trex/constants.py`.

The `cat.csv` published here is a real working ruleset. Rules for paying an
individual rather than a business belong in `cat_personal.csv` and are not
published.

## Insurance claims

Download the claims history from the IHP portal as `.xls` and save it in
`data/statements/` as `IHP<plan><MM>.xls`: the plan letter is whose account it
is, one letter per account holder (e.g. `IHPG10.xls`), and MM the month it was
downloaded. Keep the old exports; each `trex summarize` reads them all, taking
each claim from the newest export it appears in.

`healthcare<year>.csv` is then one table — `date, ihp, ref, patient, provider,
claim_type, card, remark, category, cost, incurred, payable, claim_status,
match` (plus `not_ref` once a pair is marked `wrong`) — with a row per medical charge and per claim no charge
accounts for. `match` says which, in the order the rows are listed:

- `review`, `to claim` or `not claimable`: **no claim found**. A new one
  reads `review`: decide, and replace it with `to claim` (claim it, then it
  matches once the claim shows up in an export) or `not claimable` (a gym pass,
  a premium). Grouped in that order, then by remark and date; `to claim` and
  `review` rows add up to the `unclaimed` total, `not claimable` ones to their
  own. Remarks matching a regex in `data/categories/not_claimable.csv`
  (`regex` column, case-insensitive; read, never written) start out
  `not claimable` instead of `review`.
- `check`: a nearby claim at a provider the remark names (by word or initials,
  e.g. SGH), or else within 10% of the amount — never a claim at a different
  provider than the one the remark names: look at it by eye.
- `exact`: a claim for the same amount, incurred up to 7 days before the charge
  (`--window` to change) or a day after.
- `copay`: a panel clinic's co-payment — the claim's bill less what the plan
  paid, to within 1% of the bill.
- `checked`: a `check` you confirmed (see below).
- `no charge`: a claim no card charge accounts for (a panel visit, cash,
  another card), with the card columns blank.

Charges with a claim are grouped by plan, patient, remark, then date; `no
charge` rows by plan, patient, then date.

To settle a `check` row for good, edit its `match` cell in
`healthcare<year>.csv` to `checked` (it is this claim) or `wrong` (it isn't),
then run `trex summarize`. The marks are read back before the file is rebuilt,
so they survive every later run: a `checked` pair stays paired, and a `wrong`
ref is kept in `not_ref` and never proposed for that charge again (the charge
moves to its next candidate, or to unclaimed). The file is gitignored here, so
back it up if you keep marks in it. A mark whose charge or claim later disappears is dropped
with a warning.

A charge that looks medical (SGH, KKH, SATA, DENTAL, CLINIC…) but is filed
under another category is listed and matched too, its `category` column naming
where it is filed, and logged as a warning. It is left out of the `total` and
`unclaimed` lines, which cover HEALTHCARE only so they agree with the summary;
an `unclaimed, filed outside HEALTHCARE` line totals the unclaimed ones. Move
genuine ones to HEALTHCARE and `trex reconcile`; add merchants that only look
medical (a juice stall inside a hospital, a shop at Parkway Parade) to
`data/categories/not_medical.csv` (`regex` column, case-insensitive, an
optional `note`; read, never written). Both `not_claimable.csv` and
`not_medical.csv` are gitignored and ship absent; without them nothing is
excepted. An export line trex can't read — an
unknown status, a bad date or amount — makes `trex summarize` exit 1 naming it.

## Data layout

```
data/
  statements/   input PDFs and IHP exports — private, gitignored, never committed
  extracted/    raw transcription of each PDF — gitignored
  parsed/       categorized output, one file per statement — gitignored
  summary/      everything `trex summarize` writes (below) — gitignored
  categories/   cat.csv and brands.csv (versioned); cat_personal.csv,
                not_claimable.csv, not_medical.csv, cat_stats.csv (gitignored)
```

`summary/` holds, per year:

- `summary<year>.csv`: the category × month table.
- `summary<year>_01jan.csv` …: each month's expenses and credits across every
  card, in SGD.
- `healthcare<year>.csv`: every medical charge, marked with the IHP claim
  covering it (see "Insurance claims").
- `statement_totals<year>.csv`: each statement's spending, a row per month (as
  in the file name: Chase01, PLY0102 → 01) and a column per card, Chase in USD
  and SGD. Recorded once, then guarded: if a statement later adds up
  differently, summarize warns, keeps the recorded value and exits 1 until run
  with `--accept-totals`.

All but the totals file are rebuilt on every run, so fix categories in
`parsed/`, not here. `trex summarize` also balance-checks every statement of
the year from `extracted/` (previous − credits + spending = printed closing
balance, for Chase, UOB and PayLah), logs OK or FAIL per statement, and exits 1
on any FAIL.

Chase amounts are USD and converted at `EXCHANGE_RATE_USD2SGD` in
`src/trex/config.py`; everything else is already SGD.

## Development

```bash
make check        # ruff, mypy and pytest: what the pre-push hook and CI run
make fmt          # ruff format + ruff check --fix
uv run pytest     # tests only; no statement PDFs required
```

`make setup` also turns on the git hooks: pre-commit blocks statement PDFs and
notebooks with outputs, and lints staged files, commit-msg enforces Conventional Commits, pre-push runs
`make check`. CI runs `make check` on Python 3.10 and 3.12.

Tests run against `tests/fixtures/data` via `TREX_DATA_DIR`, so they never touch
your real statements.
