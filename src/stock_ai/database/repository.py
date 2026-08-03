"""Repository for reading and writing price data.

The repository operates on an injected :class:`~sqlalchemy.orm.Session` (the
caller owns the transaction, typically via ``Database.session()``). Writes are
idempotent: re-ingesting overlapping dates updates existing rows instead of
duplicating them.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
from sqlalchemy import select
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
)
from stock_ai.data.types import FinancialReport, FiscalPeriod, Fundamentals
from stock_ai.database.models import (
    FinancialStatement,
    FundamentalSnapshot,
    PriceBar,
    Security,
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

    Cross-market work needs the listing market alongside the symbol — it is
    what selects the quote currency — so this is kept separate from the
    symbols-only :func:`list_symbols`.
    """
    rows = session.execute(select(Security.symbol, Security.market).order_by(Security.symbol)).all()
    return [(symbol, market) for symbol, market in rows]


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

        stmt = sqlite_insert(PriceBar).values(records)
        stmt = stmt.on_conflict_do_update(
            index_elements=["security_id", "date"],
            set_={col: getattr(stmt.excluded, col) for col in _UPSERT_COLUMNS},
        )
        self.session.execute(stmt)
        return len(records)

    def get_prices(
        self,
        symbol: str,
        start: dt.date | None = None,
        end: dt.date | None = None,
    ) -> pd.DataFrame:
        """Return stored bars for ``symbol`` as a canonical OHLCV frame."""
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

    def latest_date(self, symbol: str) -> dt.date | None:
        """Return the most recent stored bar date for ``symbol``, or ``None``."""
        return self.session.execute(
            select(PriceBar.date)
            .join(Security)
            .where(Security.symbol == symbol)
            .order_by(PriceBar.date.desc())
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

    def get_latest(self, symbol: str) -> Fundamentals | None:
        """Return the most recent stored snapshot for ``symbol``, or ``None``."""
        row = self.session.execute(
            select(FundamentalSnapshot)
            .join(Security)
            .where(Security.symbol == symbol)
            .order_by(FundamentalSnapshot.as_of.desc())
            .limit(1)
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

        stmt = sqlite_insert(FinancialStatement).values(records)
        stmt = stmt.on_conflict_do_update(
            index_elements=["security_id", "fiscal_year", "period"],
            set_={col: getattr(stmt.excluded, col) for col in _STATEMENT_COLUMNS},
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
                streak calculations compare — mixing quarters into a
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
