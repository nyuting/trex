---
name: review
description: Review a diff, branch or commit range in trex against the project's standards and report ranked findings. Use when asked to review changes, a branch, or a PR in this repo.
---

# Reviewing a change in trex

The standards come from CLAUDE.md and the `write-code` skill. This skill
covers how to apply them to a diff.

## Process
1. Scope: `git diff main...HEAD --stat` (or the range you were given), plus
   `git log main..HEAD --format='%h %s%n%b'` for the stated intent.
2. Read the diff only for `src/`, `tests/`, docs and config:
   `git diff main...HEAD -- src tests '*.md' .githooks Makefile pyproject.toml`.
   Never diff, `cat` or read paths under `data/`. CLAUDE.md "Data and privacy"
   explains why. For data changes, `--stat` is all you look at.
3. Read the surrounding code of each changed function, including its callers,
   before judging it. Most findings here are about the boundaries between
   modules, not single lines.
4. Run `make check`. Don't report anything it already catches (ruff, mypy,
   `RANKS`, module docstrings).
5. Report findings most severe first, each with
   `file:line`, the problem, a concrete failure scenario (which input makes
   which output wrong), and a suggested fix. If nothing survives, say so.

## Checklist, by severity
1. **Silent data loss.** Rows skipped, a regex miss that `continue`s,
   `except: pass`, dates parsed without a leap year, an empty result where
   there should be an error. Also check that every log message and warning
   matches what the code does.
2. **Unintended output change.** A refactor that changes CSV columns,
   ordering, formatting or rounding. Any behavior change must be named in the
   commit body.
3. **Privacy.** New code or tests that read real `data/` paths, print rows,
   commit statements or notebooks with outputs, or a path literal outside
   `config.py`.
4. **Ownership.** A second reader or writer for one format, a module in the
   wrong stage, a caller relying on another module's internals, or a library
   exception leaking past its module.
5. **Names.** A name that misstates the outcome, a verb inconsistent with
   `read_`/`write_`/`parse_`/`update_`, or a function whose job changed but whose
   name didn't.
6. **Dead weight.** Helpers, flags or wrappers with no remaining caller, a
   conversion out of a shape and back, index constants where a header key would do,
   or a test duplicating another's code path.
7. **Layout and docs.** Function order against CLAUDE.md "Module layout", a
   docstring restating the name, or missing docs for an edge case or side effect.
8. **Commits.** Not single-purpose, a body that doesn't say why, or an unlisted
   behavior change.

Weigh severity by consequence: a data-loss path outranks any number of naming
issues. Flag only problems you can name a failure for. Leave out style
preferences you can't tie to a rule above.
