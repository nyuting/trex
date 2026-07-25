"""Logging setup. The library only ever logs; the CLI decides where output goes."""

from __future__ import annotations

import logging

LOGGER_NAME = "trex"


def get_logger(name: str | None = None) -> logging.Logger:
    """Return the trex logger, or a named child of it (pass ``__name__``)."""
    if name and name != LOGGER_NAME:
        return logging.getLogger(LOGGER_NAME).getChild(name.removeprefix("trex."))
    return logging.getLogger(LOGGER_NAME)


def configure_logging(verbose: bool = False) -> None:
    """Attach a plain stderr handler to the trex logger. Called by the CLI only.

    Any handler from a previous call is replaced, so repeated calls pick up the
    current verbosity and the current ``sys.stderr``.
    """
    logger = get_logger()
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    for existing in list(logger.handlers):
        logger.removeHandler(existing)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
