"""Shared HTTP error handling for the data providers.

One helper, for one reason: a 429 has to be distinguishable from every other
failure by the time it reaches the caller. ``httpx.raise_for_status()`` throws
the same ``HTTPStatusError`` for a rate limit as for a bad symbol, and the bulk
ingester's whole pacing strategy depends on telling those apart.
"""

from __future__ import annotations

from typing import Any

from stock_ai.core.exceptions import DataError, RateLimitError
from stock_ai.core.logging import redact

#: Fallback wait when the provider rate-limits without saying for how long.
DEFAULT_RETRY_AFTER = 60.0

#: How much of an error body to keep. Enough for a sentence of explanation,
#: short enough to sit in a failure table without swamping it.
_MAX_BODY_CHARS = 300


def _explanation(response: Any) -> str:
    """Return the provider's own account of the failure, if it gave one.

    Status codes are a category, not a reason. A 400 from J-Quants means the
    request was rejected and nothing more - whether the range was too wide, the
    start date predates the plan, or a parameter was misspelled is only in the
    body. Discarding it is what turned a 403 into two rounds of guessing at
    "plan limits" when the body said the endpoint did not exist.

    The text is redacted before it is returned: an error body can quote back
    the request, and for a provider that carries its key in the query string
    that would put the key in an exception message.
    """
    body = getattr(response, "text", None)
    if not isinstance(body, str):
        return ""
    cleaned = " ".join(redact(body).split())
    if not cleaned:
        return ""
    if len(cleaned) > _MAX_BODY_CHARS:
        cleaned = f"{cleaned[:_MAX_BODY_CHARS]}..."
    return f" Provider said: {cleaned}"


def _retry_after(response: Any) -> float | None:
    """Parse the ``Retry-After`` header, in seconds, if it is usable."""
    raw = None
    headers = getattr(response, "headers", None)
    if headers is not None:
        raw = headers.get("Retry-After") or headers.get("retry-after")
    if raw is None:
        return None
    try:
        seconds = float(str(raw).strip())
    except ValueError:
        return None  # the HTTP-date form; not worth parsing for a backoff
    return seconds if seconds >= 0 else None


def raise_for_status(response: Any, context: str) -> None:
    """Raise a typed error for a failed response, leaving 2xx alone.

    Args:
        response: The HTTP response.
        context: What was being fetched, for the message (e.g. ``"7203"``).

    Raises:
        RateLimitError: On HTTP 429, carrying the provider's requested wait.
        DataError: On any other 4xx or 5xx.
    """
    status = getattr(response, "status_code", 200)
    if status < 400:
        return
    if status == 429:
        raise RateLimitError(
            f"Rate limited by the provider while fetching {context}.",
            retry_after=_retry_after(response) or DEFAULT_RETRY_AFTER,
        )
    raise DataError(f"HTTP {status} while fetching {context}.{_explanation(response)}")
