"""Weighted composite scoring: combine factors into a 0..100 score.

Weights are fully configurable - pass any ``(factor, weight)`` list. A factor
that returns ``None`` is dropped and the remaining weights are renormalized, so
a single missing metric never collapses the whole score to zero.

**That renormalization is not free, and every score carries its coverage so the
cost is visible.** A name scored on two factors is graded out of those two: if
both are strong it reaches 100, while a name measured on all five must be
strong five times over to match it. The score is then partly a measure of how
little is known, and because ranking sorts descending, the least-documented
names float to the top. Observed live on a real ranking of 1,564 securities:
the top four rows all scored 99.9-100.0 with three of five factors missing,
beating a fully-measured name at 82.3.

So ``coverage`` is part of the result rather than an optional extra, and
:func:`~stock_ai.portfolio.ranking.rank_securities` filters on it by default.
A composite score without it is not comparable to another composite score.
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
    """A composite score with its per-factor breakdown and its coverage."""

    symbol: str
    score: float  # 0..100
    breakdown: dict[str, float]  # factor name -> sub-score in [0, 1]
    #: Fraction of the total factor weight that could actually be measured,
    #: in [0, 1]. ``1.0`` means every factor applied; ``0.35`` means the score
    #: is an average over roughly a third of the intended evidence.
    coverage: float = 0.0


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
        available = sum(weight for _, weight in self.weighted_factors)
        return ScoreResult(
            symbol=context.symbol,
            score=overall,
            breakdown=breakdown,
            coverage=0.0 if available == 0 else total_weight / available,
        )


def default_weighted_factors() -> list[WeightedFactor]:
    """Return the default factor set and weights (sum to 1.0)."""
    return [
        (ROEFactor(), 0.25),
        (ProfitMarginFactor(), 0.20),
        (ValueFactor(), 0.20),
        (DividendFactor(), 0.15),
        (MomentumFactor(), 0.20),
    ]
