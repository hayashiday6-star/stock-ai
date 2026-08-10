"""yfinance-backed :class:`~stock_ai.data.base.PriceProvider` (US equities).

The network call is isolated behind an injectable ``downloader`` callable so the
provider can be unit-tested without touching the network.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

import pandas as pd

from stock_ai.core.exceptions import DataError
from stock_ai.core.logging import get_logger
from stock_ai.data.sanity import plausible_dividend_yield
from stock_ai.data.schema import normalize_ohlcv
from stock_ai.data.sectors import from_yfinance
from stock_ai.data.types import Fundamentals, SecurityProfile

logger = get_logger(__name__)

# A downloader takes (symbol, start, end) and returns a raw yfinance frame.
Downloader = Callable[[str, date, date], pd.DataFrame]
# An info fetcher takes a symbol and returns yfinance's ``Ticker.info`` dict.
InfoFetcher = Callable[[str], dict[str, Any]]
Clock = Callable[[], date]


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


def _to_float(value: Any) -> float | None:
    """Coerce a provider value to ``float``, mapping missing/NaN/junk to ``None``."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


def _dividend_yield(info: dict[str, Any]) -> float | None:
    """Return the annual dividend yield as a **fraction** (0.023 = 2.3%).

    ``dividendRate / price`` is the primary path: it is unambiguous, and it
    matches how the J-Quants provider derives the same figure, which the two
    must agree on for cross-market ranking to mean anything.

    The ``dividendYield`` fallback is read as a **percentage**. Observed live:
    yfinance returned ``0.78`` for MSFT and ``0.13`` for MRVL - real yields of
    0.78% and 0.13%. An earlier version of this function rescaled only values
    above 1.0, on the theory that a percentage always exceeds one; those two
    names disprove it, and both were stored as 78% and 13%, which is enough to
    hand a mega-cap a perfect dividend score.
    """
    price = _to_float(info.get("currentPrice")) or _to_float(info.get("previousClose"))
    rate = _to_float(info.get("dividendRate"))
    if rate is not None and price:
        return plausible_dividend_yield(rate / price)

    raw = _to_float(info.get("dividendYield"))
    if raw is None:
        return None
    return plausible_dividend_yield(raw / 100.0)


def _default_info_fetcher(symbol: str) -> dict[str, Any]:
    """Fetch ``Ticker.info`` from yfinance (imported lazily)."""
    import yfinance as yf

    return yf.Ticker(symbol).info


class YFinanceFundamentalsProvider:
    """Fetch a fundamentals snapshot via yfinance's ``Ticker.info``."""

    def __init__(self, info_fetcher: InfoFetcher | None = None, clock: Clock | None = None) -> None:
        """Create the provider.

        Args:
            info_fetcher: Callable returning the raw info dict. Defaults to
                :func:`_default_info_fetcher`; inject a fake in tests.
            clock: Callable returning the snapshot date. Defaults to today.
        """
        self._info = info_fetcher or _default_info_fetcher
        self._today = clock or dt.date.today

    def fetch_fundamentals(self, symbol: str) -> Fundamentals:
        """Fetch and normalize fundamentals for ``symbol``."""
        info = self._info(symbol)
        if not info:
            raise DataError(f"No fundamentals returned for {symbol!r}.")

        return Fundamentals(
            symbol=symbol,
            as_of=self._today(),
            per=_to_float(info.get("trailingPE")),
            pbr=_to_float(info.get("priceToBook")),
            roe=_to_float(info.get("returnOnEquity")),
            revenue=_to_float(info.get("totalRevenue")),
            net_income=_to_float(info.get("netIncomeToCommon")),
            dividend_yield=_dividend_yield(info),
            market_cap=_to_float(info.get("marketCap")),
        )


class YFinanceProfileProvider:
    """Fetch a security's descriptive profile via yfinance's ``Ticker.info``."""

    name = "yfinance"

    def __init__(self, info_fetcher: InfoFetcher | None = None) -> None:
        """Create the provider.

        Args:
            info_fetcher: Callable returning the raw info dict; injected in tests.
        """
        self._info = info_fetcher or _default_info_fetcher

    def fetch_profile(self, symbol: str) -> SecurityProfile:
        """Fetch and normalize the profile for ``symbol``."""
        info = self._info(symbol)
        if not info:
            raise DataError(f"No profile returned for {symbol!r}.")

        return SecurityProfile(
            symbol=symbol,
            market="US",
            name=info.get("longName") or info.get("shortName"),
            sector=str(from_yfinance(info.get("sector"))),
            industry=info.get("industry"),
        )
