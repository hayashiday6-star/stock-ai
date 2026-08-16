"""Bulk ingestion across a whole universe, built to survive being interrupted.

Backfilling TSE Prime is ~1,600 symbols and one request each per data type. At
any realistic rate limit that is tens of minutes, over a network that will drop
at least once. So the shape of this module is dictated less by what to fetch
than by what happens when the run does not finish:

- **Resume is the default.** A symbol whose data is already current is skipped
  without a request, so re-running after a failure costs only the remainder.
  This works because the underlying writes are upserts and
  :class:`~stock_ai.data.service.IngestionService` already resolves an
  incremental start date.
- **One symbol's failure is one symbol's failure.** Errors are collected, never
  raised, so a delisted code or a momentary 500 cannot cost you the other 1,599.
- **Throttling is on by default.** Hammering a free-tier API until it blocks
  you is not faster.

Progress is reported through a callback rather than printed, so the CLI can
render a progress bar and a test can assert on the sequence.
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import SecretStr

from stock_ai.core.exceptions import RateLimitError
from stock_ai.core.logging import get_logger
from stock_ai.data.http import DEFAULT_RETRY_AFTER
from stock_ai.data.jquants_fundamentals import JQuantsFundamentalsProvider
from stock_ai.data.jquants_provider import JQuantsPriceProvider
from stock_ai.data.service import IngestionService
from stock_ai.data.types import SecurityProfile
from stock_ai.database.engine import Database
from stock_ai.database.repository import (
    FinancialStatementRepository,
    FundamentalsRepository,
    PriceRepository,
    upsert_profile,
)

logger = get_logger(__name__)

#: Called with (index, total, symbol) before each symbol is processed.
ProgressCallback = Callable[[int, int, str], None]

#: How much slower to go after each rate limit, and the ceiling on that.
_THROTTLE_GROWTH = 3.0
_MAX_THROTTLE_SECONDS = 10.0


class _RateLimitExhaustedError(Exception):
    """The provider kept refusing after every retry; the run must stop."""


class Dataset(StrEnum):
    """What to ingest for each symbol."""

    PRICES = "prices"
    STATEMENTS = "statements"


@dataclass
class BulkReport:
    """What a bulk run did, and what it could not do."""

    dataset: Dataset
    succeeded: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    """Already current, so not refetched."""
    failed: dict[str, str] = field(default_factory=dict)
    """Symbol -> error, for the ones worth retrying."""
    rows: int = 0
    rate_limited: int = 0
    """How many times the provider asked us to slow down."""
    aborted: str | None = None
    """Why the run stopped early, if it did. ``None`` means it covered everything."""

    @property
    def attempted(self) -> int:
        """Symbols that cost a request."""
        return len(self.succeeded) + len(self.failed)

    def summary(self) -> str:
        """A one-line summary suitable for a log or a console."""
        text = (
            f"{self.dataset.value}: {len(self.succeeded)} ok, "
            f"{len(self.skipped)} skipped, {len(self.failed)} failed, {self.rows} rows"
        )
        if self.rate_limited:
            text += f", rate limited {self.rate_limited}x"
        if self.aborted:
            text += f" - ABORTED: {self.aborted}"
        return text


def store_universe(database: Database, profiles: Sequence[SecurityProfile]) -> int:
    """Store the universe's profiles, creating any securities that are new.

    Run this before a bulk fetch: it is one request's worth of data that gives
    every later step a symbol list, a name, and a sector.
    """
    with database.session() as session:
        for profile in profiles:
            upsert_profile(session, profile)
    logger.info("Stored %d universe profile(s)", len(profiles))
    return len(profiles)


class BulkIngester:
    """Fetch one dataset across many symbols, resumably and politely."""

    def __init__(
        self,
        database: Database,
        api_key: SecretStr | None = None,
        throttle_seconds: float = 0.5,
        max_rate_limit_retries: int = 4,
        price_provider: object | None = None,
        statement_provider: object | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        """Wire the ingester.

        Args:
            database: Target database.
            api_key: J-Quants key used by the default providers.
            throttle_seconds: Pause between symbols. The default is deliberately
                non-zero; a free-tier key that gets rate-limited costs more time
                than the pause does. It is raised automatically on a 429 and
                never lowered again within a run.
            max_rate_limit_retries: How many times to wait out a 429 on one
                symbol before giving up on the whole run.
            price_provider: Overrides the default price provider (tests).
            statement_provider: Overrides the default statements provider (tests).
            sleeper: Overrides ``time.sleep`` (tests).
        """
        self.database = database
        self.throttle_seconds = max(0.0, throttle_seconds)
        self.max_rate_limit_retries = max(0, max_rate_limit_retries)
        self._prices = price_provider or JQuantsPriceProvider(api_key=api_key)
        # The snapshot's price comes from the database, not the network: prices
        # are loaded before statements in every bulk run, so it is already local
        # by the time it is needed. Without a price the snapshot still stores
        # ROE, revenue, and net income - just not PER, PBR, yield, or market cap.
        self._statements = statement_provider or JQuantsFundamentalsProvider(
            api_key=api_key, price_source=self._latest_close
        )
        self._sleep = sleeper or time.sleep

    def run(
        self,
        symbols: Sequence[str],
        dataset: Dataset,
        resume: bool = True,
        lookback_days: int = 365,
        progress: ProgressCallback | None = None,
        backfill: bool = False,
    ) -> BulkReport:
        """Ingest ``dataset`` for every symbol.

        Args:
            symbols: The universe to cover.
            dataset: Prices or statements.
            resume: Skip symbols whose data is already current. Turn off only to
                force a refetch - the skip check is what makes a re-run cheap.
            lookback_days: Backfill window for a symbol with no prices yet.
            progress: Called before each symbol with ``(index, total, symbol)``.
            backfill: Extend symbols that already hold data back to
                ``lookback_days``. Implies no resume skipping: a symbol current
                at the front is exactly the one whose history is short at the
                back, so skipping it would make the flag do nothing.

        Returns:
            A :class:`BulkReport`; individual failures are collected, not raised.
        """
        report = BulkReport(dataset=dataset)
        total = len(symbols)

        for index, symbol in enumerate(symbols, start=1):
            if progress is not None:
                progress(index, total, symbol)

            if resume and not backfill and self._is_current(symbol, dataset):
                report.skipped.append(symbol)
                continue

            try:
                rows = self._fetch_with_backoff(symbol, dataset, lookback_days, report, backfill)
            except _RateLimitExhaustedError as exc:
                # Nothing left to do but stop. Continuing would spend the rest
                # of the universe collecting the same refusal in seconds, which
                # is how a run "finishes" having fetched nothing.
                report.aborted = str(exc)
                logger.error("Bulk %s aborted at %s: %s", dataset.value, symbol, exc)
                break
            except Exception as exc:  # one symbol must not end the run
                logger.warning("Bulk %s failed for %s: %s", dataset.value, symbol, exc)
                report.failed[symbol] = str(exc)
            else:
                report.succeeded.append(symbol)
                report.rows += rows

            # Only pause after a request actually went out.
            if self.throttle_seconds:
                self._sleep(self.throttle_seconds)

        logger.info("Bulk run finished - %s", report.summary())
        return report

    def _fetch_with_backoff(
        self,
        symbol: str,
        dataset: Dataset,
        lookback_days: int,
        report: BulkReport,
        backfill: bool = False,
    ) -> int:
        """Ingest one symbol, waiting out any rate limit rather than failing it.

        A 429 says the *run* is going too fast. Recording it against the symbol
        and moving on is doubly wrong: the symbol was never really attempted,
        and the next request is issued immediately into the same closed door.

        Each refusal also slows the rest of the run permanently. Recovering the
        original pace would just walk back into the limit, and finishing slowly
        beats finishing empty.
        """
        for attempt in range(self.max_rate_limit_retries + 1):
            try:
                return self._ingest_one(symbol, dataset, lookback_days, backfill)
            except RateLimitError as exc:
                report.rate_limited += 1
                if attempt == self.max_rate_limit_retries:
                    raise _RateLimitExhaustedError(
                        f"still rate limited after {attempt + 1} attempts. "
                        f"{len(report.succeeded)} symbol(s) were loaded; re-run later "
                        "to continue from here."
                    ) from exc
                wait = (exc.retry_after or DEFAULT_RETRY_AFTER) * (2**attempt)
                self.throttle_seconds = min(
                    self.throttle_seconds * _THROTTLE_GROWTH, _MAX_THROTTLE_SECONDS
                )
                logger.warning(
                    "Rate limited on %s; waiting %.0fs (attempt %d/%d), "
                    "slowing to %.1fs between symbols.",
                    symbol,
                    wait,
                    attempt + 1,
                    self.max_rate_limit_retries,
                    self.throttle_seconds,
                )
                self._sleep(wait)
        raise AssertionError("unreachable")  # pragma: no cover

    def _ingest_one(
        self, symbol: str, dataset: Dataset, lookback_days: int, backfill: bool = False
    ) -> int:
        """Ingest one symbol, returning the rows written."""
        if dataset is Dataset.PRICES:
            service = IngestionService(
                self._prices,
                self.database,
                default_lookback_days=lookback_days,
                backfill=backfill,
            )
            result = service.ingest_symbol(symbol, market="JP")
            if not result.ok:
                raise RuntimeError(result.error or "ingest failed")
            return result.rows

        # One request, both products. The series answers "is revenue growing";
        # the snapshot answers "is it cheap", and the valuation screens read
        # only the snapshot table.
        snapshot, reports = self._statements.fetch_snapshot_and_statements(symbol)
        with self.database.session() as session:
            rows = FinancialStatementRepository(session).upsert_reports(
                symbol, reports, market="JP"
            )
            FundamentalsRepository(session).upsert_fundamentals(snapshot, market="JP")
        return rows

    def _latest_close(self, symbol: str) -> float | None:
        """Return the newest stored close, so the snapshot costs no extra request.

        Prices are loaded before statements in every bulk run, so by the time a
        snapshot is derived the price it needs is already local. A symbol with no
        stored price still gets a snapshot - just one without the ratios that
        need a price, which is the honest outcome rather than a failed fetch.
        """
        with self.database.session() as session:
            frame = PriceRepository(session).get_prices(symbol)
        if frame is None or frame.empty or "close" not in frame:
            return None
        close = frame["close"].dropna()
        return float(close.iloc[-1]) if not close.empty else None

    def _is_current(self, symbol: str, dataset: Dataset) -> bool:
        """Whether ``symbol`` already has today's data for ``dataset``.

        Prices count as current if the latest stored bar is from the last
        calendar day the market could have traded.

        Statements count as current only when *both* products of the request are
        stored. Checking the series alone would let a database written before
        snapshots were stored skip every symbol on re-run, leaving the valuation
        screens permanently empty with no way to notice.
        """
        with self.database.session() as session:
            if dataset is Dataset.PRICES:
                latest = PriceRepository(session).latest_date(symbol)
                return latest is not None and latest >= _last_possible_session()
            has_series = FinancialStatementRepository(session).latest_fiscal_year(symbol)
            has_snapshot = FundamentalsRepository(session).get_latest(symbol)
            return has_series is not None and has_snapshot is not None


def _last_possible_session() -> dt.date:
    """Return the most recent weekday, as the freshest bar that could exist.

    Weekends are handled but exchange holidays are not: on a holiday this
    returns a date with no bar, so those symbols are re-attempted. Re-attempting
    is the safe direction - the alternative is silently skipping a real update.
    """
    today = dt.date.today()
    if today.weekday() == 5:  # Saturday
        return today - dt.timedelta(days=1)
    if today.weekday() == 6:  # Sunday
        return today - dt.timedelta(days=2)
    return today
