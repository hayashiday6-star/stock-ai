"""Shared HTTP error handling for the data providers.

One helper, for one reason: a 429 has to be distinguishable from every other
failure by the time it reaches the caller. ``httpx.raise_for_status()`` throws
the same ``HTTPStatusError`` for a rate limit as for a bad symbol, and the bulk
ingester's whole pacing strategy depends on telling those apart.
"""

from __future__ import annotations

from typing import Any

from stock_ai.core.exceptions import DataError, RateLimitError

#: Fallback wait when the provider rate-limits without saying for how long.
DEFAULT_RETRY_AFTER = 60.0


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
    raise DataError(f"HTTP {status} while fetching {context}.")
