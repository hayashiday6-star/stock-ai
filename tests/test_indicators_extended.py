"""Tests for ATR, ADX, Stochastic, OBV, and the indicator framework."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stock_ai.data.schema import CLOSE, HIGH, LOW, OPEN, VOLUME
from stock_ai.technical.base import ADX, MACD, RSI, SMA, Stochastic, apply
from stock_ai.technical.indicators import (
    adx,
    atr,
    on_balance_volume,
    stochastic,
    volume_sma,
)


def _ohlc(rows: list[tuple[float, float, float, float]], volume: int = 1000) -> pd.DataFrame:
    """Build an OHLCV frame from (open, high, low, close) tuples."""
    idx = pd.date_range("2024-01-01", periods=len(rows), freq="D")
    data = {
        OPEN: [r[0] for r in rows],
        HIGH: [r[1] for r in rows],
        LOW: [r[2] for r in rows],
        CLOSE: [r[3] for r in rows],
        "adj_close": [r[3] for r in rows],
        VOLUME: [volume] * len(rows),
    }
    return pd.DataFrame(data, index=idx)


def _uptrend(n: int = 30) -> pd.DataFrame:
    rows = [(i, i + 1.0, i - 1.0, i + 0.5) for i in range(1, n + 1)]
    return _ohlc(rows)


def test_atr_constant_range_equals_range() -> None:
    # Every bar has high-low = 2 and no gaps -> True Range == 2 -> ATR -> 2.
    rows = [(10, 11, 9, 10) for _ in range(20)]
    result = atr(_ohlc(rows), window=5)
    assert result.iloc[-1] == pytest.approx(2.0, abs=1e-6)


def test_stochastic_at_new_high_is_100() -> None:
    # Close equals the high and rises every bar, so %K pins to 100.
    rows = [(i, i + 1.0, i - 1.0, i + 1.0) for i in range(1, 21)]
    result = stochastic(_ohlc(rows), k=5, d=3)
    assert result["stoch_k"].iloc[-1] == pytest.approx(100.0, abs=1e-6)


def test_stochastic_bounded() -> None:
    rows = [(5, 7, 3, 5), (6, 8, 4, 7), (5, 6, 2, 3), (7, 9, 5, 8), (4, 5, 1, 2)] * 4
    result = stochastic(_ohlc(rows), k=5, d=3).dropna()
    assert (result["stoch_k"] >= -1e-9).all()
    assert (result["stoch_k"] <= 100 + 1e-9).all()


def test_adx_positive_di_dominates_in_uptrend() -> None:
    result = ADX(window=5).compute(_uptrend(40)).dropna()
    last = result.iloc[-1]
    assert last["plus_di"] > last["minus_di"]
    assert 0 <= last["adx"] <= 100


def test_obv_rises_on_up_closes() -> None:
    frame = _uptrend(10)
    obv = on_balance_volume(frame)
    # Every close is higher than the prior -> OBV strictly increases.
    assert obv.is_monotonic_increasing
    assert obv.iloc[-1] == pytest.approx(1000 * 9)


def test_volume_sma() -> None:
    result = volume_sma(_uptrend(10), window=3)
    assert result.iloc[-1] == pytest.approx(1000.0)


def test_apply_concatenates_named_columns() -> None:
    frame = _uptrend(40)
    table = apply(frame, [SMA(3), RSI(3), MACD()])
    assert "sma_3" in table.columns
    assert "rsi_3" in table.columns
    assert {"macd", "signal", "histogram"}.issubset(table.columns)
    assert len(table) == len(frame)


def test_apply_with_prices_prepends_ohlcv() -> None:
    frame = _uptrend(10)
    table = apply(frame, [SMA(3)], with_prices=True)
    assert CLOSE in table.columns
    assert "sma_3" in table.columns


def test_apply_bollinger_and_stochastic_prefixes() -> None:
    frame = _uptrend(40)
    table = apply(frame, [Stochastic(k=5)])
    assert {"stoch_k", "stoch_d"}.issubset(table.columns)
    assert not np.isnan(table["stoch_k"].iloc[-1])


# --- flat markets must not produce NaN -------------------------------------


def _flat(periods: int = 20, price: float = 100.0) -> pd.DataFrame:
    """A halted / limit-locked stock: every OHLC value identical."""
    idx = pd.date_range("2024-01-01", periods=periods, freq="D", name="date")
    return pd.DataFrame(
        {
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "adj_close": price,
            "volume": 0,
        },
        index=idx,
    )


def test_stochastic_on_a_zero_width_range_is_mid_band() -> None:
    """A flat window gives 0/0; %K is 50, not NaN."""
    result = stochastic(_flat(), k=5, d=3)
    assert result["stoch_k"].iloc[-1] == pytest.approx(50.0)
    assert result["stoch_d"].iloc[-1] == pytest.approx(50.0)


def test_adx_on_a_flat_series_is_zero_not_nan() -> None:
    """No directional movement means DI/ADX of 0, not 0/0."""
    last = adx(_flat(), window=5).iloc[-1]
    assert last["plus_di"] == pytest.approx(0.0)
    assert last["minus_di"] == pytest.approx(0.0)
    assert last["adx"] == pytest.approx(0.0)
