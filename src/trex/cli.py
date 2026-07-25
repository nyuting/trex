"""The ``trex`` command line. Each subcommand is a thin wrapper over the library."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from trex import __version__
from trex.categorize import regroup_cat_file, reset_rules, sort_brands_file, suggest_brands
from trex.config import get_data_dir
from trex.extract import extract_all_pdfs, extract_pdf_to_csv
from trex.log import configure_logging, get_logger
from trex.parse import find_statement_pdf, parse_statement
from trex.reconcile import list_pending_updates, reconcile_update, regroup_update
from trex.summary import check_summary_total, find_uncategorized, summarize

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
    parse.set_defaults(run=_run_parse)

    reconcile = subcommands.add_parser("reconcile", help="apply edits from <name>-update.csv")
    reconcile.add_argument("names", nargs="+")
    reconcile.set_defaults(run=_run_reconcile)

    regroup = subcommands.add_parser("regroup", help="re-section <name>.csv from its -update.csv")
    regroup.add_argument("names", nargs="+")
    regroup.set_defaults(run=_run_regroup)

    summarize_cmd = subcommands.add_parser("summarize", help="category x month table for a year")
    summarize_cmd.add_argument("--year", type=int, help="defaults to the current year")
    summarize_cmd.add_argument("--check", action="store_true", help="verify the total afterwards")
    summarize_cmd.add_argument(
        "--fail-on-uncategorized",
        action="store_true",
        help="exit non-zero if any spending is left out of the total",
    )
    summarize_cmd.set_defaults(run=_run_summarize)

    _add_rules_parser(subcommands)
    return parser


def _add_rules_parser(subcommands) -> None:
    """Register the ``trex rules`` group: cat.csv and brands.csv maintenance."""
    rules = subcommands.add_parser("rules", help="maintain the categorization rule files")
    actions = rules.add_subparsers(dest="action", required=True)

    merge = actions.add_parser("regroup", help="merge cat.csv rules for the same merchant")
    merge.set_defaults(run=_run_rules_regroup)

    sort_brands = actions.add_parser("sort-brands", help="sort brands.csv")
    sort_brands.set_defaults(run=_run_rules_sort_brands)

    suggest = actions.add_parser("suggest-brands", help="propose brands.csv additions")
    suggest.add_argument("--min-keys", type=int, default=3)
    suggest.add_argument("--top", type=int, default=30)
    suggest.set_defaults(run=_run_rules_suggest_brands)

    pending = actions.add_parser("pending", help="list statements awaiting reconcile")
    pending.set_defaults(run=_run_rules_pending)


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
    for name in args.names:
        parse_statement(name, year=args.year, verbose=args.show_rows)
    return 0


def _run_reconcile(args: argparse.Namespace) -> int:
    failed = False
    for name in args.names:
        if reconcile_update(name) is None:
            failed = True
    return 1 if failed else 0


def _run_regroup(args: argparse.Namespace) -> int:
    for name in args.names:
        regroup_update(name)
    return 0


def _run_summarize(args: argparse.Namespace) -> int:
    summarize(year=args.year)
    if args.check:
        _, _, difference = check_summary_total(year=args.year)
        if abs(difference) >= 0.01:
            return 1
    if args.fail_on_uncategorized and find_uncategorized(year=args.year):
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


def _run_rules_pending(_args: argparse.Namespace) -> int:
    pending = list_pending_updates()
    if not pending:
        logger.info("nothing pending")
        return 0
    for name in pending:
        logger.info("%s", name)
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


if __name__ == "__main__":
    sys.exit(main())
