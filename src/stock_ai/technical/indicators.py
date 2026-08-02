"""Technical indicators computed from a canonical OHLCV frame.

Every function takes the canonical price frame (see :mod:`stock_ai.data.schema`)
and returns a pandas ``Series`` (single-line indicators) or ``DataFrame``
(multi-line indicators), indexed to match the input. Insufficient history yields
leading ``NaN`` rather than an error; a missing required column is an error.
"""

from __future__ import annotations

import pandas as pd

from stock_ai.core.exceptions import DataError
from stock_ai.data.schema import CLOSE


def _series(prices: pd.DataFrame, column: str) -> pd.Series:
    """Return ``column`` from ``prices`` or raise if it is absent."""
    if column not in prices.columns:
        raise DataError(f"Price frame is missing the {column!r} column.")
    return prices[column]


def sma(prices: pd.DataFrame, window: int = 20) -> pd.Series:
    """Simple moving average of the close over ``window`` bars."""
    return _series(prices, CLOSE).rolling(window).mean().rename(f"sma_{window}")


def ema(prices: pd.DataFrame, span: int = 20) -> pd.Series:
    """Exponential moving average of the close (``adjust=False``)."""
    return _series(prices, CLOSE).ewm(span=span, adjust=False).mean().rename(f"ema_{span}")


def rsi(prices: pd.DataFrame, window: int = 14) -> pd.Series:
    """Wilder's Relative Strength Index of the close over ``window`` bars."""
    close = _series(prices, CLOSE)
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    # Wilder's smoothing == an EWMA with alpha = 1 / window.
    avg_gain = gain.ewm(alpha=1 / window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / window, adjust=False).mean()

    rs = avg_gain / avg_loss
    return (100.0 - 100.0 / (1.0 + rs)).rename(f"rsi_{window}")


def macd(
    prices: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """MACD line, signal line, and histogram.

    Returns:
        A DataFrame with columns ``macd``, ``signal``, ``histogram``.
    """
    close = _series(prices, CLOSE)
    fast_ema = close.ewm(span=fast, adjust=False).mean()
    slow_ema = close.ewm(span=slow, adjust=False).mean()
    macd_line = fast_ema - slow_ema
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame(
        {
            "macd": macd_line,
            "signal": signal_line,
            "histogram": macd_line - signal_line,
        }
    )


def bollinger_bands(
    prices: pd.DataFrame,
    window: int = 20,
    num_std: float = 2.0,
) -> pd.DataFrame:
    """Bollinger Bands around the simple moving average.

    Returns:
        A DataFrame with columns ``middle``, ``upper``, ``lower``. The standard
        deviation is population (``ddof=0``), matching the common convention.
    """
    close = _series(prices, CLOSE)
    middle = close.rolling(window).mean()
    deviation = close.rolling(window).std(ddof=0)
    return pd.DataFrame(
        {
            "middle": middle,
            "upper": middle + num_std * deviation,
            "lower": middle - num_std * deviation,
        }
    )
