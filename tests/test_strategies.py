"""Tests for the additional strategies and the strategy factory."""

from __future__ import annotations

import pandas as pd
import pytest

from stock_ai.backtest.strategy import (
    AboveSMA,
    BuyAndHold,
    MACDCross,
    RSIReversion,
    SMACrossover,
    build_strategy,
)
from stock_ai.data.schema import ADJ_CLOSE, CLOSE, HIGH, LOW, OPEN, VOLUME


def _prices(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="D", name="date")
    return pd.DataFrame(
        {
            OPEN: closes,
            HIGH: [c + 1 for c in closes],
            LOW: [c - 1 for c in closes],
            CLOSE: closes,
            ADJ_CLOSE: closes,
            VOLUME: [1000] * len(closes),
        },
        index=idx,
    )


def test_above_sma_long_when_above() -> None:
    closes = [float(i) for i in range(1, 30)]  # steadily rising
    signal = AboveSMA(window=5).generate_signals(_prices(closes))
    assert signal.dtype == bool
    assert signal.iloc[-1]  # last close is above its SMA


def test_macd_cross_boolean() -> None:
    closes = [float(i) for i in range(1, 60)]
    signal = MACDCross().generate_signals(_prices(closes))
    assert signal.dtype == bool
    assert len(signal) == len(closes)


def test_rsi_reversion_is_stateful() -> None:
    # Deep dip then a strong recovery: enter on the dip, exit after recovery.
    closes = [50, 48, 46, 40, 30, 25, 24, 26, 30, 40, 55, 70, 80, 85, 88] + [90] * 10
    signal = RSIReversion(period=3, buy_below=35, exit_above=60).generate_signals(
        _prices([float(c) for c in closes])
    )
    assert signal.dtype == bool
    # Somewhere it should have been long (entered on the dip).
    assert signal.any()
    # After the strong recovery it should be flat again.
    assert not bool(signal.iloc[-1])


def test_rsi_reversion_rejects_bad_thresholds() -> None:
    with pytest.raises(ValueError, match="below"):
        RSIReversion(buy_below=60, exit_above=40)


def test_build_strategy_names() -> None:
    assert isinstance(build_strategy("hold"), BuyAndHold)
    assert isinstance(build_strategy("sma", fast=10, slow=20), SMACrossover)
    assert isinstance(build_strategy("sma200", slow=200), AboveSMA)
    assert isinstance(build_strategy("macd"), MACDCross)
    assert isinstance(build_strategy("rsi"), RSIReversion)


def test_build_strategy_unknown_raises() -> None:
    with pytest.raises(ValueError, match="Unknown strategy"):
        build_strategy("bogus")


def test_sma200_uses_two_hundred_days_not_the_crossover_slow_window() -> None:
    """The number is in the name; ``--slow`` belongs to the crossover strategy.

    This read ``slow if slow > 2 else 200``, and every caller passes the
    crossover default of 50, so the guard could never fire: asking for sma200
    ran a 50-day filter. Nothing raised - a 50-day trend filter returns
    perfectly ordinary numbers - so only the printed strategy name gave it away.
    """
    from stock_ai.backtest.strategy import build_strategy

    assert build_strategy("sma200").name == "above_sma_200"
    assert build_strategy("sma200", fast=20, slow=50).name == "above_sma_200"
    assert build_strategy("sma200", slow=10, window=100).name == "above_sma_100"


def test_the_crossover_still_reads_fast_and_slow() -> None:
    from stock_ai.backtest.strategy import build_strategy

    assert build_strategy("sma", fast=5, slow=30, window=200).name == "sma_crossover_5_30"
