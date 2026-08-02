"""Event-driven long/flat backtest engine.

Correctness guarantees (verified by tests), matching the project's backtest
protocol:

- **No look-ahead.** A signal observed at the close of bar *t* is filled at the
  **open of bar t+1**, never at bar *t*'s close.
- **Daily mark-to-market.** Equity is ``cash + shares * close`` on every bar, so
  drawdown and volatility reflect open positions, not just realised trades.

The engine is intentionally single-asset and fully invested when long; portfolio
allocation is a later concern layered on top of this core.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
import pandas as pd

from stock_ai.backtest.metrics import Metrics, compute_metrics
from stock_ai.core.exceptions import BacktestError
from stock_ai.core.logging import get_logger
from stock_ai.data.schema import CLOSE, OPEN

logger = get_logger(__name__)


@dataclass(frozen=True)
class Trade:
    """A completed round-trip trade (entry and exit fills)."""

    entry_date: dt.date
    entry_price: float
    exit_date: dt.date
    exit_price: float

    @property
    def return_pct(self) -> float:
        """Return of the trade as a fraction of the entry price."""
        return self.exit_price / self.entry_price - 1.0


@dataclass(frozen=True)
class BacktestResult:
    """The equity curve, trade log, and summary metrics of one run."""

    equity: pd.Series
    trades: list[Trade]
    metrics: Metrics


class BacktestEngine:
    """Simulate a long/flat strategy with next-open fills and daily MTM."""

    def __init__(
        self,
        initial_capital: float = 100_000.0,
        commission: float = 0.0,
        slippage: float = 0.0,
    ) -> None:
        """Configure the engine.

        Args:
            initial_capital: Starting cash.
            commission: Per-trade cost as a fraction of trade value (e.g. 0.001).
            slippage: Adverse price fraction applied to each fill (e.g. 0.0005).
        """
        self.initial_capital = initial_capital
        self.commission = commission
        self.slippage = slippage

    def run(self, prices: pd.DataFrame, signals: pd.Series) -> BacktestResult:
        """Run the backtest.

        Args:
            prices: Canonical OHLCV frame indexed by date.
            signals: Boolean "want to be long" series. A ``True`` at bar *t*
                means hold from the open of bar *t+1*.

        Returns:
            A :class:`BacktestResult`.

        Raises:
            BacktestError: If ``prices`` is empty or missing OHLC columns.
        """
        if prices.empty:
            raise BacktestError("Cannot backtest an empty price frame.")
        for column in (OPEN, CLOSE):
            if column not in prices.columns:
                raise BacktestError(f"Price frame is missing the {column!r} column.")

        index = prices.index
        open_ = prices[OPEN].to_numpy(dtype=float)
        close = prices[CLOSE].to_numpy(dtype=float)

        signal = signals.reindex(index).fillna(value=False).astype(bool).to_numpy()
        # Position wanted at bar i is the signal decided at bar i-1 (next-open fill).
        target = np.zeros(len(signal), dtype=bool)
        target[1:] = signal[:-1]

        cash = self.initial_capital
        shares = 0.0
        entry_price = 0.0
        entry_date: dt.date | None = None
        trades: list[Trade] = []
        nav = np.empty(len(index), dtype=float)

        for i in range(len(index)):
            want_long = bool(target[i])
            holding = shares > 0.0

            if want_long and not holding:
                fill = open_[i] * (1.0 + self.slippage)
                shares = cash / (fill * (1.0 + self.commission))
                cash -= shares * fill * (1.0 + self.commission)
                entry_price = fill
                entry_date = index[i].date()
            elif not want_long and holding:
                fill = open_[i] * (1.0 - self.slippage)
                cash += shares * fill * (1.0 - self.commission)
                trades.append(Trade(entry_date, entry_price, index[i].date(), fill))
                shares = 0.0

            nav[i] = cash + shares * close[i]

        # Close any position still open at the last bar for complete trade stats.
        if shares > 0.0 and entry_date is not None:
            trades.append(Trade(entry_date, entry_price, index[-1].date(), close[-1]))

        equity = pd.Series(nav, index=index, name="equity")
        metrics = compute_metrics(equity, [t.return_pct for t in trades])
        logger.info(
            "Backtest: return=%.2f%% CAGR=%.2f%% Sharpe=%.2f MaxDD=%.2f%% trades=%d",
            metrics.total_return * 100,
            metrics.cagr * 100,
            metrics.sharpe,
            metrics.max_drawdown * 100,
            metrics.num_trades,
        )
        return BacktestResult(equity=equity, trades=trades, metrics=metrics)
