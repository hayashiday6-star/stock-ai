"""Scoring factors: each maps a candidate to a normalized sub-score in ``[0, 1]``.

Factors read from a :class:`~stock_ai.screening.base.ScreeningContext` (latest
fundamentals plus price history). A factor returns ``None`` when its input is
unavailable, so the scorer can renormalize over the factors that do apply.
Add a factor by subclassing :class:`Factor`; nothing else changes (OCP).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from stock_ai.data.schema import ADJ_CLOSE
from stock_ai.screening.base import ScreeningContext


def _clamp01(value: float) -> float:
    """Clamp ``value`` into the closed unit interval."""
    return max(0.0, min(1.0, value))


class Factor(ABC):
    """A normalized 0..1 sub-score over a screening context."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier used as the breakdown key."""

    @abstractmethod
    def score(self, context: ScreeningContext) -> float | None:
        """Return a sub-score in ``[0, 1]``, or ``None`` if not computable."""


class ROEFactor(Factor):
    """Higher return on equity scores higher (30% ROE = full marks)."""

    def __init__(self, target: float = 0.30) -> None:
        """Store the ROE that maps to a full score."""
        self.target = target

    @property
    def name(self) -> str:
        """Return ``roe``."""
        return "roe"

    def score(self, context: ScreeningContext) -> float | None:
        """Score ROE relative to the target."""
        if context.fundamentals is None or context.fundamentals.roe is None:
            return None
        return _clamp01(context.fundamentals.roe / self.target)


class ProfitMarginFactor(Factor):
    """Higher net margin (net income / revenue) scores higher."""

    def __init__(self, target: float = 0.25) -> None:
        """Store the margin that maps to a full score."""
        self.target = target

    @property
    def name(self) -> str:
        """Return ``profit_margin``."""
        return "profit_margin"

    def score(self, context: ScreeningContext) -> float | None:
        """Score the net profit margin relative to the target."""
        f = context.fundamentals
        if f is None or f.revenue in (None, 0) or f.net_income is None:
            return None
        return _clamp01((f.net_income / f.revenue) / self.target)


class ValueFactor(Factor):
    """Lower P/E scores higher (cheaper is better); non-positive P/E excluded."""

    def __init__(self, ceiling: float = 40.0) -> None:
        """Store the P/E ceiling beyond which the score is zero."""
        self.ceiling = ceiling

    @property
    def name(self) -> str:
        """Return ``value_per``."""
        return "value_per"

    def score(self, context: ScreeningContext) -> float | None:
        """Score cheapness from the trailing P/E."""
        f = context.fundamentals
        if f is None or f.per is None or f.per <= 0:
            return None
        return _clamp01(1.0 - f.per / self.ceiling)


class DividendFactor(Factor):
    """Higher dividend yield scores higher (6% = full marks)."""

    def __init__(self, target: float = 0.06) -> None:
        """Store the yield that maps to a full score."""
        self.target = target

    @property
    def name(self) -> str:
        """Return ``dividend``."""
        return "dividend"

    def score(self, context: ScreeningContext) -> float | None:
        """Score the dividend yield relative to the target."""
        if context.fundamentals is None or context.fundamentals.dividend_yield is None:
            return None
        return _clamp01(context.fundamentals.dividend_yield / self.target)


class MomentumFactor(Factor):
    """Higher trailing price momentum scores higher.

    Maps the return over the last ``lookback`` bars from ``-floor`` to ``+cap``
    onto ``[0, 1]`` (default: -20% → 0, +40% → 1).
    """

    def __init__(self, lookback: int = 126, floor: float = 0.20, cap: float = 0.40) -> None:
        """Store the lookback window and the return range to normalize over."""
        self.lookback = lookback
        self.floor = floor
        self.cap = cap

    @property
    def name(self) -> str:
        """Return ``momentum``."""
        return "momentum"

    def score(self, context: ScreeningContext) -> float | None:
        """Score trailing momentum from adjusted-close returns."""
        prices = context.prices
        if prices is None or ADJ_CLOSE not in prices.columns or len(prices) < 2:
            return None
        window = prices[ADJ_CLOSE].iloc[-self.lookback :]
        start = window.iloc[0]
        if start == 0:
            return None
        ret = window.iloc[-1] / start - 1.0
        return _clamp01((ret + self.floor) / (self.floor + self.cap))
