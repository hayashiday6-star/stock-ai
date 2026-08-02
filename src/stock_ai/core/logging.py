"""Logging setup: a Rich console handler plus a rotating file handler.

Call :func:`configure_logging` once at process startup (the CLI does this),
then obtain module loggers with :func:`get_logger`.
"""

from __future__ import annotations

import logging
from logging.config import dictConfig

from stock_ai.config.constants import LOG_DIR, LOG_FILE

_MAX_BYTES = 5_000_000
_BACKUP_COUNT = 3


def configure_logging(level: str = "INFO") -> None:
    """Configure root logging.

    Console output is rendered by Rich; a rotating file handler mirrors logs to
    ``logs/stock_ai.log`` for later inspection.

    Args:
        level: Minimum level name, e.g. ``"DEBUG"`` or ``"INFO"``.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "console": {"format": "%(message)s", "datefmt": "[%X]"},
                "file": {
                    "format": "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
                },
            },
            "handlers": {
                "console": {
                    "class": "rich.logging.RichHandler",
                    "formatter": "console",
                    "rich_tracebacks": True,
                    "show_path": False,
                },
                "file": {
                    "class": "logging.handlers.RotatingFileHandler",
                    "formatter": "file",
                    "filename": str(LOG_FILE),
                    "maxBytes": _MAX_BYTES,
                    "backupCount": _BACKUP_COUNT,
                    "encoding": "utf-8",
                },
            },
            "root": {"level": level, "handlers": ["console", "file"]},
        }
    )


def get_logger(name: str) -> logging.Logger:
    """Return a logger for ``name`` (pass ``__name__`` from the caller)."""
    return logging.getLogger(name)
