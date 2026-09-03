"""Ingestion service: fetch prices via a provider and persist them.

Combines a :class:`~stock_ai.data.base.PriceProvider` with the database. It is
incremental: for a symbol that already has data it only fetches bars newer than
the latest stored date, which is what makes daily updates cheap and idempotent.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from stock_ai.core.exceptions import NoDataError, RateLimitError, StockAIError
from stock_ai.core.logging import get_logger
from stock_ai.data.base import FundamentalsProvider, PriceProvider
from stock_ai.database.engine import Database
from stock_ai.database.repository import FundamentalsRepository, PriceRepository

logger = get_logger(__name__)

_DEFAULT_LOOKBACK_DAYS = 365

#: How much of a provider or database error to keep in a result row.
#:
#: A failed bulk upsert carries the entire statement in its message - tens of
#: thousands of ``?`` placeholders plus every bound value. Printed in a results
#: table it buries the one line that says what went wrong, and on a 1,500-symbol
#: run it would do that once per symbol. The first sentence is the finding; the
#: rest belongs in the log.
_ERROR_SUMMARY_CHARS = 200


def _summarize(error: Exception) -> str:
    """Collapse an exception into one readable line."""
    text = " ".join(str(error).split())
    if len(text) <= _ERROR_SUMMARY_CHARS:
        return text
    return f"{text[:_ERROR_SUMMARY_CHARS]}… (全文はログを参照)"


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
        backfill: bool = False,
    ) -> None:
        """Wire the service to a provider and a database.

        Args:
            provider: Source of OHLCV bars.
            database: Persistence target.
            default_lookback_days: Backfill window used when a symbol has no
                stored data yet.
            backfill: Also extend symbols that **already** have data, when the
                lookback reaches further back than their oldest stored bar.
                Off by default so a nightly refresh stays incremental.
        """
        self.provider = provider
        self.database = database
        self.default_lookback_days = default_lookback_days
        self.backfill = backfill

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

        Raises:
            RateLimitError: **The one exception that is not captured.** A 429
                belongs to the run, not to the symbol: the symbol was never
                really attempted, and folding it into a failed result sends the
                caller straight into the same closed door with the next one.
                :class:`~stock_ai.data.bulk.BulkIngester` waits it out and, if
                it persists, stops. Observed live before this was wired through:
                a 429 partway into a 399-symbol backfill was recorded against
                every remaining symbol and 315 names "failed" in two minutes,
                none of them fetched.
        """
        end = end or dt.date.today()
        try:
            with self.database.session() as session:
                repo = PriceRepository(session)
                had_bars = repo.latest_date(symbol) is not None
                resolved_start = start or self._resolve_start(repo, symbol, end)
                if resolved_start > end:
                    logger.info("%s already up to date (%s)", symbol, end)
                    return IngestResult(symbol, 0, ok=True)

                try:
                    prices = self.provider.fetch_prices(symbol, resolved_start, end)
                except NoDataError:
                    # An empty answer means two different things, and only the
                    # caller knows which. For a symbol that already has bars it
                    # means "nothing new yet" - the normal state of every run
                    # before the session closes, and all day at a weekend.
                    # Calling that a failure makes the scheduled job report an
                    # error nearly every morning, and an alarm that fires daily
                    # is one nobody reads. For a symbol with no bars at all it
                    # is a real finding: the provider does not know the ticker.
                    if not had_bars:
                        raise
                    logger.info("%s: no new bars for %s..%s", symbol, resolved_start, end)
                    return IngestResult(symbol, 0, ok=True)

                rows = repo.upsert_prices(symbol, prices, market=market)
                logger.info("Ingested %d bars for %s", rows, symbol)
                return IngestResult(symbol, rows, ok=True)
        except RateLimitError:
            # **``RateLimitError`` は ``StockAIError`` の一種なので、下の except に
            # 先回りしてここで通す。** 順序を入れ替えると、レート制限が銘柄ごとの
            # 失敗に化けて、呼び出し側は閉じた扉に残り全部を叩きつける。
            raise
        except StockAIError as exc:
            logger.warning("Ingest failed for %s: %s", symbol, _summarize(exc))
            return IngestResult(symbol, 0, ok=False, error=_summarize(exc))
        except Exception as exc:  # provider/network errors must not abort a batch
            # The traceback is kept for DEBUG. At INFO it would print the whole
            # failed statement, which is how one bad upsert filled a console.
            logger.error(
                "Unexpected ingest error for %s: %s: %s",
                symbol,
                type(exc).__name__,
                _summarize(exc),
                exc_info=logger.isEnabledFor(logging.DEBUG),
            )
            detail = f"{type(exc).__name__}: {_summarize(exc)}"
            return IngestResult(symbol, 0, ok=False, error=detail)

    def ingest_many(
        self,
        symbols: list[str],
        start: dt.date | None = None,
        end: dt.date | None = None,
        market: str = "US",
    ) -> list[IngestResult]:
        """Ingest each symbol, continuing past individual failures.

        Raises:
            RateLimitError: A rate limit is not an individual failure - it
                belongs to the run - so it ends the batch instead of being
                collected. Re-run later; stored symbols are skipped.
        """
        return [self.ingest_symbol(sym, start, end, market) for sym in symbols]

    def _resolve_start(self, repo: PriceRepository, symbol: str, end: dt.date) -> dt.date:
        """Return the start date for ``symbol``: incremental, or a backfill.

        Incremental by default - the day after the latest stored bar - because
        a daily refresh must not re-download years it already holds, and on a
        1,500-symbol universe that difference is the whole rate-limit budget.

        ``backfill`` overrides it. Without that switch, ``--lookback`` applies
        only to symbols with no prices at all, so asking a universe that
        already holds four years for 5,000 days resolves every symbol to
        "already up to date": the run reports success and not one extra year
        arrives. Nothing raises, which is exactly why it goes unnoticed.

        Extending history is therefore opt-in rather than inferred. Inferring
        it from "the window is longer than what is stored" would also re-fetch
        a full year for every newly-added symbol on every nightly run, which
        turns a cheap job into a rate-limited one.
        """
        wanted = end - dt.timedelta(days=self.default_lookback_days)
        latest = repo.latest_date(symbol)
        if latest is None:
            return wanted

        if self.backfill:
            earliest = repo.earliest_date(symbol)
            if earliest is not None and wanted < earliest:
                logger.info(
                    "%s: extending history back from %s to %s (stored bars are kept)",
                    symbol,
                    earliest,
                    wanted,
                )
                return wanted
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
        except RateLimitError:
            raise  # 銘柄の失敗ではなく、実行そのものの失敗（上の説明と同じ理由）
        except StockAIError as exc:
            logger.warning("Fundamentals ingest failed for %s: %s", symbol, exc)
            return IngestResult(symbol, 0, ok=False, error=str(exc))
        except Exception as exc:  # provider/network errors must not abort a batch
            logger.exception("Unexpected fundamentals error for %s", symbol)
            return IngestResult(symbol, 0, ok=False, error=str(exc))

    def ingest_many(self, symbols: list[str], market: str = "US") -> list[IngestResult]:
        """Ingest each symbol's fundamentals, continuing past failures."""
        return [self.ingest_symbol(sym, market) for sym in symbols]
