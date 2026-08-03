"""Concrete, fundamentals-based screening conditions.

Each condition reads one metric from the context's :class:`Fundamentals`.
A missing metric (``None``) never passes: what cannot be verified is excluded.
Add a new condition by subclassing ``_MinThreshold`` or ``_MaxThreshold`` and
setting ``_metric`` / ``_label`` — no other code changes.
"""

from __future__ import annotations

from stock_ai.fundamental.growth import (
    consecutive_dividend_increases,
    dividend_growth,
    latest_payout_ratio,
    profit_growth,
    revenue_growth,
)
from stock_ai.screening.base import Condition, ScreeningContext


class _FundamentalThreshold(Condition):
    """Base for single-metric threshold conditions."""

    _metric: str
    _label: str

    def __init__(self, threshold: float) -> None:
        """Store the comparison threshold."""
        self.threshold = threshold

    def _metric_value(self, context: ScreeningContext) -> float | None:
        """Return the metric from the context, or ``None`` if unavailable."""
        if context.fundamentals is None:
            return None
        return getattr(context.fundamentals, self._metric)


class _MinThreshold(_FundamentalThreshold):
    """Passes when the metric is present and ``>= threshold``."""

    def evaluate(self, context: ScreeningContext) -> bool:
        """Return ``True`` if the metric is at least the threshold."""
        value = self._metric_value(context)
        return value is not None and value >= self.threshold

    def describe(self) -> str:
        """Describe as ``LABEL >= threshold``."""
        return f"{self._label} >= {self.threshold}"


class _MaxThreshold(_FundamentalThreshold):
    """Passes when the metric is present and ``<= threshold``."""

    def evaluate(self, context: ScreeningContext) -> bool:
        """Return ``True`` if the metric is at most the threshold."""
        value = self._metric_value(context)
        return value is not None and value <= self.threshold

    def describe(self) -> str:
        """Describe as ``LABEL <= threshold``."""
        return f"{self._label} <= {self.threshold}"


class MinROE(_MinThreshold):
    """Return on equity at or above the threshold."""

    _metric = "roe"
    _label = "ROE"


class MaxPER(_MaxThreshold):
    """Price/earnings at or below the threshold."""

    _metric = "per"
    _label = "PER"


class MaxPBR(_MaxThreshold):
    """Price/book at or below the threshold."""

    _metric = "pbr"
    _label = "PBR"


class MinDividendYield(_MinThreshold):
    """Dividend yield at or above the threshold."""

    _metric = "dividend_yield"
    _label = "DividendYield"


class MinMarketCap(_MinThreshold):
    """Market capitalisation at or above the threshold."""

    _metric = "market_cap"
    _label = "MarketCap"


# --- growth and dividend conditions (read the statement series) -------------


class _StatementThreshold(Condition):
    """Base for conditions computed from the annual statement series.

    A metric that cannot be computed — no statements loaded, too short a
    history, a loss-making base year — never passes, matching the rule the
    fundamentals conditions follow: what cannot be verified is excluded.
    """

    _label: str

    def __init__(self, threshold: float, years: int = 1) -> None:
        """Store the threshold and the number of fiscal years to look back."""
        self.threshold = threshold
        self.years = years

    def _measure(self, context: ScreeningContext) -> float | None:
        """Return the metric for ``context``, or ``None`` if not computable."""
        raise NotImplementedError

    def evaluate(self, context: ScreeningContext) -> bool:
        """Return ``True`` if the metric is present and at least the threshold."""
        value = self._measure(context)
        return value is not None and value >= self.threshold

    def describe(self) -> str:
        """Describe as ``LABEL(Ny) >= threshold``."""
        return f"{self._label}({self.years}y) >= {self.threshold}"


class MinRevenueGrowth(_StatementThreshold):
    """増収: revenue grew at least ``threshold`` over ``years`` fiscal years."""

    _label = "RevenueGrowth"

    def _measure(self, context: ScreeningContext) -> float | None:
        """Return revenue growth over the configured window."""
        return revenue_growth(context.statements, self.years)


class MinProfitGrowth(_StatementThreshold):
    """増益: net income grew at least ``threshold`` over ``years`` fiscal years."""

    _label = "ProfitGrowth"

    def _measure(self, context: ScreeningContext) -> float | None:
        """Return net income growth over the configured window."""
        return profit_growth(context.statements, self.years)


class MinDividendGrowth(_StatementThreshold):
    """増配: dividend per share grew at least ``threshold`` over ``years``."""

    _label = "DividendGrowth"

    def _measure(self, context: ScreeningContext) -> float | None:
        """Return dividend-per-share growth over the configured window."""
        return dividend_growth(context.statements, self.years)


class MinConsecutiveDividendIncreases(Condition):
    """連続増配: the dividend was raised at least ``years`` years running."""

    def __init__(self, years: int) -> None:
        """Store the minimum streak length."""
        self.years = years

    def evaluate(self, context: ScreeningContext) -> bool:
        """Return ``True`` if the dividend-increase streak is long enough."""
        return consecutive_dividend_increases(context.statements) >= self.years

    def describe(self) -> str:
        """Describe as ``ConsecutiveDividendIncreases >= years``."""
        return f"ConsecutiveDividendIncreases >= {self.years}"


class MaxPayoutRatio(Condition):
    """配当性向の上限: the payout ratio is known and at or below ``threshold``.

    An unknown ratio is excluded rather than assumed safe — a dividend whose
    sustainability cannot be checked is exactly the one worth checking.
    """

    def __init__(self, threshold: float) -> None:
        """Store the maximum acceptable payout ratio."""
        self.threshold = threshold

    def evaluate(self, context: ScreeningContext) -> bool:
        """Return ``True`` if the latest payout ratio is at most the threshold."""
        ratio = latest_payout_ratio(context.statements)
        return ratio is not None and ratio <= self.threshold

    def describe(self) -> str:
        """Describe as ``PayoutRatio <= threshold``."""
        return f"PayoutRatio <= {self.threshold}"


class MaxMarketCap(_MaxThreshold):
    """Market capitalisation at or below the threshold (小型株の絞り込み)."""

    _metric = "market_cap"
    _label = "MarketCap"
