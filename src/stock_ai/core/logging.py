"""Logging setup: a Rich console handler plus a rotating file handler.

Call :func:`configure_logging` once at process startup (the CLI does this),
then obtain module loggers with :func:`get_logger`.

Every record passes through :class:`SecretRedactingFilter` on its way out. That
is not defence in depth, it is the only defence: this project's own code is
careful with secrets, but the HTTP client underneath it logs the URL it fetched,
and an API that takes its key as a *query parameter* puts that key in the URL.
EDINET does exactly that, and the verification script tells people to paste its
output when asking for help.
"""

from __future__ import annotations

import logging
import re
from logging.config import dictConfig
from typing import Any

from stock_ai.config.constants import LOG_DIR, LOG_FILE

_MAX_BYTES = 5_000_000
_BACKUP_COUNT = 3

#: Query parameters whose value is a credential. Matched case-insensitively
#: against the part before ``=``; the value up to the next separator is dropped.
_SECRET_PARAMS = (
    "subscription-key",
    "ocp-apim-subscription-key",
    "api_key",
    "apikey",
    "x-api-key",
    "access_token",
    "refresh_token",
    "id_token",
    "token",
    "key",
    "password",
    "secret",
)

_SECRET_QUERY = re.compile(
    r"(?i)\b(" + "|".join(re.escape(name) for name in _SECRET_PARAMS) + r")=[^&\s\"'<>]+"
)

#: Literal secret values registered at runtime, redacted wherever they appear.
#: Query-parameter matching cannot catch a key echoed in prose or in a header
#: dump, and a value we were handed is the one string we can always recognise.
_KNOWN_SECRETS: set[str] = set()

#: Below this length a "secret" is more likely to be a common substring, and
#: redacting it would corrupt unrelated messages.
_MIN_REDACTABLE_LENGTH = 8


def register_secret(value: str | None) -> None:
    """Register a literal secret so it is stripped from every future log line."""
    if value and len(value) >= _MIN_REDACTABLE_LENGTH:
        _KNOWN_SECRETS.add(value)


def redact(text: str) -> str:
    """Return ``text`` with credentials replaced by ``<redacted>``."""
    cleaned = _SECRET_QUERY.sub(lambda m: f"{m.group(1)}=<redacted>", text)
    for secret in _KNOWN_SECRETS:
        cleaned = cleaned.replace(secret, "<redacted>")
    return cleaned


class SecretRedactingFilter(logging.Filter):
    """Strip credentials out of a record before any handler formats it.

    The redaction is applied to the *formatted* message and the arguments are
    dropped, because rewriting ``record.msg`` alone would leave a secret sitting
    in ``record.args`` for the formatter to substitute back in.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Rewrite the record in place; never drops a record."""
        try:
            message = record.getMessage()
        except Exception:  # a broken format string must not lose the record
            return True
        cleaned = redact(message)
        if cleaned != message:
            record.msg = cleaned
            record.args = None
        return True


def configure_logging(level: str = "INFO") -> None:
    """Configure root logging.

    Console output is rendered by Rich; a rotating file handler mirrors logs to
    ``logs/stock_ai.log`` for later inspection. Both are filtered for secrets.

    Args:
        level: Minimum level name, e.g. ``"DEBUG"`` or ``"INFO"``.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler: dict[str, Any] = {"filters": ["redact"]}
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {"redact": {"()": SecretRedactingFilter}},
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
                    **handler,
                },
                "file": {
                    "class": "logging.handlers.RotatingFileHandler",
                    "formatter": "file",
                    "filename": str(LOG_FILE),
                    "maxBytes": _MAX_BYTES,
                    "backupCount": _BACKUP_COUNT,
                    "encoding": "utf-8",
                    **handler,
                },
            },
            "root": {"level": level, "handlers": ["console", "file"]},
        }
    )


def get_logger(name: str) -> logging.Logger:
    """Return a logger for ``name`` (pass ``__name__`` from the caller)."""
    return logging.getLogger(name)
