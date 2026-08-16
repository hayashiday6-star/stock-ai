"""Technical indicators computed from a canonical OHLCV frame.

Every function takes the canonical price frame (see :mod:`stock_ai.data.schema`)
and returns a pandas ``Series`` (single-line indicators) or ``DataFrame``
(multi-line indicators), indexed to match the input. Insufficient history yields
leading ``NaN`` rather than an error; a missing required column is an error.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from stock_ai.core.exceptions import DataError
from stock_ai.data.schema import CLOSE, HIGH, LOW, VOLUME


def _series(prices: pd.DataFrame, column: str) -> pd.Series:
    """Return ``column`` from ``prices`` or raise if it is absent."""
    if column not in prices.columns:
        raise DataError(f"Price frame is missing the {column!r} column.")
    return prices[column]


def _wilder(series: pd.Series, window: int) -> pd.Series:
    """Wilder's smoothing (RMA): an EWMA with ``alpha = 1 / window``."""
    return series.ewm(alpha=1 / window, adjust=False).mean()


def _true_range(prices: pd.DataFrame) -> pd.Series:
    """Compute the True Range series from high, low, and previous close."""
    high = _series(prices, HIGH)
    low = _series(prices, LOW)
    prev_close = _series(prices, CLOSE).shift(1)
    ranges = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    )
    return ranges.max(axis=1)


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
    result = 100.0 - 100.0 / (1.0 + rs)
    # A perfectly flat stretch (a halted or limit-locked name) gives 0/0. There
    # is no directional pressure either way, so RSI is 50 by convention rather
    # than NaN. The leading NaN from ``diff()`` is untouched: NaN == 0 is False.
    return result.mask((avg_gain == 0.0) & (avg_loss == 0.0), 50.0).rename(f"rsi_{window}")


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


def atr(prices: pd.DataFrame, window: int = 14) -> pd.Series:
    """Average True Range (Wilder-smoothed) over ``window`` bars."""
    return _wilder(_true_range(prices), window).rename(f"atr_{window}")


def adx(prices: pd.DataFrame, window: int = 14) -> pd.DataFrame:
    """Average Directional Index with the directional indicators.

    Returns:
        A DataFrame with columns ``adx``, ``plus_di``, ``minus_di``.
    """
    high = _series(prices, HIGH)
    low = _series(prices, LOW)

    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=prices.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=prices.index,
    )

    # A flat stretch drives ATR (and both DIs) to zero. Guard each division so
    # "no movement" reads as 0 rather than 0/0 = NaN.
    atr_ = _wilder(_true_range(prices), window)
    plus_di = (100.0 * _wilder(plus_dm, window) / atr_).mask(atr_ == 0.0, 0.0)
    minus_di = (100.0 * _wilder(minus_dm, window) / atr_).mask(atr_ == 0.0, 0.0)
    di_sum = plus_di + minus_di
    dx = (100.0 * (plus_di - minus_di).abs() / di_sum).mask(di_sum == 0.0, 0.0)
    return pd.DataFrame({"adx": _wilder(dx, window), "plus_di": plus_di, "minus_di": minus_di})


def stochastic(prices: pd.DataFrame, k: int = 14, d: int = 3) -> pd.DataFrame:
    """Stochastic oscillator (%K and its %D moving average).

    Returns:
        A DataFrame with columns ``stoch_k``, ``stoch_d``.
    """
    low = _series(prices, LOW)
    high = _series(prices, HIGH)
    close = _series(prices, CLOSE)
    lowest = low.rolling(k).min()
    highest = high.rolling(k).max()
    span = highest - lowest
    # A zero-width range (flat window) would give 0/0; the close sits exactly
    # mid-range there, so %K is 50 rather than NaN.
    percent_k = (100.0 * (close - lowest) / span).mask(span == 0.0, 50.0)
    return pd.DataFrame({"stoch_k": percent_k, "stoch_d": percent_k.rolling(d).mean()})


def on_balance_volume(prices: pd.DataFrame) -> pd.Series:
    """On-Balance Volume: running total of volume signed by close direction."""
    close = _series(prices, CLOSE)
    volume = _series(prices, VOLUME)
    direction = np.sign(close.diff().fillna(0.0))
    return (direction * volume).cumsum().rename("obv")


def volume_sma(prices: pd.DataFrame, window: int = 20) -> pd.Series:
    """Simple moving average of volume over ``window`` bars."""
    return _series(prices, VOLUME).rolling(window).mean().rename(f"volume_sma_{window}")
