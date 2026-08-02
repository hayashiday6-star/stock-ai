"""Abstractions for market-data acquisition.

Downstream code depends on these interfaces, never on a concrete provider, so
providers (yfinance, J-Quants, ...) stay swappable via dependency injection.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

import pandas as pd


@runtime_checkable
class PriceProvider(Protocol):
    """Fetches historical OHLCV price bars for a single symbol."""

    def fetch_prices(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """Return canonical OHLCV bars for ``symbol`` over ``[start, end]``.

        Args:
            symbol: Provider-native ticker (e.g. ``"AAPL"``, ``"7203.T"``).
            start: First calendar date to include (inclusive).
            end: Last calendar date to include (inclusive).

        Returns:
            A DataFrame in the schema defined by :mod:`stock_ai.data.schema`.

        Raises:
            DataError: If no data is available or the response is malformed.
        """
        ...
