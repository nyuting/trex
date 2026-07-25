# CLAUDE.md

Guide for working in this repo. Read this before grepping — the module map below
is meant to take you straight to the right file.

## What it is

`trex` turns credit-card PDF statements into categorized monthly summaries.
Installed package on a src layout; `pip install -e ".[dev]"`.

Pipeline: `data/statements/*.pdf` → `data/extracted/*.csv` → `data/parsed/*.csv`
→ `data/parsed/summary<year>.csv`.

## Commands

```bash
pytest                       # 178 tests; needs no statement PDFs
ruff check --fix . && ruff format .
trex --help
```

Use a Python 3.10+ interpreter for anything ad hoc — an older system `python3` is
below `requires-python` and will fail to import the package.

## Module map

| File | Owns |
| --- | --- |
| `config.py` | **Every path.** Repo-root or `TREX_DATA_DIR` resolution, `EXCHANGE_RATE_USD2SGD` |
| `constants.py` | `CATEGORIES`, `SOURCES_BY_ID`, UOB card headers, issuer names |
| `models.py` | `Transaction`, `Credit`, `Statement`, `ParsedExpenseRow` |
| `text.py` | `parse_num_list` / `format_num_list` for comma-separated id cells |
| `log.py` | `get_logger`; `configure_logging` is called by the CLI only |
| `serialize.py` | **The parsed-CSV format.** Fixed-width rows, sections, read + write |
| `extract/pdf.py` | pdfplumber word geometry → `PageRow` |
| `extract/{chase,paylah,uob}.py` | One issuer's PDF quirks each |
| `extract/__init__.py` | Issuer dispatch, `extract_pdf_to_csv` |
| `categorize/rules.py` | `CatRules` / `CatRule` — cat.csv loading and matching, plus the read-only cat_personal.csv |
| `categorize/brands.py` | Remark normalization, brand matching, literal↔regex escaping |
| `categorize/regroup.py` | cat.csv merging, brands.csv sorting and suggestions |
| `parse/rows.py` | Per-issuer row readers, `parse_cost`, `adjust_year` |
| `parse/files.py` | Extracted CSV → categorized `Statement` |
| `parse/__init__.py` | `parse_statement` — the whole pipeline for one statement |
| `reconcile.py` | The `-update.csv` workflow, `read_parsed_statement` |
| `summary.py` | Aggregation and the category × month table |
| `cli.py` | argparse subcommands, each a thin wrapper |

Dependencies run one way: `cli → parse → {extract, categorize, serialize} →
{config, constants, models, text, log}`. Don't add an edge back up; that cycle
is what the refactor removed.

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

**Add a card**: add its label to `SOURCES_BY_ID`; for UOB, add its statement
header to `UOB_CARD_HEADERS`.

**Add an issuer**: new module in `extract/` returning CSV lines, register it in
`EXTRACTORS`; add a row reader in `parse/rows.py`, a `_parse_*` in
`parse/files.py`, and a branch in `detect_issuer`.

**Change a categorization**: prefer the `-update.csv` reconcile workflow (see
README) over editing `cat.csv` by hand — it keeps the rule and the output in
step.

## Data and privacy

This is a public repo. `data/statements/`, `data/extracted/` and `data/parsed/`
hold real personal statements and everything derived from them: all three are
gitignored, only their `.gitkeep` is tracked, and nothing from them is ever
pasted into output. `.claude/settings.json` denies reads of `data/` for the same
reason — work from `tests/fixtures/data`, which is anonymized and mirrors every
format.

Rules naming a person rather than a merchant live in
`data/categories/cat_personal.csv`, which is gitignored too and absent by default
(`CatRules.load` skips it when missing). `CatRules` loads it alongside cat.csv but
never writes to it, so no reconcile or `trex rules regroup` can pull those names
into the file the pipeline rewrites — keep it that way.

`data/categories/cat.csv` **is** committed here, so it must stay merchant-only.
Many merchant rules legitimately carry a name and a PayNow number — a hawker
stall or a fishmonger is a business. The line is who is being paid, not the shape
of the remark: before committing a change to cat.csv, check that every rule with
a person's name or an 8-digit number is a business. Anything else goes in
`cat_personal.csv`.

## Verifying a change to the pipeline

Output format changes are easy to make accidentally. To confirm you haven't:
regenerate `data/parsed/` and diff it against a copy taken before the change —
it must be byte-identical unless the change was intended. (`data/parsed/` is not
committed here, so keep the copy yourself.)
