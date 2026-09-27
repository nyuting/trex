# CLAUDE.md

Guide for working in this repo. Read this before grepping — the module map below
is meant to take you straight to the right file.

## What it is

`trex` turns credit-card PDF statements into categorized monthly summaries.
Installed package on a src layout; `make setup` (uv, locked by `uv.lock`).

Pipeline: `data/statements/*.pdf` → `data/extracted/*.csv` → `data/parsed/*.csv`
→ `data/summary/summary<year>.csv` plus one combined
`summary<year>_<NNmon>.csv` per month and a `healthcare<year>.csv` listing
(derived; rebuilt by every `trex summarize`). `statement_totals<year>.csv` is
not derived: it records each statement's spending once, and `trex summarize`
exits 1 if a recorded total later changes (`--accept-totals` to take it).

## Commands

```bash
make setup                   # uv sync --extra dev, and point git at .githooks/
make check                   # lint + mypy + pytest: what pre-push and CI run
make fmt                     # ruff format + ruff check --fix
uv run pytest                # needs no statement PDFs
trex --help
trex summarize --fail-on-uncategorized --fail-on-multi-category
trex rules {regroup,sort-brands,suggest-brands}   # cat.csv / brands.csv upkeep
```

Hooks: pre-commit blocks any staged PDF or `data/statements/` path, then runs
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

| File | Owns |
| --- | --- |
| `config.py` | **Every path.** `TREX_DATA_DIR`, else `data/` under the nearest `pyproject.toml` ancestor; `EXCHANGE_RATE_USD2SGD` |
| `constants.py` | `CATEGORIES`, `HEALTHCARE_CATEGORY_ID`, `CARDS_BY_ID`, UOB card headers, issuer names, `PAYLAH_TOPUP_REMARK`, `PERSONAL_TRANSFER_PREFIXES` |
| `models.py` | `Transaction`, `Credit`, `CardBalance`, `RewardsBalance`, `Statement`, `ParsedExpenseRow`, `ParsedCreditRow`, `to_cents`, `row_key`, `category_ids` |
| `cells.py` | `parse_id_list` / `format_id_list` / `format_amount`: CSV cell ↔ value |
| `log.py` | `get_logger`; `configure_logging` is called by the CLI only |
| `serialize.py` | **The parsed-CSV format.** Fixed-width rows, sections, card balances, the UNI$ block; read + write |
| `extract/pdf.py` | pdfplumber word geometry → `PageRow` |
| `extract/{chase,paylah,uob}.py` | One issuer's PDF quirks each (UOB also emits its UNI$ rewards row) |
| `extract/__init__.py` | Issuer dispatch, `extract_pdf_to_csv` |
| `categorize/rules.py` | `CatRules` / `CatRule` — cat.csv loading and matching, plus the read-only cat_personal.csv |
| `categorize/brands.py` | Remark normalization, brand matching, literal↔regex escaping |
| `categorize/regroup.py` | cat.csv merging, brands.csv sorting and suggestions |
| `parse/rows.py` | Per-issuer row readers, `parse_cost`, `date_within_statement_year` |
| `parse/files.py` | Extracted CSV → categorized `Statement` |
| `parse/balance.py` | `check_card_totals`: previous − credits + spending = printed total, per card (Chase, UOB, PayLah); UOB's UNI$ previous + earned − used + adjustment = current |
| `parse/__init__.py` | `parse_statement` — the whole pipeline for one statement |
| `reconcile.py` | Manual edits in a parsed CSV → cat.csv rules, then re-parse; `read_parsed_statement`; `check_against_prior` (optional `data/parsed_prior/`) |
| `summary.py` | Aggregation, the category × month table, the per-month combined statements, the healthcare listing |
| `totals.py` | `statement_totals<year>.csv`: spending per statement month × card, guarded against change; `check_statement_balances`, the per-statement balance report `trex summarize` logs |
| `vcs.py` | `commit_paths` (git commit of just the given paths) and `push`; back `--commit` / `--push` on `trex parse` and `trex reconcile`, and `--commit-balanced` on parse |
| `cli.py` | argparse subcommands, each a thin wrapper |

Dependencies run one way: `cli → {reconcile, totals, vcs}`, `reconcile →
parse`, `totals → summary → parse`, and `parse → {extract, categorize,
serialize} → {config, constants, models, cells, log}`. Don't add an edge back
up; that cycle is what the refactor removed.

**Issuer vs card.** An *issuer* is a statement family (Chase, PayLah, UOB) and
picks the extractor and parser. A *card* is the label a row belongs to
(`CARDS_BY_ID`; a PayLah wallet counts as one). A UOB statement holds several cards.

## Invariants

- **No path literals outside `config.py`.** Take an optional `Path` argument
  defaulting to the config value — that's what makes a function testable.
- **No file I/O at import time.** Construct a `CatRules` and call `load()`.
  Rules used to load on import, which made a wrong cwd crash the import.
- **`serialize.py` is the only module that knows the parsed-CSV layout.**
- **`logging`, never `print()`.** The library logs; the CLI configures a handler.
- Public names are snake_case, verb-first, and say the outcome rather than the
  mechanism (`classify_remark`, not `sortRemark`). Docstrings state what is
  returned and any side effect.

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
`data/summary/` hold real personal statements and everything derived from them:
all are gitignored (only the `.gitkeep` placeholders are tracked), and nothing
from them is ever pasted into output. `.gitignore` also ignores every `*.pdf`,
and the pre-commit hook blocks them. For the same reason the project's Claude
settings deny reads of `data/statements/`, `data/extracted/`, `data/parsed/`,
`data/categories/cat.csv` and any `*.pdf`. Work from `tests/fixtures/data`
instead: it's anonymized and holds UOB and PayLah (PLG) samples; Chase and PLY
cases are inline in `tests/test_extract.py` and friends.

Rules naming a person rather than a merchant live in
`data/categories/cat_personal.csv`, which is gitignored too and absent by
default (`CatRules.load` skips it when missing). `CatRules` loads it alongside
cat.csv but never writes to it, so no reconcile or `trex rules regroup` can pull
those names into the file the pipeline rewrites — keep it that way.

`data/categories/cat.csv` **is** committed here, so it must stay merchant-only.
Many merchant rules legitimately carry a name and a PayNow number — a hawker
stall or a fishmonger is a business. The line is who is being paid, not the shape
of the remark: before committing a change to cat.csv, check that every rule with
a person's name or an 8-digit number is a business. Anything else goes in
`cat_personal.csv`.

## Verifying a change to the pipeline

Output format changes are easy to make accidentally. After a change, **ask the
user** whether to run the pipeline on their real data (reading `data/` needs
their permission; see above). `data/parsed/` is not committed here, so copy it
first, then:

```bash
cp -R data/parsed /tmp/parsed-before
ls data/parsed | sed 's/\.csv$//' | xargs uv run trex parse   # every statement
uv run trex summarize --check
diff -r /tmp/parsed-before data/parsed
```

Both commands must exit 0 and the diff must be empty unless the change was
intended. Summarize any differences without pasting transaction rows.
