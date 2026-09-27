"""Committing pipeline output to git. Called by the CLI only; the library never commits."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

from trex.config import get_data_dir
from trex.log import get_logger

logger = get_logger(__name__)


def commit_paths(paths: Sequence[Path], message: str, cwd: Path | None = None) -> bool:
    """Commit whichever of `paths` changed, and nothing else. Returns True if committed.

    Anything already staged outside `paths` stays staged and out of the commit.
    Git runs in `cwd` (default: the data directory), so it finds that tree's
    repository. Hooks run as usual; a failing hook or git command raises
    ``subprocess.CalledProcessError``.
    """
    cwd = cwd or get_data_dir()
    changed = _changed_paths(paths, cwd)
    if not changed:
        logger.info("nothing to commit")
        return False

    specs = [str(path) for path in changed]
    _git(["add", "--", *specs], cwd)
    _git(["commit", "--quiet", "--only", "-m", message, "--", *specs], cwd)
    logger.info("committed %d files: %s", len(changed), message)
    return True


def push(cwd: Path | None = None) -> None:
    """Push the current branch to its upstream. A failing push or hook raises
    ``subprocess.CalledProcessError``."""
    _git(["push", "--quiet"], cwd or get_data_dir())
    logger.info("pushed")


def _changed_paths(paths: Sequence[Path], cwd: Path) -> list[Path]:
    """Return the existing `paths` that are untracked or differ from HEAD."""
    existing = [path for path in paths if path.exists()]
    if not existing:
        return []
    status = _git(
        ["status", "--porcelain", "--untracked-files=all", "--", *map(str, existing)], cwd
    )
    if not status.strip():
        return []
    root = Path(_git(["rev-parse", "--show-toplevel"], cwd).strip()).resolve()
    dirty = {root / line[3:] for line in status.splitlines()}
    return [path for path in existing if path.resolve() in dirty]


def _git(args: list[str], cwd: Path) -> str:
    """Run git with `args` in `cwd` and return its stdout."""
    result = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return result.stdout
