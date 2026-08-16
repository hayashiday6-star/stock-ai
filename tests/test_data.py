"""Tests for the data layer: OHLCV normalization and the yfinance provider.

No network access — a fake downloader is injected into the provider.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from stock_ai.core.exceptions import DataError
from stock_ai.data.schema import ADJ_CLOSE, DATE, OHLCV_COLUMNS, VOLUME, normalize_ohlcv
from stock_ai.data.yfinance_provider import YFinancePriceProvider

START = date(2024, 1, 1)
END = date(2024, 1, 5)


def _raw_yf_frame() -> pd.DataFrame:
    """Build a raw yfinance-style frame: unsorted dates, 'Adj Close', tz-aware."""
    idx = pd.to_datetime(["2024-01-03", "2024-01-02"]).tz_localize("America/New_York")
    return pd.DataFrame(
        {
            "Open": [102.0, 100.0],
            "High": [103.0, 101.0],
            "Low": [101.0, 99.0],
            "Close": [102.5, 100.5],
            "Adj Close": [102.5, 100.5],
            "Volume": [2_000, 1_000],
        },
        index=idx,
    )


def _provider_returning(frame: pd.DataFrame) -> YFinancePriceProvider:
    return YFinancePriceProvider(downloader=lambda _s, _st, _e: frame)


def test_normalize_produces_canonical_schema() -> None:
    result = normalize_ohlcv(_raw_yf_frame())
    assert list(result.columns) == OHLCV_COLUMNS
    assert result.index.name == DATE
    assert result.index.tz is None
    assert result.index.is_monotonic_increasing
    assert result[VOLUME].dtype == "int64"


def test_normalize_handles_multiindex_columns() -> None:
    raw = _raw_yf_frame()
    raw.columns = pd.MultiIndex.from_product([raw.columns, ["AAPL"]])
    result = normalize_ohlcv(raw)
    assert list(result.columns) == OHLCV_COLUMNS
    assert ADJ_CLOSE in result.columns


def test_normalize_missing_columns_raises() -> None:
    bad = pd.DataFrame({"Open": [1.0]}, index=pd.to_datetime(["2024-01-02"]))
    with pytest.raises(DataError):
        normalize_ohlcv(bad)


def test_fetch_prices_returns_sorted_bars() -> None:
    provider = _provider_returning(_raw_yf_frame())
    result = provider.fetch_prices("AAPL", START, END)
    assert len(result) == 2
    assert result.index[0] < result.index[1]


def test_fetch_prices_empty_raises() -> None:
    provider = _provider_returning(pd.DataFrame())
    with pytest.raises(DataError):
        provider.fetch_prices("AAPL", START, END)


def test_fetch_prices_rejects_reversed_range() -> None:
    provider = _provider_returning(_raw_yf_frame())
    with pytest.raises(DataError):
        provider.fetch_prices("AAPL", END, START)


# --- 429 must be distinguishable from every other failure -------------------


def test_a_429_becomes_a_rate_limit_error_with_the_requested_wait() -> None:
    """The bulk ingester's pacing depends on telling 429 from a bad symbol."""
    from stock_ai.core.exceptions import RateLimitError
    from stock_ai.data.http import raise_for_status

    class _Response:
        status_code = 429
        headers = {"Retry-After": "30"}

    with pytest.raises(RateLimitError) as excinfo:
        raise_for_status(_Response(), "statements for 7203")
    assert excinfo.value.retry_after == 30.0


def test_a_429_without_a_retry_after_still_carries_a_wait() -> None:
    """A backoff of None would be read as "retry immediately"."""
    from stock_ai.core.exceptions import RateLimitError
    from stock_ai.data.http import DEFAULT_RETRY_AFTER, raise_for_status

    class _Response:
        status_code = 429
        headers: dict[str, str] = {}

    with pytest.raises(RateLimitError) as excinfo:
        raise_for_status(_Response(), "x")
    assert excinfo.value.retry_after == DEFAULT_RETRY_AFTER


def test_an_http_date_retry_after_falls_back_rather_than_crashing() -> None:
    from stock_ai.core.exceptions import RateLimitError
    from stock_ai.data.http import DEFAULT_RETRY_AFTER, raise_for_status

    class _Response:
        status_code = 429
        headers = {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}

    with pytest.raises(RateLimitError) as excinfo:
        raise_for_status(_Response(), "x")
    assert excinfo.value.retry_after == DEFAULT_RETRY_AFTER


def test_other_errors_stay_ordinary_data_errors() -> None:
    """A 404 is one symbol's problem; treating it as a rate limit would stall."""
    from stock_ai.core.exceptions import DataError, RateLimitError
    from stock_ai.data.http import raise_for_status

    class _Response:
        status_code = 404
        headers: dict[str, str] = {}

    with pytest.raises(DataError) as excinfo:
        raise_for_status(_Response(), "x")
    assert not isinstance(excinfo.value, RateLimitError)


def test_a_successful_response_raises_nothing() -> None:
    from stock_ai.data.http import raise_for_status

    class _Response:
        status_code = 200
        headers: dict[str, str] = {}

    raise_for_status(_Response(), "x")


# --- error bodies -----------------------------------------------------------


class _Failing:
    """A minimal response object carrying a status and a body."""

    def __init__(self, status_code: int, text: str = "", headers: dict | None = None) -> None:
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


def test_an_error_carries_the_providers_own_explanation() -> None:
    """A status code is a category; the reason is only ever in the body.

    Observed live: J-Quants answered a 13-year price range with ``400 Bad
    Request`` and the message said only "HTTP 400". Whether the window was too
    wide, the start predated the plan, or a parameter was wrong is exactly what
    the body would have said, and it was being thrown away - the same discard
    that turned a 403 into two rounds of guessing at "plan limits".
    """
    from stock_ai.core.exceptions import DataError
    from stock_ai.data.http import raise_for_status

    response = _Failing(400, '{"message":"The specified period is too long."}')
    with pytest.raises(DataError) as excinfo:
        raise_for_status(response, "prices for 7203")

    assert "400" in str(excinfo.value)
    assert "period is too long" in str(excinfo.value)


def test_an_error_body_never_leaks_a_key() -> None:
    """Error bodies quote the request back, and some providers key the URL."""
    from stock_ai.core.exceptions import DataError
    from stock_ai.core.logging import register_secret
    from stock_ai.data.http import raise_for_status

    register_secret("super-secret-key-value")
    response = _Failing(400, "Bad request for Subscription-Key=super-secret-key-value")

    with pytest.raises(DataError) as excinfo:
        raise_for_status(response, "prices for 7203")

    assert "super-secret-key-value" not in str(excinfo.value)
    assert "<redacted>" in str(excinfo.value)


def test_a_bodyless_error_still_reports_its_status() -> None:
    from stock_ai.core.exceptions import DataError
    from stock_ai.data.http import raise_for_status

    with pytest.raises(DataError, match="HTTP 500"):
        raise_for_status(_Failing(500, ""), "prices for 7203")


def test_a_long_error_body_is_truncated() -> None:
    from stock_ai.core.exceptions import DataError
    from stock_ai.data.http import raise_for_status

    with pytest.raises(DataError) as excinfo:
        raise_for_status(_Failing(400, "x" * 5000), "prices for 7203")

    assert len(str(excinfo.value)) < 500
    assert "..." in str(excinfo.value)


def test_a_rate_limit_is_still_typed_separately() -> None:
    """The 429 path must keep its own type, body or no body."""
    from stock_ai.core.exceptions import RateLimitError
    from stock_ai.data.http import raise_for_status

    with pytest.raises(RateLimitError) as excinfo:
        raise_for_status(_Failing(429, "slow down", {"Retry-After": "12"}), "prices for 7203")

    assert excinfo.value.retry_after == 12.0
