"""Repository for reading and writing price data.

The repository operates on an injected :class:`~sqlalchemy.orm.Session` (the
caller owns the transaction, typically via ``Database.session()``). Writes are
idempotent: re-ingesting overlapping dates updates existing rows instead of
duplicating them.
"""

from __future__ import annotations

import datetime as dt
import functools
from collections.abc import Iterator
from typing import Any

import pandas as pd
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from stock_ai.data.schema import (
    ADJ_CLOSE,
    CLOSE,
    DATE,
    HIGH,
    LOW,
    OHLCV_COLUMNS,
    OPEN,
    VOLUME,
    split_adjusted,
)
from stock_ai.data.types import (
    Disclosure,
    FinancialReport,
    FiscalPeriod,
    Fundamentals,
    HoldingRecord,
    Importance,
    SecurityProfile,
    WatchEntry,
)
from stock_ai.database.models import (
    FinancialStatement,
    FundamentalSnapshot,
    Holding,
    PriceBar,
    Security,
    SeenDisclosure,
    WatchlistItem,
)

_UPSERT_COLUMNS = [OPEN, HIGH, LOW, CLOSE, ADJ_CLOSE, VOLUME]
_FUNDAMENTAL_COLUMNS = [
    "per",
    "pbr",
    "roe",
    "revenue",
    "net_income",
    "dividend_yield",
    "market_cap",
]
_STATEMENT_COLUMNS = [
    "disclosed_on",
    "revenue",
    "operating_income",
    "net_income",
    "equity",
    "eps",
    "bps",
    "dividend_per_share",
    "shares_outstanding",
]


def list_symbols(session: Session) -> list[str]:
    """Return all stored security symbols, sorted alphabetically."""
    return list(session.execute(select(Security.symbol).order_by(Security.symbol)).scalars().all())


def list_securities(session: Session) -> list[tuple[str, str]]:
    """Return ``(symbol, market)`` for every stored security, sorted by symbol.

    Cross-market work needs the listing market alongside the symbol - it is
    what selects the quote currency - so this is kept separate from the
    symbols-only :func:`list_symbols`.
    """
    rows = session.execute(select(Security.symbol, Security.market).order_by(Security.symbol)).all()
    return [(symbol, market) for symbol, market in rows]


def price_history_spans(session: Session) -> list[tuple[str, str, dt.date, dt.date, int]]:
    """Return ``(symbol, market, earliest, latest, bars)`` for every stored series.

    One grouped query rather than three per symbol: on a 1,500-name universe
    the per-symbol form is thousands of round trips to answer a question asked
    after every backfill.
    """
    rows = session.execute(
        select(
            Security.symbol,
            Security.market,
            func.min(PriceBar.date),
            func.max(PriceBar.date),
            func.count(PriceBar.id),
        )
        .join(PriceBar, PriceBar.security_id == Security.id)
        .group_by(Security.symbol, Security.market)
        .order_by(Security.symbol)
    ).all()
    return [
        (symbol, market, earliest, latest, int(bars))
        for symbol, market, earliest, latest, bars in rows
    ]


def upsert_profile(session: Session, profile: SecurityProfile) -> None:
    """Store descriptive attributes for a security, creating it if needed.

    Only fields the profile actually carries are written: a provider that omits
    the industry must not blank one another provider already supplied.
    """
    security = get_or_create_security(
        session, profile.symbol, market=profile.market, name=profile.name
    )
    for field in ("name", "sector", "industry"):
        value = getattr(profile, field)
        if value is not None:
            setattr(security, field, value)
    session.flush()


def get_profile(session: Session, symbol: str) -> SecurityProfile | None:
    """Return the stored profile for ``symbol``, or ``None`` if unknown."""
    security = session.execute(
        select(Security).where(Security.symbol == symbol)
    ).scalar_one_or_none()
    if security is None:
        return None
    return SecurityProfile(
        symbol=security.symbol,
        market=security.market,
        name=security.name,
        sector=security.sector,
        industry=security.industry,
    )


def get_or_create_security(
    session: Session, symbol: str, market: str = "US", name: str | None = None
) -> Security:
    """Return the :class:`Security` for ``symbol``, creating it if absent."""
    security = session.execute(
        select(Security).where(Security.symbol == symbol)
    ).scalar_one_or_none()
    if security is None:
        security = Security(symbol=symbol, market=market, name=name)
        session.add(security)
        session.flush()  # assign the primary key
    return security


#: 環境が何を返しても、これ以上は1文に詰めない。
#:
#: SQLite の上限はビルドによって違う。3.32 より前は 999、以降の既定は 32,766、
#: そして自前ビルドは 250,000 を返すこともある（この開発環境がそう）。実測値を
#: そのまま使うと、開発機では分割が起きず利用者の環境でだけ落ちる――実際その形で
#: 表面化した。立花の 6,278 行 × 8 列 = 50,224 個が Windows の 32,766 を超え、
#: J-Quants の5年分（約 9,600 個）では届いていなかった。
#:
#: どこでも同じ経路を通すために、報告値と この上限の小さい方を採る。
_PORTABLE_PARAMETER_CAP = 32766


@functools.cache
def max_bound_parameters() -> int:
    """SQLite が1文で受け付けるバインド変数の数。決め打ちにしない。

    ``with sqlite3.connect(...)`` は接続を閉じない。あれはトランザクションの
    文脈管理であって、クローズではない。閉じ忘れると呼ぶたびに接続が漏れる
    （テストが ResourceWarning を 467 件出して気付いた）。値はプロセス内で
    変わらないので、ついでに一度だけ調べる。
    """
    import contextlib
    import sqlite3

    try:
        with contextlib.closing(sqlite3.connect(":memory:")) as conn:
            reported = int(conn.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER))
    except (AttributeError, sqlite3.Error):  # pragma: no cover - 古い実装向けの保険
        return 999
    return min(reported, _PORTABLE_PARAMETER_CAP)


def chunked(records: list[dict[str, Any]], columns_per_row: int) -> Iterator[list[dict[str, Any]]]:
    """バインド変数の上限に収まる大きさに切って返す。

    余白を持たせるのは、実行時に SQLAlchemy が変数を足すことがあるため。
    """
    if not records:
        return
    limit = int(max_bound_parameters() * 0.9)
    size = max(1, limit // max(columns_per_row, 1))
    for start in range(0, len(records), size):
        yield records[start : start + size]


class PriceRepository:
    """Persist and query :class:`PriceBar` rows keyed by symbol and date."""

    def __init__(self, session: Session) -> None:
        """Bind the repository to an active session."""
        self.session = session

    def upsert_prices(self, symbol: str, prices: pd.DataFrame, market: str = "US") -> int:
        """Insert or update OHLCV bars for ``symbol``.

        Args:
            symbol: Provider-native ticker.
            prices: A canonical OHLCV frame (see :mod:`stock_ai.data.schema`).
            market: Market code stored on a newly created security.

        Returns:
            The number of rows written (inserted or updated).
        """
        if prices.empty:
            return 0

        security = get_or_create_security(self.session, symbol, market=market)
        frame = prices.reset_index()
        records = [
            {
                "security_id": security.id,
                "date": pd.Timestamp(row[DATE]).date(),
                OPEN: float(row[OPEN]),
                HIGH: float(row[HIGH]),
                LOW: float(row[LOW]),
                CLOSE: float(row[CLOSE]),
                ADJ_CLOSE: float(row[ADJ_CLOSE]),
                VOLUME: int(row[VOLUME]),
            }
            for row in frame.to_dict("records")
        ]

        # 1文にまとめない。SQLite にはバインド変数の上限があり、25年分の
        # 日足はそれを超える。
        for batch in chunked(records, len(_UPSERT_COLUMNS) + 2):
            stmt = sqlite_insert(PriceBar).values(batch)
            stmt = stmt.on_conflict_do_update(
                index_elements=["security_id", "date"],
                set_={col: getattr(stmt.excluded, col) for col in _UPSERT_COLUMNS},
            )
            self.session.execute(stmt)
        return len(records)

    def _load_bars(
        self,
        symbol: str,
        start: dt.date | None = None,
        end: dt.date | None = None,
    ) -> pd.DataFrame:
        """The stored bars exactly as written, unadjusted."""
        stmt = (
            select(PriceBar).join(Security).where(Security.symbol == symbol).order_by(PriceBar.date)
        )
        if start is not None:
            stmt = stmt.where(PriceBar.date >= start)
        if end is not None:
            stmt = stmt.where(PriceBar.date <= end)

        rows = self.session.execute(stmt).scalars().all()
        frame = pd.DataFrame(
            [
                {
                    DATE: row.date,
                    OPEN: row.open,
                    HIGH: row.high,
                    LOW: row.low,
                    CLOSE: row.close,
                    ADJ_CLOSE: row.adj_close,
                    VOLUME: row.volume,
                }
                for row in rows
            ],
            columns=[DATE, *OHLCV_COLUMNS],
        )
        frame[DATE] = pd.to_datetime(frame[DATE])
        return frame.set_index(DATE)

    def get_prices(
        self,
        symbol: str,
        start: dt.date | None = None,
        end: dt.date | None = None,
    ) -> pd.DataFrame:
        """Return stored bars for ``symbol``, on the split-adjusted basis.

        The adjustment happens here, not in the callers, because there are a
        dozen call sites and every strategy, indicator and the backtest engine
        reads ``close`` and ``open``. One forgotten call site is a backtest that
        reports a split as an 80% crash and says nothing about it - which is
        what was happening. See
        :func:`~stock_ai.data.schema.split_adjusted` for the measured effect.

        Nothing that reads the *current* price changes: the factor is
        ``adj_close / close``, and on the latest bar there is no later split, so
        it is 1.0. Only history moves, which is the point.
        """
        return split_adjusted(self._load_bars(symbol, start, end))

    def get_raw_prices(
        self,
        symbol: str,
        start: dt.date | None = None,
        end: dt.date | None = None,
    ) -> pd.DataFrame:
        """Return stored bars for ``symbol`` exactly as traded, unadjusted.

        Market capitalisation (shares outstanding times price) needs the
        actually-traded price, not :meth:`get_prices`'s split-adjusted one: a
        split changes the adjustment factor but not the real number of yen the
        market was paying for the company that day. Multiplying a split-
        adjusted close by an as-reported (never adjusted) share count would be
        the same combined-scale mistake :func:`~stock_ai.data.schema.
        split_adjusted` exists to prevent, just introduced from the other side.
        """
        return self._load_bars(symbol, start, end)

    def latest_date(self, symbol: str) -> dt.date | None:
        """Return the most recent stored bar date for ``symbol``, or ``None``."""
        return self.session.execute(
            select(PriceBar.date)
            .join(Security)
            .where(Security.symbol == symbol)
            .order_by(PriceBar.date.desc())
            .limit(1)
        ).scalar_one_or_none()

    def earliest_date(self, symbol: str) -> dt.date | None:
        """Return the oldest stored bar date for ``symbol``, or ``None``.

        Needed to tell "already up to date" from "up to date at the front and
        missing ten years at the back", which look identical from
        :meth:`latest_date` alone.
        """
        return self.session.execute(
            select(PriceBar.date)
            .join(Security)
            .where(Security.symbol == symbol)
            .order_by(PriceBar.date.asc())
            .limit(1)
        ).scalar_one_or_none()


class FundamentalsRepository:
    """Persist and query fundamentals snapshots keyed by symbol and date."""

    def __init__(self, session: Session) -> None:
        """Bind the repository to an active session."""
        self.session = session

    def upsert_fundamentals(self, fundamentals: Fundamentals, market: str = "US") -> None:
        """Insert or update the snapshot for its ``(symbol, as_of)``."""
        security = get_or_create_security(self.session, fundamentals.symbol, market=market)
        record = {
            "security_id": security.id,
            "as_of": fundamentals.as_of,
            **{col: getattr(fundamentals, col) for col in _FUNDAMENTAL_COLUMNS},
        }
        stmt = sqlite_insert(FundamentalSnapshot).values([record])
        stmt = stmt.on_conflict_do_update(
            index_elements=["security_id", "as_of"],
            set_={col: getattr(stmt.excluded, col) for col in _FUNDAMENTAL_COLUMNS},
        )
        self.session.execute(stmt)

    def get_latest(self, symbol: str, as_of: dt.date | None = None) -> Fundamentals | None:
        """Return the newest stored snapshot for ``symbol``, or ``None``.

        Args:
            symbol: The security.
            as_of: Ignore snapshots taken after this date. Pass the formation
                date in any historical test - a snapshot is stamped with the day
                it was *fetched*, so the newest one is by definition today's, and
                scoring a 2024 formation on today's market cap is look-ahead of
                the most flattering kind.
        """
        query = select(FundamentalSnapshot).join(Security).where(Security.symbol == symbol)
        if as_of is not None:
            query = query.where(FundamentalSnapshot.as_of <= as_of)
        row = self.session.execute(
            query.order_by(FundamentalSnapshot.as_of.desc()).limit(1)
        ).scalar_one_or_none()
        if row is None:
            return None
        return Fundamentals(
            symbol=symbol,
            as_of=row.as_of,
            **{col: getattr(row, col) for col in _FUNDAMENTAL_COLUMNS},
        )


class FinancialStatementRepository:
    """Persist and query the fiscal-period statement series for a security.

    Unlike :class:`FundamentalsRepository`, which keeps one snapshot per fetch
    date, this stores one row per *fiscal period*. That axis is what makes
    growth, dividend streaks, and payout history answerable.
    """

    def __init__(self, session: Session) -> None:
        """Bind the repository to an active session."""
        self.session = session

    def upsert_reports(
        self, symbol: str, reports: list[FinancialReport], market: str = "US"
    ) -> int:
        """Insert or update ``reports`` for ``symbol``, keyed by fiscal period.

        A restated disclosure for a period already stored overwrites it, so
        re-ingesting the same history is idempotent.

        **A value already stored is never replaced by a blank.** Two sources
        cover different columns for the same fiscal year: J-Quants carries EPS,
        BPS, the dividend and operating income, while EDINET's 「主要な経営指標等」
        carries none of them. A plain overwrite would mean that changing
        ``JP_STATEMENT_SOURCE`` silently erased the dividend history the
        dividend screens read - no error, just columns going empty. Incoming
        ``None`` therefore leaves the stored value alone.

        The cost is that a genuine correction *to* blank cannot be expressed
        here. Disclosures restate figures; they do not withdraw them into
        nothing, so that trade is one-sided in practice.

        Returns:
            The number of rows written.
        """
        if not reports:
            return 0

        security = get_or_create_security(self.session, symbol, market=market)
        # Later entries win if a payload repeats a period, matching the upsert.
        by_period = {(r.fiscal_year, str(r.period)): r for r in reports}
        records = [
            {
                "security_id": security.id,
                "fiscal_year": report.fiscal_year,
                "period": str(report.period),
                **{col: getattr(report, col) for col in _STATEMENT_COLUMNS},
            }
            for report in by_period.values()
        ]

        # 価格と同じ理由で分割する（:func:`chunked` 参照）。EDINET の XBRL から
        # 全期間を入れるようになれば、こちらも上限に届きうる。
        for batch in chunked(records, len(_STATEMENT_COLUMNS) + 3):
            stmt = sqlite_insert(FinancialStatement).values(batch)
            stmt = stmt.on_conflict_do_update(
                index_elements=["security_id", "fiscal_year", "period"],
                set_={
                    col: func.coalesce(
                        getattr(stmt.excluded, col), getattr(FinancialStatement, col)
                    )
                    for col in _STATEMENT_COLUMNS
                },
            )
            self.session.execute(stmt)
        return len(records)

    def get_reports(
        self, symbol: str, period: FiscalPeriod | None = FiscalPeriod.FY
    ) -> list[FinancialReport]:
        """Return ``symbol``'s statements oldest first.

        Args:
            symbol: The security to read.
            period: Restrict to one fiscal period type; ``None`` returns every
                period. Defaults to annual, which is what growth and dividend
                streak calculations compare - mixing quarters into a
                year-over-year series would silently corrupt it.
        """
        stmt = (
            select(FinancialStatement)
            .join(Security)
            .where(Security.symbol == symbol)
            .order_by(FinancialStatement.fiscal_year, FinancialStatement.period)
        )
        if period is not None:
            stmt = stmt.where(FinancialStatement.period == str(period))

        return [
            FinancialReport(
                symbol=symbol,
                fiscal_year=row.fiscal_year,
                period=FiscalPeriod(row.period),
                **{col: getattr(row, col) for col in _STATEMENT_COLUMNS},
            )
            for row in self.session.execute(stmt).scalars().all()
        ]

    def latest_fiscal_year(self, symbol: str) -> int | None:
        """Return the most recent stored annual fiscal year, or ``None``."""
        return self.session.execute(
            select(FinancialStatement.fiscal_year)
            .join(Security)
            .where(
                Security.symbol == symbol,
                FinancialStatement.period == str(FiscalPeriod.FY),
            )
            .order_by(FinancialStatement.fiscal_year.desc())
            .limit(1)
        ).scalar_one_or_none()


class HoldingRepository:
    """Persist and query the user's actual positions."""

    def __init__(self, session: Session) -> None:
        """Bind the repository to an active session."""
        self.session = session

    def set_holding(
        self, symbol: str, quantity: float, average_cost: float, market: str = "US"
    ) -> None:
        """Set ``symbol``'s position outright, replacing any existing one.

        A non-positive quantity removes the holding, so closing a position is
        expressible without a separate call.
        """
        if quantity <= 0:
            self.remove_holding(symbol)
            return

        security = get_or_create_security(self.session, symbol, market=market)
        stmt = sqlite_insert(Holding).values(
            [
                {
                    "security_id": security.id,
                    "quantity": quantity,
                    "average_cost": average_cost,
                }
            ]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["security_id"],
            set_={
                "quantity": stmt.excluded.quantity,
                "average_cost": stmt.excluded.average_cost,
            },
        )
        self.session.execute(stmt)

    def add_shares(self, symbol: str, quantity: float, price: float, market: str = "US") -> None:
        """Add ``quantity`` shares bought at ``price``, blending the cost basis."""
        existing = self.get_holding(symbol)
        if existing is None:
            self.set_holding(symbol, quantity, price, market=market)
            return

        total = existing.quantity + quantity
        if total <= 0:
            self.remove_holding(symbol)
            return
        blended = (existing.average_cost * existing.quantity + price * quantity) / total
        self.set_holding(symbol, total, blended, market=market)

    def remove_holding(self, symbol: str) -> None:
        """Delete ``symbol``'s position if one is stored."""
        row = self._row(symbol)
        if row is not None:
            self.session.delete(row)

    def get_holding(self, symbol: str) -> HoldingRecord | None:
        """Return ``symbol``'s position, or ``None`` if it is not held."""
        row = self._row(symbol)
        if row is None:
            return None
        return HoldingRecord(
            symbol=symbol,
            market=row.security.market,
            quantity=row.quantity,
            average_cost=row.average_cost,
        )

    def list_holdings(self) -> list[HoldingRecord]:
        """Return every stored position, sorted by symbol."""
        rows = (
            self.session.execute(select(Holding).join(Security).order_by(Security.symbol))
            .scalars()
            .all()
        )
        return [
            HoldingRecord(
                symbol=row.security.symbol,
                market=row.security.market,
                quantity=row.quantity,
                average_cost=row.average_cost,
            )
            for row in rows
        ]

    def _row(self, symbol: str) -> Holding | None:
        """Return the ORM row for ``symbol``, or ``None``."""
        return self.session.execute(
            select(Holding).join(Security).where(Security.symbol == symbol)
        ).scalar_one_or_none()


class WatchlistRepository:
    """Persist the watchlist and the disclosures already reported for it."""

    def __init__(self, session: Session) -> None:
        """Bind the repository to an active session."""
        self.session = session

    def add(
        self,
        symbol: str,
        note: str | None = None,
        min_importance: Importance = Importance.MEDIUM,
        market: str = "US",
    ) -> None:
        """Add ``symbol`` to the watchlist, updating it if already present."""
        security = get_or_create_security(self.session, symbol, market=market)
        stmt = sqlite_insert(WatchlistItem).values(
            [
                {
                    "security_id": security.id,
                    "note": note,
                    "min_importance": str(min_importance),
                }
            ]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["security_id"],
            set_={"note": stmt.excluded.note, "min_importance": stmt.excluded.min_importance},
        )
        self.session.execute(stmt)

    def remove(self, symbol: str) -> bool:
        """Drop ``symbol`` from the watchlist; ``True`` if it was there."""
        row = self.session.execute(
            select(WatchlistItem).join(Security).where(Security.symbol == symbol)
        ).scalar_one_or_none()
        if row is None:
            return False
        self.session.delete(row)
        return True

    def list_entries(self) -> list[WatchEntry]:
        """Return every watchlist entry, sorted by symbol."""
        rows = (
            self.session.execute(select(WatchlistItem).join(Security).order_by(Security.symbol))
            .scalars()
            .all()
        )
        return [
            WatchEntry(
                symbol=row.security.symbol,
                market=row.security.market,
                note=row.note,
                min_importance=Importance(row.min_importance),
            )
            for row in rows
        ]

    def is_seen(self, uid: str) -> bool:
        """Whether a disclosure with ``uid`` has already been reported."""
        return (
            self.session.execute(
                select(SeenDisclosure.id).where(SeenDisclosure.uid == uid).limit(1)
            ).scalar_one_or_none()
            is not None
        )

    def count_seen(self, symbol: str | None = None) -> int:
        """How many disclosures are on record as already reported."""
        stmt = select(func.count()).select_from(SeenDisclosure)
        if symbol is not None:
            stmt = stmt.where(
                SeenDisclosure.security_id.in_(
                    select(Security.id).where(Security.symbol == symbol.upper())
                )
            )
        return int(self.session.execute(stmt).scalar_one())

    def forget_seen(self, symbol: str | None = None) -> int:
        """Drop the seen record for ``symbol`` (or everything) and return the count.

        A seen disclosure is never fetched again, which is what stops a daily
        run re-delivering yesterday's news - and also what makes a bad pass
        permanent. A run that recorded verdicts it should not have leaves those
        filings invisible to every later run, and no amount of re-running
        recovers them. This is the way back.
        """
        removed = self.count_seen(symbol)
        stmt = delete(SeenDisclosure)
        if symbol is not None:
            stmt = stmt.where(
                SeenDisclosure.security_id.in_(
                    select(Security.id).where(Security.symbol == symbol.upper())
                )
            )
        self.session.execute(stmt)
        return removed

    def mark_seen(self, disclosure: Disclosure, importance: Importance, market: str = "US") -> None:
        """Record ``disclosure`` as reported so later runs skip it."""
        security = get_or_create_security(self.session, disclosure.symbol, market=market)
        stmt = sqlite_insert(SeenDisclosure).values(
            [
                {
                    "uid": disclosure.uid,
                    "security_id": security.id,
                    "title": disclosure.title[:512],
                    "importance": str(importance),
                }
            ]
        )
        # Two runs racing on the same item must not raise; first write wins.
        self.session.execute(stmt.on_conflict_do_nothing(index_elements=["uid"]))
