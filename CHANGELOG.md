# Changelog

All notable changes to `trex`. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/) (0.x: minor versions may break).

## [0.2.0] – 2026-10-30

Reviewed line-by-line by Yuting.

Everything since the 0.1.0 scaffold: the scripts became the `trex` package and
CLI, every issuer gained a balance check, and summaries gained monthly files,
recorded statement totals and a healthcare listing matched to IHP claims.

### Added

- `trex` CLI (`extract`, `parse`, `reconcile`, `summarize`, `rules`) and a
  public package API; `trex --version`.
- PDF extraction with pdfplumber.
- Balance checks against printed totals: Chase account summary, PayLah, UOB
  per card, and UOB UNI$ rewards.
- `parse --commit` and `--commit-balanced`; `reconcile --in-place`;
  `summarize --commit` and `--push`.
- Summaries written to `data/summary/`, with a combined file per month.
- `statement_totals<year>.csv`: each statement's total is recorded once, and
  `summarize` exits 1 if it later changes (`--accept-totals` to take it).
- `healthcare<year>.csv` listing, each charge marked with the IHP claim covering
  it; claim matches are settled once and kept. `not_claimable.csv` and
  `not_medical.csv` exclude merchants from it.
- `cat_personal.csv`: rules naming people, loaded but never rewritten.
- `trex rules stats`, writing an untracked `cat_stats.csv`.
- `summarize` runs its checks by default and flags uncategorized,
  multi-category and excluded spending.

### Changed

- Restructured into a src-layout package with `extract`, `categorize`,
  `parse`, `summary` and `healthcare` subpackages; imports run one way.
- Renamed the `source` column to `card`; `issuer` now names the statement family.
- Each CSV format has a single reader and writer.
- Modules follow one layout convention (pipeline modules top-down).
- Tooling: uv with a lockfile, a Makefile, mypy, and `.githooks` in place of
  pre-commit (Conventional Commits, blocks PDFs, statements and notebook
  outputs). CI runs on Python 3.10 and 3.12.

### Removed

- **Breaking:** the `-update.csv` reconcile workflow; edit the parsed CSV and
  run `trex reconcile` instead.
- The `regroup` CLI alias, the pandas and tabula dependencies, and the legacy
  scripts.

### Fixed

- Chase amounts over 1,000, leading-dot amounts and credits.
- 29 February dates, in both parse and summary.
- PDFs with unrecognised names are skipped instead of aborting extraction.
- An unreadable UOB UNI$ row is flagged instead of dropped.
- Two merge faults in `trex rules regroup`.
- Pre-push tests no longer rewrite the real `.git/config`.
- Re-parsing a statement leaves its manual categorizations unchanged.

### Tests

- Full suite on anonymized fixtures, plus checks of import direction and
  module docstrings; duplicate tests pruned.

## [0.1.0] – 2026-07-25

- Package scaffold.
