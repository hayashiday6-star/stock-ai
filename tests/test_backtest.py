"""Tests for the backtest engine, metrics, and strategies.

The engine tests deliberately pin down the two correctness properties that
matter most: no look-ahead (next-open fills) and daily mark-to-market.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stock_ai.backtest.engine import BacktestEngine
from stock_ai.backtest.metrics import (
    cagr,
    max_drawdown,
    profit_factor,
    sharpe_ratio,
    win_rate,
)
from stock_ai.backtest.strategy import BuyAndHold, SMACrossover
from stock_ai.core.exceptions import BacktestError
from stock_ai.data.schema import CLOSE, HIGH, LOW, OPEN, VOLUME


def _prices(opens: list[float], closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(opens), freq="D")
    return pd.DataFrame(
        {
            OPEN: opens,
            HIGH: [max(o, c) for o, c in zip(opens, closes, strict=True)],
            LOW: [min(o, c) for o, c in zip(opens, closes, strict=True)],
            CLOSE: closes,
            "adj_close": closes,
            VOLUME: [1000] * len(opens),
        },
        index=idx,
    )


def test_no_lookahead_signal_fills_next_open() -> None:
    # Signal True at bar 0 must NOT act on bar 0; it fills at bar 1's open.
    prices = _prices(opens=[10, 10, 10], closes=[10, 20, 20])
    signals = pd.Series([True, True, True], index=prices.index)

    result = BacktestEngine(initial_capital=100_000).run(prices, signals)
    equity = result.equity

    assert equity.iloc[0] == pytest.approx(100_000)  # flat on bar 0
    assert equity.iloc[1] == pytest.approx(200_000)  # bought at open 10, MTM close 20


def test_daily_mark_to_market() -> None:
    # While long, equity must track the daily close, not just realised P&L.
    prices = _prices(opens=[10, 10, 10, 10], closes=[10, 12, 9, 11])
    signals = pd.Series([True, True, True, True], index=prices.index)

    equity = BacktestEngine(initial_capital=100_000).run(prices, signals).equity
    # Entered at bar 1 open (10) with 10_000 shares.
    assert equity.iloc[1] == pytest.approx(120_000)
    assert equity.iloc[2] == pytest.approx(90_000)  # unrealised drawdown shows up
    assert equity.iloc[3] == pytest.approx(110_000)


def test_commission_reduces_equity() -> None:
    prices = _prices(opens=[10, 10, 10], closes=[10, 10, 10])
    signals = pd.Series([True, False, False], index=prices.index)  # one round trip

    no_cost = BacktestEngine(commission=0.0).run(prices, signals).equity.iloc[-1]
    with_cost = BacktestEngine(commission=0.01).run(prices, signals).equity.iloc[-1]
    assert with_cost < no_cost


def test_records_completed_trade() -> None:
    prices = _prices(opens=[10, 10, 10], closes=[10, 10, 10])
    signals = pd.Series([True, True, False], index=prices.index)
    # Buy at bar 1 open (10), sell at bar 2 open (10).
    result = BacktestEngine().run(prices, signals)
    assert len(result.trades) == 1
    assert result.trades[0].entry_price == pytest.approx(10)
    assert result.trades[0].exit_price == pytest.approx(10)


def test_empty_prices_raises() -> None:
    with pytest.raises(BacktestError):
        BacktestEngine().run(pd.DataFrame(), pd.Series(dtype=bool))


def test_buy_and_hold_signal_is_all_true() -> None:
    prices = _prices([10, 11], [10, 11])
    signals = BuyAndHold().generate_signals(prices)
    assert signals.all()
    assert len(signals) == 2


def test_sma_crossover_signal_is_boolean() -> None:
    closes = [float(i) for i in range(1, 40)]
    prices = _prices(closes, closes)
    signals = SMACrossover(fast=5, slow=10).generate_signals(prices)
    assert signals.dtype == bool
    assert signals.iloc[-1]  # steadily rising -> fast above slow at the end


def test_sma_crossover_rejects_bad_windows() -> None:
    with pytest.raises(ValueError, match="smaller"):
        SMACrossover(fast=50, slow=20)


# --- metrics ---------------------------------------------------------------


def test_max_drawdown() -> None:
    idx = pd.date_range("2024-01-01", periods=4, freq="D")
    equity = pd.Series([100, 120, 90, 110], index=idx)
    assert max_drawdown(equity) == pytest.approx(-0.25)


def test_cagr_of_doubling_over_one_year() -> None:
    idx = pd.to_datetime(["2023-01-01", "2024-01-01"])
    equity = pd.Series([100.0, 200.0], index=idx)
    assert cagr(equity) == pytest.approx(1.0, abs=1e-2)


def test_sharpe_zero_volatility_is_zero() -> None:
    idx = pd.date_range("2024-01-01", periods=5, freq="D")
    assert sharpe_ratio(pd.Series([100.0] * 5, index=idx)) == 0.0


def test_profit_factor_and_win_rate() -> None:
    returns = [0.1, -0.05, 0.2]
    assert profit_factor(returns) == pytest.approx(0.3 / 0.05)
    assert win_rate(returns) == pytest.approx(2 / 3)
    assert profit_factor([0.1, 0.2]) == np.inf  # no losers
    assert win_rate([]) == 0.0
