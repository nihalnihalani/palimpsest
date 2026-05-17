"""Centralized structured logging. Use logs.get_logger(__name__) in every module."""
from __future__ import annotations
import logging
import os
import sys
from typing import Any

try:
    from rich.logging import RichHandler  # type: ignore
    _RICH = True
except ImportError:
    _RICH = False

_INITIALIZED = False


def _level_from_env() -> int:
    # WIKI_LOG_LEVEL is independent of LOG_LEVEL (which we lower to WARNING
    # in config.py to silence cognee's structlog noise) — that way our own
    # wiki.* loggers stay informative even when cognee is quiet.
    raw = (os.environ.get("WIKI_LOG_LEVEL") or "INFO").upper()
    return getattr(logging, raw, logging.INFO)


def _init() -> None:
    global _INITIALIZED
    if _INITIALIZED:
        return
    root = logging.getLogger("wiki")
    root.setLevel(_level_from_env())
    root.propagate = False
    if not root.handlers:
        if _RICH:
            handler = RichHandler(rich_tracebacks=True, show_time=True,
                                  show_path=False, markup=False)
        else:
            handler = logging.StreamHandler(sys.stderr)
            handler.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)-5s %(name)s | %(message)s",
                datefmt="%H:%M:%S"))
        root.addHandler(handler)
    _INITIALIZED = True


def get_logger(name: str) -> logging.Logger:
    _init()
    short = name.replace("palimpsest.", "wiki.")
    return logging.getLogger(short)


def event(logger: logging.Logger, event_name: str, **fields: Any) -> None:
    """Log a structured event with key=value fields. Use for important
    business events (ingest_claim, supersedes_write, etc.)."""
    parts = [f"{k}={v!r}" for k, v in fields.items()]
    logger.info(f"{event_name}  {' '.join(parts)}")
