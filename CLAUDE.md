# CLAUDE.md

Guide for working in this repo. Read this before grepping — the module map below
is meant to take you straight to the right file.

## What it is

`trex` turns credit-card PDF statements into categorized monthly summaries.
Installed package on a src layout; `make setup` (uv, locked by `uv.lock`).

Pipeline: `data/statements/*.pdf` → `data/extracted/*.csv` → `data/parsed/*.csv`
→ `data/summary/summary<year>.csv` plus one combined
`summary<year>_<NNmon>.csv` per month and a `healthcare<year>.csv` listing
(derived; rebuilt by every `trex summarize`). The healthcare listing marks each
charge with the IHP insurance claim covering it, read from the portal exports
`data/statements/IHP<plan><MM>.xls` (tab-separated text despite the name; plan
G or Y, MM the download month); a blank `ref` means never claimed. `statement_totals<year>.csv` is
not derived: it records each statement's spending once, and `trex summarize`
exits 1 if a recorded total later changes (`--accept-totals` to take it).

## Commands

```bash
make setup                   # uv sync --extra dev, and point git at .githooks/
make check                   # lint + mypy + pytest: what pre-push and CI run
make fmt                     # ruff format + ruff check --fix
uv run pytest                # needs no statement PDFs
trex --help
trex summarize               # checks on by default; --no-check, --no-fail-on-*
trex summarize --push        # then commit all changed data/ CSVs + cat.csv, and push
trex rules {regroup,sort-brands,suggest-brands,stats}   # cat.csv / brands.csv upkeep
```

Hooks: pre-commit blocks any staged PDF or `data/statements/` path and any
notebook with cell outputs (they'd hold real transactions), then runs
ruff on staged files and rejects files over 1 MiB, breakpoints and conflict
markers. commit-msg enforces Conventional Commits: types `feat fix docs style
refactor perf test build ci chore revert`, scopes `extract parse categorize
categories summary reconcile cli ci deps hooks`, subject ≤ 72 chars, no trailing
period. pre-push runs `make check`. CI runs the same targets after `uv sync
--locked` on 3.10 (the floor) and 3.12.

Use the repo's interpreter for anything ad hoc: `.venv/bin/python` (uv, 3.12,
pinned by `.python-version`). An older system `python3` is below
`requires-python` and will fail to import the package.

## Module map

Where to look, by stage. Each module's docstring says what it owns.

| Stage | Modules |
| --- | --- |
| Foundation | `config.py` (**every path**), `constants.py` (categories, cards, issuers, healthcare keywords), `models.py`, `cells.py`, `log.py` |
| Extract | `extract/`: `pdf.py` word geometry, one module per issuer's PDF quirks |
| Categorize | `categorize/`: `rules.py` (cat.csv, read-only cat_personal.csv), `brands.py`, `regroup.py` (`trex rules`), `stats.py` (`trex rules stats` → untracked `cat_stats.csv`) |
| Parse | `parse/` (`parse_statement`, `check_year_balances`; `balance.py` checks printed totals), `serialize.py` (**the parsed-CSV format**) |
| Summarize | `summary/`: `__init__.py` (`summarize`, picks medical charges), `rows.py` (reading, SGD), `table.py` (`YearSummary`: category × month table), `checks.py` (warnings, year-summary total check), `monthly.py` (month files), `totals.py` (`statement_totals<year>.csv`) |
| Healthcare | `healthcare/`: `claims.py` (IHP exports), `matching.py` (charge ↔ claim), `listing.py` (**the listing format**) |
| Edit and CLI | `reconcile.py`, `vcs.py` (`--commit` / `--push`), `cli.py` |

Dependencies run one way, in the order `RANKS` gives in
`tests/test_conventions.py`: a module imports only from a lower rank or its own
package. Place a new module there; `make check` fails on an import that points
up, which is how the old import cycle stays gone.

**Issuer vs card.** An *issuer* is a statement family (Chase, PayLah, UOB) and
picks the extractor and parser. A *card* is the label a row belongs to
(`CARDS_BY_ID`; a PayLah wallet counts as one). A UOB statement holds several cards.

## Invariants

- **No path literals outside `config.py`.** Take an optional `Path` argument
  defaulting to the config value — that's what makes a function testable.
- **No file I/O at import time.** Construct a `CatRules` and call `load()`.
  Rules used to load on import, which made a wrong cwd crash the import.
- **`serialize.py` is the only module that knows the parsed-CSV layout**, and
  `healthcare/listing.py` the only one that knows the healthcare listing's.
- **`logging`, never `print()`.** The library logs; the CLI configures a handler.
- Public names are snake_case, verb-first, and say the outcome rather than the
  mechanism (`classify_remark`, not `sortRemark`). Docstrings say what the name
  and signature can't: a rule, an edge case, a side effect, or why. Leave one
  out rather than restate the name (`row_total` needs none).

## Module layout

Every module opens the same way: docstring, imports, `logger`, constants and
type aliases, then types (a type before any type holding it), then a
`<file>_name(year)` helper if it has one. Functions follow in one of two orders,
by the module's kind:

- **Pipeline** (one public function reaches most of the rest): the entry point
  first, then what it calls, depth-first in call order, so you only read
  downward. A helper with several callers goes under the first.
  `extract/`, `parse/__init__.py`, `parse/files.py`, `parse/balance.py`,
  `reconcile`, `categorize/{regroup,stats}`, `summary/{__init__,monthly,totals}`,
  `healthcare/`, `vcs`, `cli`.
- **Toolkit** (public functions mostly called from other modules): the public
  API first, grouped by topic, a primitive before what builds on it, private
  helpers at the end of their group. `config`, `constants`, `models`, `cells`,
  `log`, `categorize/{__init__,rules,brands}`, `extract/pdf.py`,
  `parse/rows.py`, `summary/{rows,checks,table}`, `serialize`.

Two or more groups may each get a `# --- topic ---` banner. A registry of
functions (`PARSERS`) goes right after the last one it names; `main()` stays
last in `cli.py`.

## Recipes

**Add a category**: add to `CATEGORIES` in `constants.py`. Nothing else reads a
hardcoded id list.

**Add a card**: add its label to `CARDS_BY_ID`; for UOB, add its statement
header to `UOB_CARD_HEADERS`. An unmapped UOB header fails the balance check
(`trex parse` exits 1) rather than losing that card's rows silently.

**Add an issuer**: add its `ISSUER_*` name and filename prefix to
`ISSUER_BY_PREFIX` in `constants.py`; new module in `extract/` returning CSV
lines, registered in `EXTRACTORS` under its issuer; add a row reader in
`parse/rows.py` and a `_parse_*` registered in `PARSERS` in `parse/files.py`.

**Change a categorization**: edit the parsed CSV, then `trex reconcile
<name>` (see README) rather than editing `cat.csv` manually — it keeps
the rule and the output in step.

## Data and privacy

This is a public repo. `data/statements/`, `data/extracted/`, `data/parsed/` and
`data/summary/` hold real personal statements, the `IHP*.xls` claims exports
(which name family members) and everything derived from them: all are
gitignored (only the `.gitkeep` placeholders are tracked), and nothing from them
is ever pasted into output. So are `cat_personal.csv`, `not_claimable.csv`,
`not_medical.csv` and `cat_stats.csv` in `data/categories/`, all absent by
default. `.gitignore` also ignores every `*.pdf`, and the pre-commit hook blocks
them and notebooks with cell outputs. For the same reason the project's Claude
settings deny reads of `data/statements/`, `data/extracted/`, `data/parsed/`,
`data/summary/`, `cat_personal.csv`, `not_claimable.csv`, `cat_stats.csv` and
any PDF, and ask before `not_medical.csv`. The committed `cat.csv` and
`brands.csv` are public and readable, so audit them freely. Work from
`tests/fixtures/data` instead: it's anonymized and holds UOB and PayLah (PLG)
samples; Chase and PLY cases are inline in `tests/test_extract.py` and friends.
These rules cover Claude's file tools only, not Bash, so don't `cat` or `grep`
any of these paths either.

Rules naming a person rather than a merchant live in
`data/categories/cat_personal.csv` (`CatRules.load` skips it when missing).
`CatRules` loads it alongside cat.csv but never writes to it, so no reconcile or
`trex rules regroup` can pull those names into the file the pipeline rewrites —
keep it that way.

`data/categories/cat.csv` **is** committed here, so it must stay merchant-only.
Many merchant rules legitimately carry a name and a PayNow number — a hawker
stall or a fishmonger is a business. The line is who is being paid, not the shape
of the remark: before committing a change to cat.csv, check that every rule with
a person's name or an 8-digit number is a business. Anything else goes in
`cat_personal.csv`. The same goes for examples in code and tests: use made-up
names and numbers.

## Verifying a change to the pipeline

Output format changes are easy to make accidentally. After a change, **ask the
user** whether to run the pipeline on their real data (reading `data/` needs
their permission; see above). `data/parsed/` is not committed here, so copy it
first, then:

```bash
cp -R data/parsed /tmp/parsed-before
ls data/parsed | sed 's/\.csv$//' | xargs uv run trex parse   # every statement
uv run trex summarize
diff -rq /tmp/parsed-before data/parsed
```

Both commands must exit 0 and the parsed files must be unchanged: the user's
data must never change as a side effect of a code change. A new balance or UNI$
mismatch means the change broke extraction, not that the data is wrong, so fix
the code and restore `data/parsed/` from the copy. If files changed, list them
(`diff -rq`, never the full diff, which prints transaction rows) and report
which changes are intended. Ask the user before looking at row-level detail.
