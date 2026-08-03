"""Weighted composite scoring: combine factors into a 0..100 score.

Weights are fully configurable - pass any ``(factor, weight)`` list. A factor
that returns ``None`` is dropped and the remaining weights are renormalized, so
a single missing metric never collapses the whole score to zero.
"""

from __future__ import annotations

from dataclasses import dataclass

from stock_ai.portfolio.factors import (
    DividendFactor,
    Factor,
    MomentumFactor,
    ProfitMarginFactor,
    ROEFactor,
    ValueFactor,
)
from stock_ai.screening.base import ScreeningContext

WeightedFactor = tuple[Factor, float]


@dataclass(frozen=True)
class ScoreResult:
    """A composite score with its per-factor breakdown."""

    symbol: str
    score: float  # 0..100
    breakdown: dict[str, float]  # factor name -> sub-score in [0, 1]


class WeightedScorer:
    """Combine weighted factors into a 0..100 score."""

    def __init__(self, weighted_factors: list[WeightedFactor]) -> None:
        """Store the ``(factor, weight)`` pairs to combine."""
        self.weighted_factors = weighted_factors

    def score(self, context: ScreeningContext) -> ScoreResult:
        """Score ``context``, renormalizing over the factors that apply."""
        breakdown: dict[str, float] = {}
        weighted_sum = 0.0
        total_weight = 0.0
        for factor, weight in self.weighted_factors:
            sub = factor.score(context)
            if sub is None:
                continue
            breakdown[factor.name] = sub
            weighted_sum += sub * weight
            total_weight += weight

        overall = 0.0 if total_weight == 0 else 100.0 * weighted_sum / total_weight
        return ScoreResult(symbol=context.symbol, score=overall, breakdown=breakdown)


def default_weighted_factors() -> list[WeightedFactor]:
    """Return the default factor set and weights (sum to 1.0)."""
    return [
        (ROEFactor(), 0.25),
        (ProfitMarginFactor(), 0.20),
        (ValueFactor(), 0.20),
        (DividendFactor(), 0.15),
        (MomentumFactor(), 0.20),
    ]
