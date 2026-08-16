"""Domain types shared across the data layer.

:class:`Fundamentals` is the canonical, provider-agnostic snapshot of a
company's key metrics. Every field is optional because providers frequently
omit individual figures for a given symbol or point in time.
"""

from __future__ import annotations

import datetime as dt
import hashlib
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


class Importance(StrEnum):
    """How much attention a disclosure deserves."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"

    @property
    def rank(self) -> int:
        """Sortable severity; ``UNKNOWN`` sits above ``LOW``.

        An item the classifier could not read is more worth a human glance than
        one it confidently judged routine.
        """
        return {"high": 3, "medium": 2, "unknown": 1, "low": 0}[self.value]


class Disclosure(BaseModel):
    """One news item or IR filing about a security."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    title: str
    body: str = ""
    published_on: dt.date | None = None
    url: str | None = None
    source: str = ""

    def as_text(self) -> str:
        """Return the item as one text block for summarization."""
        return f"{self.title}\n\n{self.body}".strip()

    @property
    def uid(self) -> str:
        """A stable identity used to avoid alerting on the same item twice.

        Derived from the content rather than a provider id, because the sources
        do not agree on one - and a title plus a date is what actually makes a
        disclosure the same disclosure.
        """
        seed = f"{self.symbol}|{self.published_on or ''}|{self.title}"
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


class SecurityProfile(BaseModel):
    """Descriptive attributes of a listing, as opposed to its numbers.

    Kept apart from :class:`Fundamentals` because it changes on a completely
    different clock: a company's sector is stable for years, while its metrics
    move daily.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    market: str = "US"
    name: str | None = None
    sector: str | None = None  # a canonical stock_ai.data.sectors.Sector value
    industry: str | None = None  # the provider's own finer label, verbatim


class HoldingRecord(BaseModel):
    """A position the user owns, with the cost basis it was built at."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    market: str = "US"
    quantity: float
    average_cost: float
    """Cost per share, in the listing market's currency."""

    def cost_basis(self) -> float:
        """Total amount paid for the position, in the listing currency."""
        return self.quantity * self.average_cost

    def market_value(self, price: float | None) -> float | None:
        """Position value at ``price``, or ``None`` when the price is unknown."""
        return None if price is None else self.quantity * price


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
    "is revenue growing, has the dividend been raised every year" - questions
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


class WatchEntry(BaseModel):
    """A watchlist entry: which security, why, and how loud it should be."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    market: str = "US"
    note: str | None = None
    min_importance: Importance = Importance.MEDIUM
    """Only disclosures at or above this level become alerts for this name."""
