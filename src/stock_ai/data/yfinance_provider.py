"""yfinance-backed :class:`~stock_ai.data.base.PriceProvider` (US equities).

The network call is isolated behind an injectable ``downloader`` callable so the
provider can be unit-tested without touching the network.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

import pandas as pd

from stock_ai.core.exceptions import DataError
from stock_ai.core.logging import get_logger
from stock_ai.data.schema import normalize_ohlcv

logger = get_logger(__name__)

# A downloader takes (symbol, start, end) and returns a raw yfinance frame.
Downloader = Callable[[str, date, date], pd.DataFrame]


def _default_download(symbol: str, start: date, end: date) -> pd.DataFrame:
    """Download raw daily bars from yfinance (imported lazily).

    yfinance treats ``end`` as exclusive, so we request one extra day to make
    the public ``[start, end]`` contract inclusive.
    """
    import yfinance as yf

    end_exclusive = end + timedelta(days=1)
    return yf.download(
        symbol,
        start=start.isoformat(),
        end=end_exclusive.isoformat(),
        auto_adjust=False,
        progress=False,
    )


class YFinancePriceProvider:
    """Fetch US equity OHLCV bars via yfinance."""

    def __init__(self, downloader: Downloader | None = None) -> None:
        """Create the provider.

        Args:
            downloader: Callable performing the raw download. Defaults to
                :func:`_default_download`; inject a fake in tests.
        """
        self._download = downloader or _default_download

    def fetch_prices(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """Fetch and normalize daily bars for ``symbol`` over ``[start, end]``."""
        if start > end:
            raise DataError(f"start ({start}) must not be after end ({end}).")

        logger.debug("Fetching prices for %s: %s..%s", symbol, start, end)
        raw = self._download(symbol, start, end)
        if raw is None or raw.empty:
            raise DataError(f"No price data returned for {symbol!r} in {start}..{end}.")

        prices = normalize_ohlcv(raw)
        logger.info("Fetched %d bars for %s", len(prices), symbol)
        return prices
