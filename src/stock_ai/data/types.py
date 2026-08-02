"""Domain types shared across the data layer.

:class:`Fundamentals` is the canonical, provider-agnostic snapshot of a
company's key metrics. Every field is optional because providers frequently
omit individual figures for a given symbol or point in time.
"""

from __future__ import annotations

import datetime as dt

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
