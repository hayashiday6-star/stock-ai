"""J-Quants (Japanese equities) implementation of :class:`PriceProvider`.

The HTTP call is isolated behind an injectable ``fetcher`` so the provider is
unit-testable without the network. The default fetcher targets the J-Quants V2
API (``x-api-key`` auth); its exact endpoint/params should be checked against the
current J-Quants docs before live use - the tested contract here is the
normalization and provider logic, exercised via an injected fetcher.

Note: J-Quants subscriptions are a *rolling* 5-year window, so always pass a
dynamically computed date range - a hard-coded start date will eventually 400.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import re
from collections.abc import Callable
from typing import Any

import pandas as pd
from pydantic import SecretStr

from stock_ai.core.exceptions import DataError, RateLimitError
from stock_ai.core.logging import get_logger
from stock_ai.data.http import raise_for_status
from stock_ai.data.schema import (
    ADJ_CLOSE,
    CLOSE,
    DATE,
    HIGH,
    LOW,
    OHLCV_COLUMNS,
    OPEN,
    VOLUME,
)

logger = get_logger(__name__)

# A fetcher takes (symbol, start, end) and returns raw daily-quote records.
JQuantsFetcher = Callable[[str, dt.date, dt.date], list[dict[str, Any]]]

# V2 uses short field names (O/H/L/C/Vo) with Adj* for split-adjusted values.
# The long V1-style names are kept as fallbacks so either shape normalizes.
_FIELD_MAP: dict[str, str] = {
    "Date": DATE,
    "O": OPEN,
    "H": HIGH,
    "L": LOW,
    "C": CLOSE,
    "AdjC": ADJ_CLOSE,
    "Vo": VOLUME,
    # Fallbacks (V1-style / verbose payloads)
    "Open": OPEN,
    "High": HIGH,
    "Low": LOW,
    "Close": CLOSE,
    "AdjustmentClose": ADJ_CLOSE,
    "Volume": VOLUME,
}

_DAILY_QUOTES_URL = "https://api.jquants.com/v2/equities/bars/daily"


def normalize_jquants(records: list[dict[str, Any]]) -> pd.DataFrame:
    """Normalize raw J-Quants daily-quote records into the canonical OHLCV schema.

    Args:
        records: A list of daily-quote dicts (J-Quants field names).

    Returns:
        A canonical OHLCV DataFrame (see :mod:`stock_ai.data.schema`).

    Raises:
        DataError: If records are empty or required fields are missing.
    """
    if not records:
        raise DataError("J-Quants returned no records.")

    df = pd.DataFrame(records)
    # Rename only the fields present, so duplicate targets can't collide.
    df = df.rename(columns={k: v for k, v in _FIELD_MAP.items() if k in df.columns})
    df = df.loc[:, ~df.columns.duplicated()]
    if ADJ_CLOSE not in df.columns and CLOSE in df.columns:
        df[ADJ_CLOSE] = df[CLOSE]

    missing = [col for col in (DATE, *OHLCV_COLUMNS) if col not in df.columns]
    if missing:
        raise DataError(f"J-Quants records missing fields: {missing}")

    df = df[[DATE, *OHLCV_COLUMNS]].copy()
    df[DATE] = pd.to_datetime(df[DATE])
    df = df.dropna(subset=[OPEN, HIGH, LOW, CLOSE]).sort_values(DATE)
    if df.empty:
        raise DataError("J-Quants records contain no valid rows after cleaning.")
    df[VOLUME] = df[VOLUME].fillna(0).astype("int64")
    return df.set_index(DATE)


#: J-Quants rejects an out-of-plan range with a 400 that names the range it
#: *would* serve, e.g. ``Your subscription covers the following dates:
#: 2021-08-16 ~ .`` The open right-hand side means "up to today".
_SUBSCRIPTION_WINDOW = re.compile(
    r"subscription covers the following dates:\s*(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})?"
)


def subscription_window(message: str) -> tuple[dt.date, dt.date | None] | None:
    """Return the date range a plan covers, if the error message names one.

    The provider answers an over-wide request by stating exactly what it would
    have served. Reading that back is the difference between "the whole symbol
    failed" and "we now know the plan starts on 2021-08-16" - and it needs no
    configuration, because the answer arrives with the refusal.
    """
    match = _SUBSCRIPTION_WINDOW.search(message)
    if match is None:
        return None
    try:
        start = dt.date.fromisoformat(match.group(1))
    except ValueError:  # pragma: no cover - the regex already fixes the shape
        return None
    end: dt.date | None = None
    if match.group(2):
        with contextlib.suppress(ValueError):
            end = dt.date.fromisoformat(match.group(2))
    return start, end


def _default_fetcher(api_key: SecretStr | None) -> JQuantsFetcher:
    """Build a fetcher that calls the J-Quants V2 daily-quotes endpoint."""

    def request(symbol: str, start: dt.date, end: dt.date) -> list[dict[str, Any]]:
        import httpx

        headers = {"x-api-key": api_key.get_secret_value()} if api_key else {}
        params = {"code": symbol, "from": start.isoformat(), "to": end.isoformat()}
        records: list[dict[str, Any]] = []
        pagination_key: str | None = None
        with httpx.Client(timeout=30.0) as client:
            while True:
                query = dict(params)
                if pagination_key:
                    query["pagination_key"] = pagination_key
                response = client.get(_DAILY_QUOTES_URL, headers=headers, params=query)
                raise_for_status(response, f"prices for {symbol}")
                payload = response.json()
                # V2 returns {"data": [...]}; older shapes used "daily_quotes".
                records.extend(payload.get("data") or payload.get("daily_quotes") or [])
                pagination_key = payload.get("pagination_key")
                if not pagination_key:
                    break
        return records

    # The plan's covered range, once the provider has told us what it is. It is
    # a property of the subscription, not of a symbol, so learning it on the
    # first refusal and applying it to the rest is the whole point: without
    # this, every symbol in a 1,564-name backfill pays its own rejected request
    # before the useful one. That doubles the request count on exactly the run
    # that is already the heaviest, and the rate limiter aborts it partway - on
    # the first real attempt only 38 symbols were extended before the run ended.
    learned: dict[str, tuple[dt.date, dt.date | None]] = {}

    def clamp(start: dt.date, end: dt.date) -> tuple[dt.date, dt.date]:
        """Narrow a range to the plan's window, if we have been told one."""
        window = learned.get("window")
        if window is None:
            return start, end
        covered_start, covered_end = window
        return max(start, covered_start), (min(end, covered_end) if covered_end else end)

    def fetch(symbol: str, start: dt.date, end: dt.date) -> list[dict[str, Any]]:
        """Fetch a range, narrowing it to what the subscription actually covers.

        Asking for more history than the plan covers fails the whole symbol,
        which is wrong twice over: the years that *are* covered were available
        all along, and a universe-wide backfill fails every name for the same
        reason. The refusal states the covered range, so it is taken, applied,
        and remembered for the remaining symbols.
        """
        start, end = clamp(start, end)
        if start >= end:
            return []  # the plan's window and the wanted window do not overlap
        try:
            return request(symbol, start, end)
        except RateLimitError:
            raise
        except DataError as exc:
            window = subscription_window(str(exc))
            if window is None:
                raise
            learned["window"] = window
            covered_start, _covered_end = window
            narrowed_start, narrowed_end = clamp(start, end)
            if narrowed_start >= narrowed_end or (narrowed_start, narrowed_end) == (start, end):
                raise
            logger.warning(
                "The J-Quants plan covers %s onward, not %s. Fetching the covered "
                "part for %s and every symbol after it; earlier history needs a "
                "different plan.",
                covered_start,
                start,
                symbol,
            )
            return request(symbol, narrowed_start, narrowed_end)

    return fetch


class JQuantsPriceProvider:
    """Fetch Japanese equity OHLCV bars via the J-Quants API."""

    name = "jquants"

    def __init__(
        self, api_key: SecretStr | None = None, fetcher: JQuantsFetcher | None = None
    ) -> None:
        """Create the provider.

        Args:
            api_key: J-Quants V2 API key (``x-api-key``).
            fetcher: Callable performing the raw fetch; injected in tests.
        """
        self._fetch = fetcher or _default_fetcher(api_key)

    def fetch_prices(self, symbol: str, start: dt.date, end: dt.date) -> pd.DataFrame:
        """Fetch and normalize daily bars for ``symbol`` over ``[start, end]``."""
        if start > end:
            raise DataError(f"start ({start}) must not be after end ({end}).")

        logger.debug("Fetching J-Quants prices for %s: %s..%s", symbol, start, end)
        records = self._fetch(symbol, start, end)
        prices = normalize_jquants(records)
        logger.info("Fetched %d bars for %s (J-Quants)", len(prices), symbol)
        return prices
