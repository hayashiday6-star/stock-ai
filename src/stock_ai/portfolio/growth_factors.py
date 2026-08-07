"""Scoring factors built on the fiscal-period statement series.

These are the inputs a "what could compound many times over" screen needs, and
none of them are expressible from a single snapshot: sustained revenue growth,
earnings following it, profit retained rather than paid out, and a base small
enough that growth still moves the market cap.

A word on what this is not. A tenbagger score is a **heuristic, not a
prediction** - no weighting of trailing fundamentals identifies future
multi-baggers reliably, and survivorship bias makes any hand-tuned set look
better in hindsight than it was. The saving grace is that this repository has a
backtest engine: score a universe, hold the top decile, and compare against
buy-and-hold before trusting the ranking with money.
"""

from __future__ import annotations

from stock_ai.data.fx import FxConverter
from stock_ai.fundamental.growth import cagr, latest_payout_ratio, profit_growth, revenue_growth
from stock_ai.portfolio.factors import Factor, _clamp01
from stock_ai.portfolio.scoring import WeightedFactor
from stock_ai.screening.base import ScreeningContext


class RevenueGrowthFactor(Factor):
    """Faster revenue growth scores higher (default: 30% YoY = full marks)."""

    def __init__(self, target: float = 0.30, years: int = 1) -> None:
        """Store the growth rate that maps to a full score and the window."""
        self.target = target
        self.years = years

    @property
    def name(self) -> str:
        """Return ``revenue_growth``."""
        return "revenue_growth"

    def score(self, context: ScreeningContext) -> float | None:
        """Score trailing revenue growth against the target."""
        growth = revenue_growth(context.statements, self.years)
        return None if growth is None else _clamp01(growth / self.target)


class ProfitGrowthFactor(Factor):
    """Faster net income growth scores higher (default: 30% YoY = full marks)."""

    def __init__(self, target: float = 0.30, years: int = 1) -> None:
        """Store the growth rate that maps to a full score and the window."""
        self.target = target
        self.years = years

    @property
    def name(self) -> str:
        """Return ``profit_growth``."""
        return "profit_growth"

    def score(self, context: ScreeningContext) -> float | None:
        """Score trailing profit growth against the target."""
        growth = profit_growth(context.statements, self.years)
        return None if growth is None else _clamp01(growth / self.target)


class RevenueCagrFactor(Factor):
    """Sustained multi-year revenue CAGR scores higher.

    Preferred over a single year-over-year figure when judging durability: one
    exceptional year cannot carry a multi-year compound rate.
    """

    def __init__(self, target: float = 0.25, years: int = 3) -> None:
        """Store the CAGR that maps to a full score and the window length."""
        self.target = target
        self.years = years

    @property
    def name(self) -> str:
        """Return ``revenue_cagr``."""
        return "revenue_cagr"

    def score(self, context: ScreeningContext) -> float | None:
        """Score the multi-year revenue CAGR against the target."""
        rate = cagr(context.statements, "revenue", self.years)
        return None if rate is None else _clamp01(rate / self.target)


class ReinvestmentFactor(Factor):
    """Retaining earnings scores higher than paying them out.

    A company that compounds does so by reinvesting; a high payout is capital
    leaving the business. Scored as ``1 - payout``, so a zero-dividend grower
    gets full marks and a company paying out everything gets none.
    """

    @property
    def name(self) -> str:
        """Return ``reinvestment``."""
        return "reinvestment"

    def score(self, context: ScreeningContext) -> float | None:
        """Score retained earnings from the inverse of the payout ratio."""
        payout = latest_payout_ratio(context.statements)
        if payout is None:
            # Distinguish "pays nothing" from "we do not know". An explicit zero
            # dividend on positive earnings is real reinvestment and is caught
            # by latest_payout_ratio returning 0.0, not None.
            return None
        return _clamp01(1.0 - payout)


class SmallCapFactor(Factor):
    """Smaller companies score higher - growth still moves a small base.

    The market cap is converted before comparison: raw figures would rank every
    Japanese listing as enormous simply because yen numbers are ~150x larger,
    which is exactly the trap a cross-market small-cap screen must avoid.
    """

    def __init__(self, fx: FxConverter | None = None, ceiling: float = 5e9) -> None:
        """Store the converter and the size, in base currency, that scores zero."""
        self.fx = fx or FxConverter()
        self.ceiling = ceiling

    @property
    def name(self) -> str:
        """Return ``small_cap``."""
        return "small_cap"

    def score(self, context: ScreeningContext) -> float | None:
        """Score smallness from the converted market cap."""
        fundamentals = context.fundamentals
        if fundamentals is None or fundamentals.market_cap is None:
            return None
        converted = self.fx.convert_from_market(fundamentals.market_cap, context.market)
        if converted is None or converted <= 0:
            return None
        return _clamp01(1.0 - converted / self.ceiling)


def tenbagger_weighted_factors(
    fx: FxConverter | None = None, ceiling: float = 5e9
) -> list[WeightedFactor]:
    """Return a growth-and-smallness factor set for multi-bagger candidates.

    Weighted towards sustained growth (CAGR plus the latest year) over a single
    good quarter, with size and reinvestment as supporting evidence. Momentum is
    excluded deliberately: it measures what the market has already paid for.

    .. warning::
       **Tested on TSE (~1,400 names, 2022-2025) and no edge was found.**
       Walk-forward over 13 quarterly formation dates with a 252-bar hold:
       1 window cleared two sigma, median ``t = +0.21``, returns decayed across
       buckets in 5 of 13. Excess returns sat within a point of zero in every
       window but one.

       The one significant window (2024-06, ``t = +2.62``) was also the first
       date tested in isolation, where it read ``t = +2.78`` and looked like
       evidence. It was one draw out of thirteen. That is the entire reason
       :func:`~stock_ai.backtest.factor_test.walk_forward` exists.

       Two caveats that do not rescue it but bound the claim: a 252-bar hold is
       a short test for a thesis about multi-year compounding, and four years of
       history is a small sample. Neither is an argument for using the score -
       "not yet disproven over a longer horizon" is not an edge.

    Args:
        fx: Converter used to compare market caps across markets.
        ceiling: Market cap, in the converter's base currency, scoring zero on
            smallness.
    """
    return [
        (RevenueCagrFactor(), 0.30),
        (RevenueGrowthFactor(), 0.20),
        (ProfitGrowthFactor(), 0.20),
        (SmallCapFactor(fx=fx, ceiling=ceiling), 0.20),
        (ReinvestmentFactor(), 0.10),
    ]
