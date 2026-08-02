"""Strategy abstraction and a couple of reference strategies.

A strategy maps a price frame to a boolean "want to be long" signal. The engine
then handles execution (next-open fills) and accounting, so strategies stay pure
and easy to test.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from stock_ai.technical.indicators import sma


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
