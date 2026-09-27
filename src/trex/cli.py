"""The ``trex`` command line. Each subcommand is a thin wrapper over the library."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from trex import __version__
from trex.categorize import regroup_cat_file, reset_rules, sort_brands_file, suggest_brands
from trex.config import get_cat_file, get_data_dir, get_extracted_dir, get_parsed_dir
from trex.extract import extract_all_pdfs, extract_pdf_to_csv
from trex.log import configure_logging, get_logger
from trex.parse import check_card_totals, find_statement_pdf, parse_statement
from trex.reconcile import reconcile_in_place
from trex.summary import (
    check_summary_total,
    find_multi_category,
    find_uncategorized,
    summarize,
)
from trex.totals import check_statement_balances, update_statement_totals
from trex.vcs import commit_paths, push

logger = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for the whole CLI."""
    parser = argparse.ArgumentParser(
        prog="trex",
        description="Turn credit-card PDF statements into categorized monthly summaries.",
    )
    parser.add_argument("--version", action="version", version=f"trex {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="log debug detail")
    subcommands = parser.add_subparsers(dest="command", required=True)

    extract = subcommands.add_parser("extract", help="PDF statements -> data/extracted")
    extract.add_argument("names", nargs="*", help="statement short names (default: every PDF)")
    extract.set_defaults(run=_run_extract)

    parse = subcommands.add_parser("parse", help="extract, categorize and write a statement")
    parse.add_argument("names", nargs="+", help="statement short names, e.g. UOB05")
    parse.add_argument("--year", type=int, help="year to assume if the statement is undated")
    parse.add_argument("--show-rows", action="store_true", help="log every transaction parsed")
    _add_commit_flags(parse)
    parse.add_argument(
        "--commit-balanced",
        action="store_true",
        help="--commit, but only the statements that pass the balance check",
    )
    parse.set_defaults(run=_run_parse)

    reconcile = subcommands.add_parser(
        "reconcile",
        help="turn category fixes in a parsed statement into cat.csv rules, then re-parse it",
    )
    reconcile.add_argument("names", nargs="+", help="statement short names, e.g. UOB05")
    _add_commit_flags(reconcile)
    reconcile.set_defaults(run=_run_reconcile)

    summarize_cmd = subcommands.add_parser(
        "summarize",
        help="category x month table for a year; also balance-checks every statement",
    )
    summarize_cmd.add_argument("--year", type=int, help="defaults to the current year")
    summarize_cmd.add_argument(
        "--check",
        action="store_true",
        help="re-sum the parsed statements and fail if they differ from the summary total",
    )
    summarize_cmd.add_argument(
        "--fail-on-uncategorized",
        action="store_true",
        help="exit non-zero if any spending is left out of the total",
    )
    summarize_cmd.add_argument(
        "--fail-on-multi-category",
        action="store_true",
        help="exit non-zero if any row is still split across several categories",
    )
    summarize_cmd.add_argument(
        "--accept-totals",
        action="store_true",
        help="take changed statement totals instead of keeping the recorded ones",
    )
    summarize_cmd.set_defaults(run=_run_summarize)

    _add_rules_parser(subcommands)
    return parser


def _add_commit_flags(subcommand: argparse.ArgumentParser) -> None:
    """Add --commit and --push, which record what the subcommand wrote in git."""
    subcommand.add_argument(
        "--commit", action="store_true", help="git-commit the CSVs and cat.csv this run wrote"
    )
    subcommand.add_argument("--push", action="store_true", help="--commit, then git push")


def _add_rules_parser(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register the ``trex rules`` group: cat.csv and brands.csv maintenance."""
    rules = subcommands.add_parser("rules", help="maintain the categorization rule files")
    actions = rules.add_subparsers(dest="action", required=True)

    merge = actions.add_parser("regroup", help="merge cat.csv rules for the same merchant")
    merge.set_defaults(run=_run_rules_regroup)

    sort_brands = actions.add_parser("sort-brands", help="sort brands.csv")
    sort_brands.set_defaults(run=_run_rules_sort_brands)

    suggest = actions.add_parser("suggest-brands", help="propose brands.csv additions")
    suggest.add_argument(
        "--min-keys",
        type=int,
        default=3,
        help="only tokens shared by at least this many cat.csv remark groups",
    )
    suggest.add_argument("--top", type=int, default=30, help="show at most this many candidates")
    suggest.set_defaults(run=_run_rules_suggest_brands)


# --- subcommand implementations -----------------------------------------


def _run_extract(args: argparse.Namespace) -> int:
    if not args.names:
        written = extract_all_pdfs()
        logger.info("extracted %d statements", len(written))
        return 0
    for name in args.names:
        extract_pdf_to_csv(find_statement_pdf(name))
    return 0


def _run_parse(args: argparse.Namespace) -> int:
    balanced, unbalanced = [], []
    for name in args.names:
        statement = parse_statement(name, year=args.year, verbose=args.show_rows)
        if check_card_totals(statement):
            unbalanced.append(statement.name)
        else:
            balanced.append(statement.name)
    wants_commit = args.commit or args.push or args.commit_balanced
    partial = args.commit_balanced and bool(balanced)
    if unbalanced and wants_commit:
        if partial:
            logger.warning("not committing %s: balance check failed", ", ".join(unbalanced))
        else:
            logger.warning("not committing: balance check failed")
    names = balanced if (partial or not unbalanced) else []
    if wants_commit and names:
        paths = [
            directory / f"{name}.csv"
            for name in names
            for directory in (get_extracted_dir(), get_parsed_dir())
        ]
        _commit(paths, f"chore(data): parse {', '.join(names)}", push_after=args.push)
    return 1 if unbalanced else 0


def _run_reconcile(args: argparse.Namespace) -> int:
    report = reconcile_in_place(args.names)
    skipped = set(report.skipped) if report is not None else set(args.names)
    done = [name for name in args.names if name not in skipped]
    if done and (args.commit or args.push):
        paths = [get_parsed_dir() / f"{name}.csv" for name in done]
        _commit(paths, f"chore(data): reconcile {', '.join(done)}", push_after=args.push)
    return 0 if len(done) == len(args.names) else 1


def _commit(paths: list[Path], message: str, push_after: bool) -> None:
    """Commit whichever of `paths` and cat.csv changed, then push if asked."""
    committed = commit_paths([*paths, get_cat_file()], message)
    if committed and push_after:
        push()


def _run_summarize(args: argparse.Namespace) -> int:
    summarize(year=args.year)
    changes = update_statement_totals(year=args.year, accept=args.accept_totals)
    unbalanced = check_statement_balances(year=args.year)
    if (changes and not args.accept_totals) or unbalanced:
        return 1
    if args.check:
        _, _, difference = check_summary_total(year=args.year)
        if abs(difference) >= 0.01:
            return 1
    if args.fail_on_uncategorized and find_uncategorized(year=args.year):
        return 1
    if args.fail_on_multi_category and find_multi_category(year=args.year):
        return 1
    return 0


def _run_rules_regroup(_args: argparse.Namespace) -> int:
    regroup_cat_file()
    reset_rules()
    return 0


def _run_rules_sort_brands(_args: argparse.Namespace) -> int:
    sort_brands_file()
    return 0


def _run_rules_suggest_brands(args: argparse.Namespace) -> int:
    suggestions = suggest_brands(min_keys=args.min_keys, top=args.top)
    if not suggestions:
        logger.info("no brand candidates")
        return 0
    logger.info("brand candidates (distinct-keys  token  e.g.):")
    for count, token, example in suggestions:
        logger.info("  %3d  %-24s  %s", count, token, example)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and run the requested subcommand. Returns the exit code."""
    args = build_parser().parse_args(argv)
    configure_logging(verbose=args.verbose)
    logger.debug("data directory: %s", get_data_dir())
    try:
        return args.run(args)
    except (FileNotFoundError, ValueError) as error:
        logger.error("error: %s", error)
        return 1
    except subprocess.CalledProcessError as error:
        output = "\n".join(part.strip() for part in (error.stdout, error.stderr) if part)
        logger.error("git %s failed:\n%s", error.cmd[1], output)
        return 1


if __name__ == "__main__":
    sys.exit(main())
