"""Concrete, fundamentals-based screening conditions.

Each condition reads one metric from the context's :class:`Fundamentals`.
A missing metric (``None``) never passes: what cannot be verified is excluded.
Add a new condition by subclassing ``_MinThreshold`` or ``_MaxThreshold`` and
setting ``_metric`` / ``_label`` — no other code changes.
"""

from __future__ import annotations

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
