"""Tests for core technical indicators against hand-checked values."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stock_ai.core.exceptions import DataError
from stock_ai.data.schema import CLOSE, OHLCV_COLUMNS
from stock_ai.technical.indicators import (
    bollinger_bands,
    ema,
    macd,
    rsi,
    sma,
)


def _prices(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    frame = pd.DataFrame(
        dict.fromkeys(OHLCV_COLUMNS, closes),
        index=idx,
    )
    frame[CLOSE] = closes
    return frame


def test_sma_matches_manual() -> None:
    result = sma(_prices([1, 2, 3, 4, 5]), window=3)
    assert np.isnan(result.iloc[1])
    assert result.iloc[2] == pytest.approx(2.0)
    assert result.iloc[4] == pytest.approx(4.0)


def test_ema_first_value_equals_first_close() -> None:
    result = ema(_prices([10, 20, 30]), span=2)
    # adjust=False seeds the EMA with the first observation.
    assert result.iloc[0] == pytest.approx(10.0)
    # span=2 -> alpha=2/3; second value = 10 + 2/3*(20-10).
    assert result.iloc[1] == pytest.approx(10 + (2 / 3) * 10)


def test_rsi_all_gains_is_100() -> None:
    result = rsi(_prices([1, 2, 3, 4, 5, 6, 7, 8]), window=3)
    assert result.iloc[-1] == pytest.approx(100.0)


def test_rsi_bounded_between_0_and_100() -> None:
    closes = [5, 4, 6, 3, 7, 2, 8, 1, 9, 5, 6, 4, 7, 3, 8]
    result = rsi(_prices(closes), window=5).dropna()
    assert (result >= 0).all()
    assert (result <= 100).all()


def test_macd_histogram_is_macd_minus_signal() -> None:
    result = macd(_prices(list(range(1, 40))))
    assert list(result.columns) == ["macd", "signal", "histogram"]
    diff = result["macd"] - result["signal"]
    pd.testing.assert_series_equal(result["histogram"], diff, check_names=False)


def test_bollinger_constant_series_has_zero_width() -> None:
    result = bollinger_bands(_prices([5.0] * 25), window=20)
    last = result.iloc[-1]
    assert last["upper"] == pytest.approx(last["lower"])
    assert last["middle"] == pytest.approx(5.0)


def test_bollinger_band_width_matches_std() -> None:
    closes = [float(i) for i in range(1, 30)]
    result = bollinger_bands(_prices(closes), window=20, num_std=2.0)
    last = result.iloc[-1]
    assert last["upper"] - last["lower"] == pytest.approx(
        4.0 * (last["upper"] - last["middle"]) / 2.0
    )


def test_missing_close_column_raises() -> None:
    frame = pd.DataFrame({"open": [1, 2, 3]})
    with pytest.raises(DataError):
        sma(frame)


def test_rsi_on_a_flat_series_is_neutral_not_nan() -> None:
    """With no gains and no losses RSI is 50 by convention, not 0/0."""
    result = rsi(_prices([100.0] * 20), window=14)
    assert result.iloc[-1] == pytest.approx(50.0)
    assert pd.isna(result.iloc[0])  # the leading diff() NaN is preserved
