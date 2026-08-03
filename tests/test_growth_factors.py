"""Tests for the growth/smallness factors behind the multi-bagger preset."""

from __future__ import annotations

import datetime as dt

import pytest

from stock_ai.data.fx import static_converter
from stock_ai.data.types import FinancialReport, Fundamentals
from stock_ai.portfolio.growth_factors import (
    ProfitGrowthFactor,
    ReinvestmentFactor,
    RevenueCagrFactor,
    RevenueGrowthFactor,
    SmallCapFactor,
    tenbagger_weighted_factors,
)
from stock_ai.portfolio.scoring import WeightedScorer
from stock_ai.screening.base import ScreeningContext

_JPY_USD = 1.0 / 150.0
_FX = static_converter("USD", JPY=_JPY_USD, USD=1.0)


def _series(rows: list[tuple[int, float, float, float, float]]) -> list[FinancialReport]:
    return [
        FinancialReport(
            symbol="X",
            fiscal_year=year,
            revenue=revenue,
            net_income=net_income,
            dividend_per_share=dps,
            eps=eps,
        )
        for year, revenue, net_income, dps, eps in rows
    ]


def _ctx(
    statements: list[FinancialReport] | None = None,
    market_cap: float | None = None,
    market: str = "US",
) -> ScreeningContext:
    fundamentals = (
        Fundamentals(symbol="X", as_of=dt.date(2024, 6, 30), market_cap=market_cap)
        if market_cap is not None
        else None
    )
    return ScreeningContext(
        symbol="X",
        market=market,
        fundamentals=fundamentals,
        statements=statements or [],
    )


_HYPER = _series(
    [(2021, 100, 5, 0, 5), (2022, 180, 12, 0, 12), (2023, 320, 25, 0, 25), (2024, 560, 48, 0, 48)]
)
_MATURE = _series(
    [
        (2021, 100, 20, 15, 20),
        (2022, 103, 21, 16, 21),
        (2023, 105, 21, 17, 21),
        (2024, 107, 22, 18, 22),
    ]
)


# --- individual factors -----------------------------------------------------


def test_revenue_growth_factor_scales_against_its_target() -> None:
    factor = RevenueGrowthFactor(target=0.30)
    # 560/320 - 1 = 0.75, well past the 30% target
    assert factor.score(_ctx(_HYPER)) == 1.0
    assert factor.score(_ctx(_MATURE)) == pytest.approx((107 / 105 - 1) / 0.30, rel=1e-3)


def test_profit_growth_factor_scales_against_its_target() -> None:
    factor = ProfitGrowthFactor(target=0.30)
    assert factor.score(_ctx(_HYPER)) == 1.0
    assert factor.score(_ctx(_MATURE)) < 0.3


def test_revenue_cagr_factor_uses_a_multi_year_window() -> None:
    factor = RevenueCagrFactor(target=0.25, years=3)
    # (560/100)^(1/3) - 1 ~= 0.79
    assert factor.score(_ctx(_HYPER)) == 1.0
    assert factor.score(_ctx(_MATURE)) == pytest.approx(
        ((107 / 100) ** (1 / 3) - 1) / 0.25, rel=1e-3
    )


def test_growth_factors_are_not_computable_without_statements() -> None:
    assert RevenueGrowthFactor().score(_ctx()) is None
    assert ProfitGrowthFactor().score(_ctx()) is None
    assert RevenueCagrFactor().score(_ctx()) is None


def test_reinvestment_rewards_retained_earnings() -> None:
    """A zero-dividend grower retains everything; a heavy payer retains little."""
    assert ReinvestmentFactor().score(_ctx(_HYPER)) == pytest.approx(1.0)
    # 18/22 paid out -> ~0.18 retained
    assert ReinvestmentFactor().score(_ctx(_MATURE)) == pytest.approx(1.0 - 18 / 22, rel=1e-3)


def test_reinvestment_is_unknown_when_the_payout_cannot_be_computed() -> None:
    loss_making = _series([(2024, 100, -10, 5, -10)])
    assert ReinvestmentFactor().score(_ctx(loss_making)) is None
    assert ReinvestmentFactor().score(_ctx()) is None


def test_small_cap_factor_converts_before_comparing() -> None:
    """Raw yen figures would rank every JP listing as enormous."""
    factor = SmallCapFactor(fx=_FX, ceiling=5e9)

    us_small = factor.score(_ctx(market_cap=3e8, market="US"))  # $300m
    jp_small = factor.score(_ctx(market_cap=4.5e10, market="JP"))  # ¥45bn -> $300m

    assert us_small == pytest.approx(1.0 - 3e8 / 5e9)
    assert jp_small == pytest.approx(us_small, rel=1e-6)


def test_small_cap_factor_floors_at_zero_for_mega_caps() -> None:
    assert SmallCapFactor(fx=_FX, ceiling=5e9).score(_ctx(market_cap=9e11)) == 0.0


def test_small_cap_factor_is_unknown_without_a_market_cap() -> None:
    factor = SmallCapFactor(fx=_FX)
    assert factor.score(_ctx()) is None
    assert factor.score(_ctx(market_cap=0.0)) is None


# --- the preset -------------------------------------------------------------


def test_preset_separates_small_growth_from_large_growth() -> None:
    """The default factor set cannot tell these apart; this one must."""
    scorer = WeightedScorer(tenbagger_weighted_factors(fx=_FX))

    small = scorer.score(_ctx(_HYPER, market_cap=3e8))
    large = scorer.score(_ctx(_HYPER, market_cap=9e11))

    assert small.score > large.score
    assert small.score == pytest.approx(98.8, abs=0.1)  # 0.3+0.2+0.2+0.2*0.94+0.1


def test_preset_ranks_a_mature_small_cap_below_a_growing_one() -> None:
    scorer = WeightedScorer(tenbagger_weighted_factors(fx=_FX))
    growing = scorer.score(_ctx(_HYPER, market_cap=3e8))
    mature = scorer.score(_ctx(_MATURE, market_cap=4e8))
    assert growing.score > mature.score * 2


def test_preset_scores_equivalent_jp_and_us_names_the_same() -> None:
    """Cross-market comparability is the point of converting the cap."""
    scorer = WeightedScorer(tenbagger_weighted_factors(fx=_FX))
    us = scorer.score(_ctx(_HYPER, market_cap=3e8, market="US"))
    jp = scorer.score(_ctx(_HYPER, market_cap=4.5e10, market="JP"))
    assert us.score == pytest.approx(jp.score, rel=1e-6)


def test_preset_weights_sum_to_one() -> None:
    assert sum(weight for _factor, weight in tenbagger_weighted_factors(fx=_FX)) == pytest.approx(
        1.0
    )


def test_preset_still_scores_when_statements_are_missing() -> None:
    """Only the size factor applies; the scorer renormalizes over what is left."""
    scorer = WeightedScorer(tenbagger_weighted_factors(fx=_FX))
    result = scorer.score(_ctx(market_cap=3e8))
    assert set(result.breakdown) == {"small_cap"}
    assert result.score == pytest.approx(100.0 * (1.0 - 3e8 / 5e9))
