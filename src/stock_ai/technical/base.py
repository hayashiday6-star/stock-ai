"""Composable indicator objects and a helper to apply many at once.

Each :class:`Indicator` wraps a function from :mod:`stock_ai.technical.indicators`
and always returns a DataFrame, so :func:`apply` can concatenate any mix of them
into one table. Add a new indicator by subclassing :class:`Indicator`; nothing
else needs to change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from stock_ai.technical import indicators as ind


class Indicator(ABC):
    """A named transform from an OHLCV frame to one or more indicator columns."""

    @abstractmethod
    def compute(self, prices: pd.DataFrame) -> pd.DataFrame:
        """Return the indicator's output columns, indexed like ``prices``."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier for this configured indicator."""


class SMA(Indicator):
    """Simple moving average of the close."""

    def __init__(self, window: int = 20) -> None:
        """Store the averaging window."""
        self.window = window

    @property
    def name(self) -> str:
        """Return ``sma_<window>``."""
        return f"sma_{self.window}"

    def compute(self, prices: pd.DataFrame) -> pd.DataFrame:
        """Compute the SMA as a single-column frame."""
        return ind.sma(prices, self.window).to_frame()


class EMA(Indicator):
    """Exponential moving average of the close."""

    def __init__(self, span: int = 20) -> None:
        """Store the EMA span."""
        self.span = span

    @property
    def name(self) -> str:
        """Return ``ema_<span>``."""
        return f"ema_{self.span}"

    def compute(self, prices: pd.DataFrame) -> pd.DataFrame:
        """Compute the EMA as a single-column frame."""
        return ind.ema(prices, self.span).to_frame()


class RSI(Indicator):
    """Wilder's Relative Strength Index."""

    def __init__(self, window: int = 14) -> None:
        """Store the RSI window."""
        self.window = window

    @property
    def name(self) -> str:
        """Return ``rsi_<window>``."""
        return f"rsi_{self.window}"

    def compute(self, prices: pd.DataFrame) -> pd.DataFrame:
        """Compute the RSI as a single-column frame."""
        return ind.rsi(prices, self.window).to_frame()


class MACD(Indicator):
    """Moving Average Convergence Divergence."""

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9) -> None:
        """Store the fast, slow, and signal spans."""
        self.fast = fast
        self.slow = slow
        self.signal = signal

    @property
    def name(self) -> str:
        """Return ``macd``."""
        return "macd"

    def compute(self, prices: pd.DataFrame) -> pd.DataFrame:
        """Compute MACD line, signal, and histogram."""
        return ind.macd(prices, self.fast, self.slow, self.signal)


class BollingerBands(Indicator):
    """Bollinger Bands around the simple moving average."""

    def __init__(self, window: int = 20, num_std: float = 2.0) -> None:
        """Store the window and standard-deviation multiplier."""
        self.window = window
        self.num_std = num_std

    @property
    def name(self) -> str:
        """Return ``bbands``."""
        return "bbands"

    def compute(self, prices: pd.DataFrame) -> pd.DataFrame:
        """Compute the middle, upper, and lower bands."""
        return ind.bollinger_bands(prices, self.window, self.num_std).add_prefix("bb_")


class ATR(Indicator):
    """Average True Range."""

    def __init__(self, window: int = 14) -> None:
        """Store the ATR window."""
        self.window = window

    @property
    def name(self) -> str:
        """Return ``atr_<window>``."""
        return f"atr_{self.window}"

    def compute(self, prices: pd.DataFrame) -> pd.DataFrame:
        """Compute the ATR as a single-column frame."""
        return ind.atr(prices, self.window).to_frame()


class ADX(Indicator):
    """Average Directional Index with directional indicators."""

    def __init__(self, window: int = 14) -> None:
        """Store the ADX window."""
        self.window = window

    @property
    def name(self) -> str:
        """Return ``adx``."""
        return "adx"

    def compute(self, prices: pd.DataFrame) -> pd.DataFrame:
        """Compute ADX, +DI, and -DI."""
        return ind.adx(prices, self.window)


class Stochastic(Indicator):
    """Stochastic oscillator (%K and %D)."""

    def __init__(self, k: int = 14, d: int = 3) -> None:
        """Store the %K and %D windows."""
        self.k = k
        self.d = d

    @property
    def name(self) -> str:
        """Return ``stochastic``."""
        return "stochastic"

    def compute(self, prices: pd.DataFrame) -> pd.DataFrame:
        """Compute %K and %D."""
        return ind.stochastic(prices, self.k, self.d)


class OBV(Indicator):
    """On-Balance Volume."""

    @property
    def name(self) -> str:
        """Return ``obv``."""
        return "obv"

    def compute(self, prices: pd.DataFrame) -> pd.DataFrame:
        """Compute OBV as a single-column frame."""
        return ind.on_balance_volume(prices).to_frame()


def apply(
    prices: pd.DataFrame,
    indicators: list[Indicator],
    with_prices: bool = False,
) -> pd.DataFrame:
    """Compute several indicators and concatenate them column-wise.

    Args:
        prices: Canonical OHLCV frame.
        indicators: Indicators to compute.
        with_prices: If ``True``, prepend the original price columns.

    Returns:
        A DataFrame indexed like ``prices`` with all indicator columns.
    """
    frames = [indicator.compute(prices) for indicator in indicators]
    parts = [prices, *frames] if with_prices else frames
    return pd.concat(parts, axis=1)
