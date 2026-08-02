"""Performance metrics computed from an equity curve and a trade log.

All metrics derive from the daily mark-to-market equity series (so drawdown and
volatility are never understated) plus per-trade returns for win rate and
profit factor.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

TRADING_DAYS_PER_YEAR = 252


@dataclass(frozen=True)
class Metrics:
    """A bundle of headline backtest performance figures."""

    total_return: float
    cagr: float
    sharpe: float
    max_drawdown: float
    profit_factor: float
    win_rate: float
    num_trades: int


def total_return(equity: pd.Series) -> float:
    """Return the overall return of the equity curve (end / start - 1)."""
    return float(equity.iloc[-1] / equity.iloc[0] - 1.0)


def cagr(equity: pd.Series) -> float:
    """Compound annual growth rate based on the calendar span of the curve."""
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    if years <= 0:
        return 0.0
    return float((equity.iloc[-1] / equity.iloc[0]) ** (1.0 / years) - 1.0)


def sharpe_ratio(equity: pd.Series, periods_per_year: int = TRADING_DAYS_PER_YEAR) -> float:
    """Annualized Sharpe ratio of daily returns (risk-free rate assumed zero)."""
    returns = equity.pct_change().dropna()
    std = returns.std(ddof=0)
    if returns.empty or std == 0:
        return 0.0
    return float(returns.mean() / std * math.sqrt(periods_per_year))


def max_drawdown(equity: pd.Series) -> float:
    """Largest peak-to-trough decline of the equity curve (a negative number)."""
    peak = equity.cummax()
    drawdown = equity / peak - 1.0
    return float(drawdown.min())


def _trade_returns(returns: list[float]) -> tuple[float, float]:
    """Split trade returns into gross profit and gross loss (absolute)."""
    gross_profit = sum(r for r in returns if r > 0)
    gross_loss = abs(sum(r for r in returns if r < 0))
    return gross_profit, gross_loss


def profit_factor(trade_returns: list[float]) -> float:
    """Gross profit divided by gross loss; ``inf`` if there are no losers."""
    gross_profit, gross_loss = _trade_returns(trade_returns)
    if gross_loss == 0:
        return math.inf if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def win_rate(trade_returns: list[float]) -> float:
    """Fraction of trades with a positive return (0 if there are no trades)."""
    if not trade_returns:
        return 0.0
    return sum(1 for r in trade_returns if r > 0) / len(trade_returns)


def compute_metrics(equity: pd.Series, trade_returns: list[float]) -> Metrics:
    """Assemble a :class:`Metrics` bundle from equity and per-trade returns."""
    return Metrics(
        total_return=total_return(equity),
        cagr=cagr(equity),
        sharpe=sharpe_ratio(equity),
        max_drawdown=max_drawdown(equity),
        profit_factor=profit_factor(trade_returns),
        win_rate=win_rate(trade_returns),
        num_trades=len(trade_returns),
    )
