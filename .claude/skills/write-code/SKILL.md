---
name: write-code
description: How to write or refactor code in trex so it matches the project's standards. Use before adding a feature, fixing a bug, or restructuring modules in src/trex or tests.
---

# Writing code in trex

Read CLAUDE.md first. It holds the hard rules (paths in `config.py`, no I/O at
import time, `logging` not `print()`, the module layout, the privacy rules),
and `make check` enforces several of them. This skill adds the judgment calls
the refactor history settled on. Each one cites the commit that set it.

## Naming
- Name the outcome or the file produced, not the mechanism:
  `build_year_summary` / `write_year_summary`, `healthcare_listing_name` (5e78746).
- Keep verbs consistent. `read_*` / `write_*` do file I/O only, `parse_*` takes
  text (`parse_row_date`, `parse_cost`), and `update_*` reads, rewrites and may
  delete (`update_healthcare_listing`, `update_statement_totals`).
- When a function's job changes, rename it in the same change. `push` became
  `push_branch` once `commit_paths` took `push=True` (d6ee832).

## Ownership and placement
- Each concern has one owner. Each CSV format gets a single reader and writer
  (9abaf12). If two modules each open the same file, make one of them call the other.
- Code lives in the stage it serves, not wherever its helper happens to be.
  "It sat there only to borrow X" is a reason to move it (2dfe58c, c0d35b6).
- Don't leak internals across a module boundary. Raise domain errors
  (`GitError`), not the library's own (`CalledProcessError`) (d6ee832).
- Once modules form one feature, make them a package and re-export the names
  callers used (facc0db, 50472df). Rank new modules in `tests/test_conventions.py`.

## Correctness
- Fail loudly and never drop data silently. An unreadable row, a missing header
  or an unmapped card must raise or fail a check (d542717, 1977945). Ask yourself
  which input would make this code skip a row without anyone noticing.
- Watch dates. Parsing day/month without a year uses 1900, so 29 Feb rows are
  lost. Go through `parse_row_date` (652ed4b).
- A log message must describe what the code does. If it warns "dropping X", it
  must actually drop X (22391d4).
- A refactor leaves output byte-identical. Any behavior change is deliberate and
  listed in the commit body.
- Compute derived values on demand rather than storing them in tracked files
  (8340598).

## Simplicity
- Delete what has no callers left: wrappers, flags, single-use helpers
  (0ae2f26, 5e78746).
- Don't convert data out of a shape and back again. Pass the shape the consumer
  needs (1977945).
- Key rows by header name, not index constants (132c1a2). Use `NamedTuple` or a
  dataclass rather than a bare tuple, and `DictWriter` for CSVs.
- Give one structure one meaning. Don't encode a second meaning under a
  sentinel key such as a `""` ref (22391d4).

## Layout and docs
- Follow CLAUDE.md "Module layout": pipeline modules read top-down, toolkit
  modules group their public API first.
- Constants go up with the constants, not beside their first use (ac54182).
- A docstring says only what the signature can't: a rule, an edge case, a side
  effect, or why. If it would restate the name, leave it out.
- If a rule can be checked, write a test for it (as `RANKS` does) rather than
  more prose.

## Tests
- One test per code path. Before adding a test, search for an existing one that
  covers the same path (53545b3).
- Name magic numbers, and pin outside inputs such as exchange rates.
- Put each test in the file for the module it exercises. Helpers only tests use
  live in `tests/` (3d81fe5, 3af0cab).
- Tests never touch the real repo or `data/`. Use `tmp_path`, the fixtures in
  `conftest.py` and `tests/fixtures/data` (e0f09dd).

## Before you finish
1. `make check` passes.
2. If the pipeline changed, ask the user before running the real-data
   verification in CLAUDE.md. `data/` must stay unchanged.
3. Commit small and single-purpose. The subject is a Conventional Commit; the
   body is prose saying why, what changed for callers, and any behavior change.
   Ask before committing.
