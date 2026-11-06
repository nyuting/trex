# trex developer tasks.
#
# The hooks and CI both call these targets, so each gate has exactly one
# definition and "it passed locally" means the same thing everywhere.

.PHONY: help setup hooks fmt lint type test cov check clean

help:  ## Show this help
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  \033[1m%-8s\033[0m %s\n", $$1, $$2}'

setup: hooks  ## Create the venv, install dev deps, activate git hooks
	uv sync --extra dev

hooks:  ## Activate the git hooks only
	chmod +x .githooks/*
	git config core.hooksPath .githooks

fmt:  ## Auto-format and apply safe lint fixes
	uv run ruff format .
	uv run ruff check --fix .

lint:  ## Check formatting and lint (no writes)
	uv run ruff format --check .
	uv run ruff check .

type:  ## Type-check src/
	uv run mypy

test:  ## Run the test suite
	uv run pytest

cov:  ## Run tests with a coverage report
	uv run pytest --cov=trex --cov-report=term-missing

check: lint type test  ## Everything the pre-push hook and CI run

clean:  ## Remove caches and build artefacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache dist build htmlcov .coverage
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
