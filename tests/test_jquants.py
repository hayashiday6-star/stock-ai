"""Tests for the J-Quants price provider (injected fetcher, no network)."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from stock_ai.core.exceptions import DataError
from stock_ai.data.jquants_provider import JQuantsPriceProvider, normalize_jquants
from stock_ai.data.schema import ADJ_CLOSE, DATE, OHLCV_COLUMNS, VOLUME

START = dt.date(2024, 1, 1)
END = dt.date(2024, 1, 31)


def _records() -> list[dict[str, Any]]:
    # Unsorted, with an AdjustmentClose that differs from Close.
    return [
        {
            "Date": "2024-01-05",
            "Open": 2100,
            "High": 2150,
            "Low": 2080,
            "Close": 2120,
            "AdjustmentClose": 1060,
            "Volume": 500000,
        },
        {
            "Date": "2024-01-04",
            "Open": 2050,
            "High": 2110,
            "Low": 2040,
            "Close": 2100,
            "AdjustmentClose": 1050,
            "Volume": 400000,
        },
    ]


def test_normalize_produces_canonical_schema() -> None:
    result = normalize_jquants(_records())
    assert list(result.columns) == OHLCV_COLUMNS
    assert result.index.name == DATE
    assert result.index.is_monotonic_increasing
    assert result[VOLUME].dtype == "int64"
    assert result[ADJ_CLOSE].iloc[0] == 1050  # AdjustmentClose used, not Close


def test_normalize_defaults_adj_close_to_close() -> None:
    records = [{"Date": "2024-01-04", "Open": 10, "High": 11, "Low": 9, "Close": 10, "Volume": 1}]
    result = normalize_jquants(records)
    assert result[ADJ_CLOSE].iloc[0] == 10


def test_normalize_v2_short_field_names() -> None:
    """Real V2 payloads use O/H/L/C/Vo with AdjC for the adjusted close."""
    records = [
        {
            "Date": "2026-06-01",
            "Code": "72030",
            "O": 3006.0,
            "H": 3009.0,
            "L": 2891.0,
            "C": 2905.5,
            "AdjC": 2905.5,
            "Vo": 37687200.0,
            "AdjFactor": 1.0,
        }
    ]
    result = normalize_jquants(records)
    assert list(result.columns) == OHLCV_COLUMNS
    assert result["open"].iloc[0] == 3006.0
    assert result[ADJ_CLOSE].iloc[0] == 2905.5
    assert result[VOLUME].iloc[0] == 37687200


def test_normalize_empty_raises() -> None:
    with pytest.raises(DataError):
        normalize_jquants([])


def test_normalize_missing_fields_raises() -> None:
    with pytest.raises(DataError):
        normalize_jquants([{"Date": "2024-01-04", "Open": 10}])


def test_provider_fetches_and_normalizes() -> None:
    calls: list[tuple[str, dt.date, dt.date]] = []

    def fetcher(symbol: str, start: dt.date, end: dt.date) -> list[dict[str, Any]]:
        calls.append((symbol, start, end))
        return _records()

    provider = JQuantsPriceProvider(fetcher=fetcher)
    result = provider.fetch_prices("7203", START, END)
    assert len(result) == 2
    assert calls == [("7203", START, END)]


def test_provider_rejects_reversed_range() -> None:
    provider = JQuantsPriceProvider(fetcher=lambda *_: _records())
    with pytest.raises(DataError):
        provider.fetch_prices("7203", END, START)


def test_provider_empty_raises() -> None:
    provider = JQuantsPriceProvider(fetcher=lambda *_: [])
    with pytest.raises(DataError):
        provider.fetch_prices("7203", START, END)
