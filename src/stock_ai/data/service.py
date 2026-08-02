"""Ingestion service: fetch prices via a provider and persist them.

Combines a :class:`~stock_ai.data.base.PriceProvider` with the database. It is
incremental: for a symbol that already has data it only fetches bars newer than
the latest stored date, which is what makes daily updates cheap and idempotent.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from stock_ai.core.exceptions import StockAIError
from stock_ai.core.logging import get_logger
from stock_ai.data.base import FundamentalsProvider, PriceProvider
from stock_ai.database.engine import Database
from stock_ai.database.repository import FundamentalsRepository, PriceRepository

logger = get_logger(__name__)

_DEFAULT_LOOKBACK_DAYS = 365


@dataclass(frozen=True)
class IngestResult:
    """Outcome of ingesting a single symbol."""

    symbol: str
    rows: int
    ok: bool
    error: str | None = None


class IngestionService:
    """Fetch and store price data, one symbol or many."""

    def __init__(
        self,
        provider: PriceProvider,
        database: Database,
        default_lookback_days: int = _DEFAULT_LOOKBACK_DAYS,
    ) -> None:
        """Wire the service to a provider and a database.

        Args:
            provider: Source of OHLCV bars.
            database: Persistence target.
            default_lookback_days: Backfill window used when a symbol has no
                stored data yet.
        """
        self.provider = provider
        self.database = database
        self.default_lookback_days = default_lookback_days

    def ingest_symbol(
        self,
        symbol: str,
        start: dt.date | None = None,
        end: dt.date | None = None,
        market: str = "US",
    ) -> IngestResult:
        """Fetch and store bars for ``symbol``.

        When ``start`` is omitted it is resolved incrementally: the day after the
        latest stored bar, or ``end - default_lookback_days`` for a new symbol.

        Args:
            symbol: Provider-native ticker.
            start: Explicit start date, or ``None`` to resolve incrementally.
            end: End date (inclusive); defaults to today.
            market: Market code stored on a newly created security.

        Returns:
            An :class:`IngestResult`; failures are captured, never raised.
        """
        end = end or dt.date.today()
        try:
            with self.database.session() as session:
                repo = PriceRepository(session)
                resolved_start = start or self._resolve_start(repo, symbol, end)
                if resolved_start > end:
                    logger.info("%s already up to date (%s)", symbol, end)
                    return IngestResult(symbol, 0, ok=True)

                prices = self.provider.fetch_prices(symbol, resolved_start, end)
                rows = repo.upsert_prices(symbol, prices, market=market)
                logger.info("Ingested %d bars for %s", rows, symbol)
                return IngestResult(symbol, rows, ok=True)
        except StockAIError as exc:
            logger.warning("Ingest failed for %s: %s", symbol, exc)
            return IngestResult(symbol, 0, ok=False, error=str(exc))
        except Exception as exc:  # provider/network errors must not abort a batch
            logger.exception("Unexpected ingest error for %s", symbol)
            return IngestResult(symbol, 0, ok=False, error=str(exc))

    def ingest_many(
        self,
        symbols: list[str],
        start: dt.date | None = None,
        end: dt.date | None = None,
        market: str = "US",
    ) -> list[IngestResult]:
        """Ingest each symbol, continuing past individual failures."""
        return [self.ingest_symbol(sym, start, end, market) for sym in symbols]

    def _resolve_start(self, repo: PriceRepository, symbol: str, end: dt.date) -> dt.date:
        """Return the incremental start date for ``symbol``."""
        latest = repo.latest_date(symbol)
        if latest is None:
            return end - dt.timedelta(days=self.default_lookback_days)
        return latest + dt.timedelta(days=1)


class FundamentalsService:
    """Fetch and store fundamentals snapshots, one symbol or many."""

    def __init__(self, provider: FundamentalsProvider, database: Database) -> None:
        """Wire the service to a fundamentals provider and a database."""
        self.provider = provider
        self.database = database

    def ingest_symbol(self, symbol: str, market: str = "US") -> IngestResult:
        """Fetch and store the latest fundamentals for ``symbol``.

        Returns:
            An :class:`IngestResult` (``rows=1`` on success); failures are
            captured, never raised.
        """
        try:
            with self.database.session() as session:
                fundamentals = self.provider.fetch_fundamentals(symbol)
                FundamentalsRepository(session).upsert_fundamentals(fundamentals, market=market)
                logger.info("Ingested fundamentals for %s", symbol)
                return IngestResult(symbol, 1, ok=True)
        except StockAIError as exc:
            logger.warning("Fundamentals ingest failed for %s: %s", symbol, exc)
            return IngestResult(symbol, 0, ok=False, error=str(exc))
        except Exception as exc:  # provider/network errors must not abort a batch
            logger.exception("Unexpected fundamentals error for %s", symbol)
            return IngestResult(symbol, 0, ok=False, error=str(exc))

    def ingest_many(self, symbols: list[str], market: str = "US") -> list[IngestResult]:
        """Ingest each symbol's fundamentals, continuing past failures."""
        return [self.ingest_symbol(sym, market) for sym in symbols]
