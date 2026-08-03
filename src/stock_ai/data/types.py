"""Domain types shared across the data layer.

:class:`Fundamentals` is the canonical, provider-agnostic snapshot of a
company's key metrics. Every field is optional because providers frequently
omit individual figures for a given symbol or point in time.
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class Fundamentals(BaseModel):
    """A point-in-time snapshot of fundamental metrics for one security."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    as_of: dt.date

    per: float | None = None  # trailing price / earnings
    pbr: float | None = None  # price / book
    roe: float | None = None  # return on equity
    revenue: float | None = None  # total revenue
    net_income: float | None = None  # net income to common
    dividend_yield: float | None = None
    market_cap: float | None = None


class FiscalPeriod(StrEnum):
    """Which slice of a fiscal year a disclosure covers."""

    Q1 = "Q1"
    Q2 = "Q2"
    Q3 = "Q3"
    FY = "FY"


class FinancialReport(BaseModel):
    """One company's disclosed results for a single fiscal period.

    This is the *time series* counterpart to :class:`Fundamentals`. Where a
    snapshot answers "what are the ratios today", a series of reports answers
    "is revenue growing, has the dividend been raised every year" — questions
    that need a fiscal-period axis, not a fetch date.

    Raw figures are kept rather than only the derived ratios: the same
    disclosure feeds growth rates, payout ratios, and streak counts, and each
    of those needs a different combination of the underlying numbers.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    fiscal_year: int
    period: FiscalPeriod = FiscalPeriod.FY
    disclosed_on: dt.date | None = None

    revenue: float | None = None
    operating_income: float | None = None
    net_income: float | None = None
    equity: float | None = None
    eps: float | None = None
    bps: float | None = None
    dividend_per_share: float | None = None  # annual DPS for the period
    shares_outstanding: float | None = None

    @property
    def is_annual(self) -> bool:
        """Whether this report covers a full fiscal year."""
        return self.period is FiscalPeriod.FY

    @property
    def payout_ratio(self) -> float | None:
        """Dividend per share / EPS, or ``None`` when either is unusable.

        A non-positive EPS makes the ratio meaningless (a loss-making company
        paying a dividend is not "returning 0% of profit"), so it is reported
        as unknown rather than as a negative or infinite number.
        """
        if self.dividend_per_share is None or self.eps is None or self.eps <= 0:
            return None
        return self.dividend_per_share / self.eps
