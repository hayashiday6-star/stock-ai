"""Strategy abstraction and a couple of reference strategies.

A strategy maps a price frame to a boolean "want to be long" signal. The engine
then handles execution (next-open fills) and accounting, so strategies stay pure
and easy to test.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from stock_ai.data.schema import CLOSE
from stock_ai.technical.indicators import macd, rsi, sma


class Strategy(ABC):
    """Maps prices to a boolean long/flat signal series."""

    @abstractmethod
    def generate_signals(self, prices: pd.DataFrame) -> pd.Series:
        """Return a boolean Series indexed like ``prices`` (``True`` = long)."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier for this configured strategy."""


class BuyAndHold(Strategy):
    """Always long: the natural benchmark for a single asset."""

    @property
    def name(self) -> str:
        """Return ``buy_and_hold``."""
        return "buy_and_hold"

    def generate_signals(self, prices: pd.DataFrame) -> pd.Series:
        """Return an all-``True`` signal."""
        return pd.Series(True, index=prices.index, name="signal")


class SMACrossover(Strategy):
    """Long while the fast SMA is above the slow SMA, flat otherwise."""

    def __init__(self, fast: int = 20, slow: int = 50) -> None:
        """Store the fast and slow SMA windows.

        Raises:
            ValueError: If ``fast`` is not smaller than ``slow``.
        """
        if fast >= slow:
            raise ValueError(f"fast ({fast}) must be smaller than slow ({slow}).")
        self.fast = fast
        self.slow = slow

    @property
    def name(self) -> str:
        """Return ``sma_crossover_<fast>_<slow>``."""
        return f"sma_crossover_{self.fast}_{self.slow}"

    def generate_signals(self, prices: pd.DataFrame) -> pd.Series:
        """Return ``True`` where the fast SMA exceeds the slow SMA."""
        fast_sma = sma(prices, self.fast)
        slow_sma = sma(prices, self.slow)
        signal = (fast_sma > slow_sma) & fast_sma.notna() & slow_sma.notna()
        return signal.rename("signal").astype(bool).reindex(prices.index, fill_value=False)


class AboveSMA(Strategy):
    """Trend filter: long while the close is above its ``window``-day SMA."""

    def __init__(self, window: int = 200) -> None:
        """Store the SMA window."""
        self.window = window

    @property
    def name(self) -> str:
        """Return ``above_sma_<window>``."""
        return f"above_sma_{self.window}"

    def generate_signals(self, prices: pd.DataFrame) -> pd.Series:
        """Return ``True`` where the close is above the SMA."""
        moving_avg = sma(prices, self.window)
        signal = (prices[CLOSE] > moving_avg) & moving_avg.notna()
        return signal.rename("signal").astype(bool).reindex(prices.index, fill_value=False)


class MACDCross(Strategy):
    """Long while the MACD line is above its signal line."""

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9) -> None:
        """Store the MACD spans."""
        self.fast = fast
        self.slow = slow
        self.signal = signal

    @property
    def name(self) -> str:
        """Return ``macd_cross``."""
        return "macd_cross"

    def generate_signals(self, prices: pd.DataFrame) -> pd.Series:
        """Return ``True`` where the MACD line exceeds the signal line."""
        frame = macd(prices, self.fast, self.slow, self.signal)
        line, sig = frame["macd"], frame["signal"]
        signal = (line > sig) & line.notna() & sig.notna()
        return signal.rename("signal").astype(bool).reindex(prices.index, fill_value=False)


class RSIReversion(Strategy):
    """Mean reversion: enter when RSI drops below ``buy_below``, exit above ``exit_above``.

    The signal is stateful — once long, it stays long until RSI recovers past
    ``exit_above`` — so it holds through the middle band rather than flip-flopping.
    """

    def __init__(self, period: int = 14, buy_below: float = 30.0, exit_above: float = 55.0) -> None:
        """Store the RSI period and the entry/exit thresholds.

        Raises:
            ValueError: If ``buy_below`` is not below ``exit_above``.
        """
        if buy_below >= exit_above:
            raise ValueError(f"buy_below ({buy_below}) must be below exit_above ({exit_above}).")
        self.period = period
        self.buy_below = buy_below
        self.exit_above = exit_above

    @property
    def name(self) -> str:
        """Return ``rsi_reversion_<period>``."""
        return f"rsi_reversion_{self.period}"

    def generate_signals(self, prices: pd.DataFrame) -> pd.Series:
        """Return a stateful long/flat signal from RSI thresholds."""
        rsi_values = rsi(prices, self.period)
        holding = False
        flags: list[bool] = []
        for value in rsi_values:
            if pd.notna(value):
                if not holding and value < self.buy_below:
                    holding = True
                elif holding and value > self.exit_above:
                    holding = False
            flags.append(holding)
        return pd.Series(flags, index=prices.index, name="signal", dtype=bool)


def build_strategy(name: str, fast: int = 20, slow: int = 50) -> Strategy:
    """Construct a strategy from its short name.

    Args:
        name: ``hold`` | ``sma`` | ``sma200`` | ``macd`` | ``rsi``.
        fast: Fast SMA window (``sma`` only).
        slow: Slow SMA window (``sma`` and ``sma200`` window).

    Returns:
        The configured :class:`Strategy`.

    Raises:
        ValueError: If ``name`` is unknown.
    """
    key = name.lower()
    if key == "hold":
        return BuyAndHold()
    if key == "sma":
        return SMACrossover(fast=fast, slow=slow)
    if key == "sma200":
        return AboveSMA(window=slow if slow > 2 else 200)
    if key == "macd":
        return MACDCross()
    if key == "rsi":
        return RSIReversion()
    raise ValueError(f"Unknown strategy {name!r}; use hold|sma|sma200|macd|rsi.")
