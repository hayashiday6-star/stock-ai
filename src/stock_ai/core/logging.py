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


#: コンソールに出す価値の無いロガー。**ファイルには残す。**
#:
#: `httpx` はリクエストごとに URL を1行出す。一括ダウンロードでは署名付き
#: URL が丸ごと出て、1本で400字を超える。人が読む役には立たないうえ、
#: **その出力は「そのまま貼ってください」と言われて貼られる。**
#:
#: 落とすのはコンソールだけで、ファイルには残る。何が起きたかを後から
#: 追う手段は減らさない。
_QUIET_ON_CONSOLE = ("httpx", "httpcore", "urllib3")


class ConsoleNoiseFilter(logging.Filter):
    """Keep per-request chatter out of the console, not out of the log file.

    The console is what a person reads and what they paste when asking for
    help. One line per HTTP request is never the answer to a question, and a
    presigned URL is 400 characters of signature. The rotating file handler
    still receives everything, so nothing is lost for diagnosis.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Drop sub-warning records from the noisy libraries."""
        if record.levelno >= logging.WARNING:
            return True
        return not record.name.startswith(_QUIET_ON_CONSOLE)


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

    **The console additionally drops per-request chatter** from ``httpx`` and
    friends, because that output gets pasted into conversations and one line per
    HTTP request is never what the question was about. Pass ``DEBUG`` to see it.
    The log file keeps it either way.

    Args:
        level: Minimum level name, e.g. ``"DEBUG"`` or ``"INFO"``.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler: dict[str, Any] = {"filters": ["redact"]}
    # コンソールだけ、さらに絞る。ファイルは絞らない。
    console_filters = ["redact"] if level.upper() == "DEBUG" else ["redact", "quiet"]
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {
                "redact": {"()": SecretRedactingFilter},
                "quiet": {"()": ConsoleNoiseFilter},
            },
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
                    "filters": console_filters,
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
