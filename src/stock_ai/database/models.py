"""SQLAlchemy 2.0 declarative models for persisted market data.

A :class:`Security` owns many :class:`PriceBar` rows. The ``(security_id, date)``
unique constraint is what makes price ingestion idempotent (upsert-able).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Date, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class Security(Base):
    """A tradable instrument identified by its provider-native symbol."""

    __tablename__ = "securities"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(128), default=None)
    market: Mapped[str] = mapped_column(String(8), default="US")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    bars: Mapped[list[PriceBar]] = relationship(
        back_populates="security",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class PriceBar(Base):
    """A single daily OHLCV bar for a :class:`Security`."""

    __tablename__ = "price_bars"
    __table_args__ = (UniqueConstraint("security_id", "date", name="uq_price_bars_security_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    security_id: Mapped[int] = mapped_column(
        ForeignKey("securities.id", ondelete="CASCADE"), index=True
    )
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    open: Mapped[float]
    high: Mapped[float]
    low: Mapped[float]
    close: Mapped[float]
    adj_close: Mapped[float]
    volume: Mapped[int]

    security: Mapped[Security] = relationship(back_populates="bars")


class FundamentalSnapshot(Base):
    """A point-in-time snapshot of fundamental metrics for a :class:`Security`."""

    __tablename__ = "fundamental_snapshots"
    __table_args__ = (
        UniqueConstraint("security_id", "as_of", name="uq_fundamentals_security_asof"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    security_id: Mapped[int] = mapped_column(
        ForeignKey("securities.id", ondelete="CASCADE"), index=True
    )
    as_of: Mapped[dt.date] = mapped_column(Date, index=True)
    per: Mapped[float | None] = mapped_column(default=None)
    pbr: Mapped[float | None] = mapped_column(default=None)
    roe: Mapped[float | None] = mapped_column(default=None)
    revenue: Mapped[float | None] = mapped_column(default=None)
    net_income: Mapped[float | None] = mapped_column(default=None)
    dividend_yield: Mapped[float | None] = mapped_column(default=None)
    market_cap: Mapped[float | None] = mapped_column(default=None)

    security: Mapped[Security] = relationship()
