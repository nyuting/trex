#!/usr/bin/env bash
# Shared helpers for trex git hooks.
#
# Hooks are plain shell scripts activated with:
#     git config core.hooksPath .githooks
# which `make setup` does for you.

set -euo pipefail

if [ -t 1 ]; then
    RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'; BOLD=$'\033[1m'; RESET=$'\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; BOLD=''; RESET=''
fi

say() { printf '%s\n' "${BOLD}[trex]${RESET} $*"; }

# Print an error, the suggested fix, and abort the git operation.
fail() {
    printf '\n%s %s\n' "${RED}${BOLD}✗ blocked:${RESET}" "$1" >&2
    shift
    for line in "$@"; do printf '  %s\n' "$line" >&2; done
    printf '\n  %s\n\n' "${YELLOW}Bypass with --no-verify only if you know CI will still pass.${RESET}" >&2
    exit 1
}

ok() { printf '%s %s\n' "${GREEN}✓${RESET}" "$1"; }

# Every tool runs through `uv run` so the versions match uv.lock rather than
# whatever happens to be on PATH.
require_uv() {
    command -v uv >/dev/null 2>&1 || fail \
        "uv is not installed, so the hooks cannot run the pinned tools." \
        "Install it:  curl -LsSf https://astral.sh/uv/install.sh | sh" \
        "Then run:    make setup"
}

# Staged paths, added/copied/modified only (not deleted), optionally one extension.
staged_files() {
    local ext="${1:-}"
    if [ -n "$ext" ]; then
        git diff --cached --name-only --diff-filter=ACM -z -- "*.${ext}" | tr '\0' '\n' | grep -v '^$' || true
    else
        git diff --cached --name-only --diff-filter=ACM -z | tr '\0' '\n' | grep -v '^$' || true
    fi
}
