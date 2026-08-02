"""Assemble backtest results into a comparison table.

Per the project's backtest protocol, a strategy is always reported next to a
benchmark and judged on risk-adjusted terms (Sharpe, max drawdown), not just
absolute return.
"""

from __future__ import annotations

import pandas as pd

from stock_ai.backtest.engine import BacktestResult

_METRIC_COLUMNS: list[str] = [
    "total_return",
    "cagr",
    "sharpe",
    "max_drawdown",
    "profit_factor",
    "win_rate",
    "num_trades",
]


def metrics_frame(named_results: dict[str, BacktestResult]) -> pd.DataFrame:
    """Build a one-row-per-strategy metrics table.

    Args:
        named_results: Mapping of display name to its backtest result.

    Returns:
        A DataFrame with a ``strategy`` column plus one column per metric.
    """
    rows = []
    for name, result in named_results.items():
        metrics = result.metrics
        rows.append(
            {
                "strategy": name,
                "total_return": metrics.total_return,
                "cagr": metrics.cagr,
                "sharpe": metrics.sharpe,
                "max_drawdown": metrics.max_drawdown,
                "profit_factor": metrics.profit_factor,
                "win_rate": metrics.win_rate,
                "num_trades": metrics.num_trades,
            }
        )
    return pd.DataFrame(rows, columns=["strategy", *_METRIC_COLUMNS])
