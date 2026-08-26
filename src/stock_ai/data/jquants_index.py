"""J-Quants TOPIX daily bars (``/indices/bars/daily/topix``).

A minimal sibling to :mod:`stock_ai.data.jquants_provider`: same V2 auth and
pagination shape, but the index has no per-symbol code, no volume, and no
split-adjusted close (TOPIX is not a tradable instrument, so there is nothing
to adjust). Kept separate rather than folded into the equities provider
because the response shape genuinely differs - forcing it through
:func:`~stock_ai.data.jquants_provider.normalize_jquants` would mean padding
in a fake volume column just to satisfy a schema TOPIX does not have.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from time import sleep
from typing import Any

import pandas as pd
from pydantic import SecretStr

from stock_ai.core.exceptions import DataError, NoDataError, RateLimitError
from stock_ai.core.logging import get_logger
from stock_ai.data.http import DEFAULT_RETRY_AFTER, raise_for_status
from stock_ai.data.jquants_provider import subscription_window
from stock_ai.data.schema import CLOSE, DATE, HIGH, LOW, OPEN

logger = get_logger(__name__)

#: How many times a rate-limited index request is retried before giving up.
_RATE_LIMIT_RETRIES = 4

# A fetcher takes (start, end) and returns raw TOPIX daily-bar records.
TopixFetcher = Callable[[dt.date, dt.date], list[dict[str, Any]]]

_TOPIX_URL = "https://api.jquants.com/v2/indices/bars/daily/topix"

_FIELD_MAP: dict[str, str] = {
    "Date": DATE,
    "O": OPEN,
    "H": HIGH,
    "L": LOW,
    "C": CLOSE,
}


def normalize_topix(records: list[dict[str, Any]]) -> pd.DataFrame:
    """Normalize raw TOPIX daily-bar records into a date-indexed OHLC frame.

    Returns:
        A frame indexed by a sorted, timezone-naive ``DatetimeIndex`` named
        ``"date"``, with columns ``open, high, low, close``.

    Raises:
        NoDataError: If the response carried no records.
        DataError: If required fields are missing.
    """
    if not records:
        raise NoDataError("J-Quants returned no TOPIX records.")

    df = pd.DataFrame(records)
    df = df.rename(columns={k: v for k, v in _FIELD_MAP.items() if k in df.columns})
    df = df.loc[:, ~df.columns.duplicated()]

    missing = [col for col in (DATE, OPEN, HIGH, LOW, CLOSE) if col not in df.columns]
    if missing:
        raise DataError(f"J-Quants TOPIX records missing fields: {missing}")

    df = df[[DATE, OPEN, HIGH, LOW, CLOSE]].copy()
    df[DATE] = pd.to_datetime(df[DATE])
    df = df.dropna(subset=[CLOSE]).sort_values(DATE)
    if df.empty:
        raise DataError("J-Quants TOPIX records contain no valid rows after cleaning.")
    df = df.set_index(DATE)
    return df[~df.index.duplicated(keep="last")]


def _default_fetcher(api_key: SecretStr | None) -> TopixFetcher:
    """Build a fetcher that calls the J-Quants V2 TOPIX daily-bars endpoint.

    A range wider than the subscription is refused outright, and the refusal
    names the range the plan *would* serve. Taking that answer and asking
    again is the difference between "the index is unavailable" and "the index
    starts in 2021" - and it needs no configuration, because the answer
    arrives with the refusal.

    The price provider already does this per symbol. The index is one
    request rather than a universe, so there is nothing to remember between
    calls: narrow once, retry once.
    """

    def request(start: dt.date, end: dt.date) -> list[dict[str, Any]]:
        import httpx

        headers = {"x-api-key": api_key.get_secret_value()} if api_key else {}
        params = {"from": start.isoformat(), "to": end.isoformat()}
        records: list[dict[str, Any]] = []
        pagination_key: str | None = None
        with httpx.Client(timeout=30.0) as client:
            while True:
                query = dict(params)
                if pagination_key:
                    query["pagination_key"] = pagination_key
                response = client.get(_TOPIX_URL, headers=headers, params=query)
                raise_for_status(response, "TOPIX bars")
                payload = response.json()
                records.extend(payload.get("data") or [])
                pagination_key = payload.get("pagination_key")
                if not pagination_key:
                    break
        return records

    def attempt(start: dt.date, end: dt.date) -> list[dict[str, Any]]:
        """One request, waiting out a rate limit rather than failing on it.

        Narrowing the window on a 429 would answer a question nobody asked -
        the pace is the problem, not the range - but giving up on the first
        refusal is worse. This is a single request for one index, so there is
        no partial universe to truncate and nothing to lose by waiting: the
        only outcomes are the bars or an honest failure after every attempt.

        The index is usually fetched right after a heavy backfill has spent
        the quota, which is exactly when one refusal is least informative.
        """
        for attempt_number in range(_RATE_LIMIT_RETRIES + 1):
            try:
                return request(start, end)
            except RateLimitError as exc:
                if attempt_number == _RATE_LIMIT_RETRIES:
                    raise
                wait = (exc.retry_after or DEFAULT_RETRY_AFTER) * (2**attempt_number)
                logger.warning(
                    "Rate limited fetching TOPIX; waiting %.0fs (attempt %d/%d).",
                    wait,
                    attempt_number + 1,
                    _RATE_LIMIT_RETRIES,
                )
                sleep(wait)
        raise AssertionError("unreachable")  # pragma: no cover

    def fetch(start: dt.date, end: dt.date) -> list[dict[str, Any]]:
        try:
            return attempt(start, end)
        except RateLimitError:
            # Already retried to exhaustion above, and a 429 never names a
            # subscription window - there is nothing left to narrow.
            raise
        except DataError as exc:
            window = subscription_window(str(exc))
            if window is None:
                raise
            covered_start, covered_end = window
            narrowed_start = max(start, covered_start)
            narrowed_end = min(end, covered_end) if covered_end else end
            if narrowed_start >= narrowed_end or (narrowed_start, narrowed_end) == (start, end):
                raise
            logger.warning(
                "The J-Quants plan covers TOPIX from %s, not %s. Fetching %s to %s "
                "instead; any study built on this index starts there, whatever "
                "history the price store holds.",
                covered_start,
                start,
                narrowed_start,
                narrowed_end,
            )
            return attempt(narrowed_start, narrowed_end)

    return fetch


def fetch_topix(
    start: dt.date,
    end: dt.date,
    api_key: SecretStr | None = None,
    fetcher: TopixFetcher | None = None,
) -> pd.DataFrame:
    """Fetch and normalize TOPIX daily bars over ``[start, end]``.

    Args:
        start: First calendar date to include.
        end: Last calendar date to include.
        api_key: J-Quants V2 API key; ignored when ``fetcher`` is given.
        fetcher: Callable performing the raw fetch; injected in tests.
    """
    if start > end:
        raise DataError(f"start ({start}) must not be after end ({end}).")
    fetch = fetcher or _default_fetcher(api_key)
    records = fetch(start, end)
    bars = normalize_topix(records)
    logger.info("Fetched %d TOPIX bar(s)", len(bars))
    return bars
