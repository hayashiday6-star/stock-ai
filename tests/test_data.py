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
