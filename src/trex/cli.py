"""The ``trex`` command line. Each subcommand is a thin wrapper over the library."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from trex import __version__
from trex.categorize import (
    RuleStats,
    compute_rule_stats,
    regroup_cat_file,
    reset_rules,
    sort_brands_file,
    suggest_brands,
    write_rule_stats,
)
from trex.cells import format_id_list
from trex.config import (
    get_cat_file,
    get_data_dir,
    get_extracted_dir,
    get_parsed_dir,
    get_summary_dir,
)
from trex.extract import extract_all_pdfs, extract_pdf_to_csv, find_statement_pdf
from trex.healthcare import DEFAULT_WINDOW_DAYS
from trex.log import configure_logging, get_logger
from trex.parse import check_card_totals, check_year_balances, parse_statement
from trex.reconcile import reconcile_in_place
from trex.summary import (
    build_year_summary,
    check_year_summary_total,
    read_dated_expenses,
    read_year_expenses,
    summarize,
    update_statement_totals,
)
from trex.vcs import GitError, commit_paths

logger = get_logger(__name__)


_STATS_SORT_KEYS = {
    "last": lambda stats: (stats.last_seen or datetime.min, stats.rule.pattern.lower()),
    "count": lambda stats: (stats.count, stats.rule.pattern.lower()),
    "pattern": lambda stats: stats.rule.pattern.lower(),
}


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
        action=argparse.BooleanOptionalAction,
        default=True,
        help="re-sum the parsed statements and fail if they differ from the summary total",
    )
    summarize_cmd.add_argument(
        "--fail-on-uncategorized",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="exit non-zero if any spending is left out of the total",
    )
    summarize_cmd.add_argument(
        "--fail-on-multi-category",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="exit non-zero if any row is still split across several categories",
    )
    summarize_cmd.add_argument(
        "--accept-totals",
        action="store_true",
        help="take changed statement totals instead of keeping the recorded ones",
    )
    summarize_cmd.add_argument(
        "--window",
        type=int,
        default=DEFAULT_WINDOW_DAYS,
        help="days after an IHP claim's visit a card charge may be dated and still match it",
    )
    summarize_cmd.add_argument(
        "--commit",
        action="store_true",
        help="if every check passes, git-commit all changed pipeline data: extracted, "
        "parsed and summary CSVs and cat.csv",
    )
    summarize_cmd.add_argument("--push", action="store_true", help="--commit, then git push")
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

    stats = actions.add_parser(
        "stats",
        help="how often, how recently and for how much each rule matches (writes cat_stats.csv)",
    )
    stats.add_argument(
        "--unused", action="store_true", help="only rules no parsed statement matches"
    )
    stats.add_argument(
        "--sort",
        choices=sorted(_STATS_SORT_KEYS),
        default="last",
        help="last: stalest first (default); count: least used first; pattern: alphabetical",
    )
    stats.set_defaults(run=_run_rules_stats)


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
    # --commit and --push need every statement balanced; --commit-balanced takes those that are
    names = balanced if (args.commit_balanced or not unbalanced) else []
    if wants_commit and unbalanced:
        which = f" {', '.join(unbalanced)}" if names else ""
        logger.warning("not committing%s: balance check failed", which)
    if wants_commit and names:
        paths = [
            directory / f"{name}.csv"
            for name in names
            for directory in (get_extracted_dir(), get_parsed_dir())
        ]
        _commit(paths, f"chore(data): parse {', '.join(names)}", push_after=args.push)
    return 1 if unbalanced else 0


def _commit(paths: list[Path], message: str, push_after: bool) -> None:
    """Commit whichever of `paths` and cat.csv changed, then push if asked."""
    commit_paths([*paths, get_cat_file()], message, push=push_after)


def _run_reconcile(args: argparse.Namespace) -> int:
    report = reconcile_in_place(args.names)
    skipped = set(report.skipped) if report is not None else set(args.names)
    done = [name for name in args.names if name not in skipped]
    if done and (args.commit or args.push):
        # the extracted CSV too, in case the statement was parsed without --commit
        paths = [
            directory / f"{name}.csv"
            for name in done
            for directory in (get_extracted_dir(), get_parsed_dir())
        ]
        _commit(paths, f"chore(data): reconcile {', '.join(done)}", push_after=args.push)
    return 0 if len(done) == len(args.names) else 1


def _run_summarize(args: argparse.Namespace) -> int:
    year = args.year or datetime.now().year
    summarize(year=year, window_days=args.window)
    failed = _summary_check_failed(args, year)
    if args.commit or args.push:
        if failed:
            logger.warning("not committing: summary checks failed")
        else:
            # the month's whole flow, so statements parsed or reconciled without --commit too
            paths = [
                path
                for directory in (get_extracted_dir(), get_parsed_dir(), get_summary_dir())
                for path in sorted(directory.glob("*.csv"))
            ]
            _commit(paths, f"chore(data): summarize {year}", push_after=args.push)
    return 1 if failed else 0


def _summary_check_failed(args: argparse.Namespace, year: int) -> bool:
    """Run the checks `args` asks for. Records new statements' totals as a side effect."""
    changes = update_statement_totals(year=year, accept=args.accept_totals)
    unbalanced = check_year_balances(year=year)
    if (changes and not args.accept_totals) or unbalanced:
        return True
    if args.check:
        _, _, difference = check_year_summary_total(year=year)
        if abs(difference) >= 0.01:
            return True
    if args.fail_on_uncategorized or args.fail_on_multi_category:
        built = build_year_summary(year, read_year_expenses(year))
        if args.fail_on_uncategorized and built.uncategorized.rows:
            return True
        if args.fail_on_multi_category and built.multi_category.rows:
            return True
    return False


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


def _run_rules_stats(args: argparse.Namespace) -> int:
    all_stats, unmatched = compute_rule_stats(read_dated_expenses())
    written = write_rule_stats(all_stats)
    shown: list[RuleStats] = sorted(all_stats, key=_STATS_SORT_KEYS[args.sort])
    if args.unused:
        shown = [stats for stats in shown if stats.count == 0]
    logger.info("count  last seen       mean     stdev  category  pattern")
    for stats in shown:
        last_seen = stats.last_seen.strftime("%Y-%m-%d") if stats.last_seen else "never"
        logger.info(
            "%5d  %-10s  %9.2f  %8.2f  %-8s  %s",
            stats.count,
            last_seen,
            stats.mean,
            stats.stdev,
            format_id_list(stats.rule.category_ids),
            stats.rule.pattern,
        )
    logger.info(
        "%d of %d rules shown; %d rows matched no rule", len(shown), len(all_stats), unmatched
    )
    logger.info("wrote %s", written)
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
    except GitError as error:
        logger.error("git %s failed:\n%s", error.command, error.output)
        return 1


if __name__ == "__main__":
    sys.exit(main())
