"""Tests for calendar-month seasonality.

The point of most of these is not that the arithmetic is right - it is that the
module refuses to call noise a pattern. A seasonality scan that cannot stay
quiet on random data is worse than no scan, because its output looks identical
either way.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stock_ai.backtest.seasonality import (
    holdout_check,
    monthly_returns,
    scan_seasonality,
    symbol_patterns,
)
from stock_ai.data.schema import ADJ_CLOSE, CLOSE, HIGH, LOW, OPEN, VOLUME


def _prices(closes: pd.Series) -> pd.DataFrame:
    """Wrap a close series in the canonical OHLCV shape."""
    return pd.DataFrame(
        {
            OPEN: closes,
            HIGH: closes,
            LOW: closes,
            CLOSE: closes,
            ADJ_CLOSE: closes,
            VOLUME: 1_000,
        },
        index=closes.index,
    )


def _daily_index(years: int, start: str = "2016-01-01") -> pd.DatetimeIndex:
    return pd.date_range(start, periods=years * 365, freq="D", name="date")


def _seasonal_series(years: int, month: int, boost: float, seed: int = 0) -> pd.DataFrame:
    """Build a series that really does rise in ``month`` and drift otherwise."""
    index = _daily_index(years)
    rng = np.random.default_rng(seed)
    daily = rng.normal(0.0, 0.004, len(index))
    daily[index.month == month] += boost
    closes = pd.Series(100.0 * np.exp(np.cumsum(daily)), index=index)
    return _prices(closes)


def _random_series(years: int, seed: int) -> pd.DataFrame:
    index = _daily_index(years)
    rng = np.random.default_rng(seed)
    closes = pd.Series(
        100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.004, len(index)))), index=index
    )
    return _prices(closes)


# --- monthly returns --------------------------------------------------------


def test_monthly_returns_are_month_over_month() -> None:
    index = pd.date_range("2024-01-01", periods=90, freq="D", name="date")
    closes = pd.Series(np.linspace(100.0, 190.0, 90), index=index)
    returns = monthly_returns(_prices(closes))

    assert len(returns) == 2  # Jan has no prior month to measure against
    assert all(value > 0 for value in returns)


def test_an_empty_frame_yields_no_returns() -> None:
    assert monthly_returns(pd.DataFrame()).empty


def test_the_adjusted_close_is_preferred() -> None:
    """A split in an unadjusted series would read as a -50% month."""
    index = pd.date_range("2024-01-01", periods=90, freq="D", name="date")
    raw = pd.Series(np.r_[np.full(45, 200.0), np.full(45, 100.0)], index=index)
    frame = _prices(raw)
    frame[ADJ_CLOSE] = 100.0  # adjusted: no move at all

    assert monthly_returns(frame).abs().max() == pytest.approx(0.0)


# --- per-symbol patterns ----------------------------------------------------


def test_a_month_without_enough_years_is_not_reported() -> None:
    prices = _random_series(years=3, seed=1)
    assert symbol_patterns("X", monthly_returns(prices), min_years=8) == []


def test_every_reported_month_carries_its_sample_size() -> None:
    prices = _random_series(years=10, seed=2)
    patterns = symbol_patterns("X", monthly_returns(prices), min_years=4)

    assert patterns
    assert all(p.years >= 4 for p in patterns)
    assert {p.month for p in patterns} <= set(range(1, 13))


def test_a_real_seasonal_boost_is_found() -> None:
    """The scan must still detect a pattern that is genuinely there."""
    prices = _seasonal_series(years=12, month=9, boost=0.004, seed=3)
    patterns = symbol_patterns("X", monthly_returns(prices), min_years=4)
    september = next(p for p in patterns if p.month == 9)

    assert september.mean_return > 0
    assert september.t_stat > 2.0


# --- the null ---------------------------------------------------------------


def test_random_data_does_not_read_as_seasonality() -> None:
    """The test that matters: quiet on data with no calendar effect at all.

    Twenty independent random walks have no seasonality by construction, so the
    real scan and the shuffled one are measuring the same thing. A verdict that
    claimed a finding here would be the module failing at its only job.
    """
    prices = {f"S{i}": _random_series(years=10, seed=100 + i) for i in range(20)}
    scan = scan_seasonality(prices, min_years=4, permutations=10)

    assert scan.expected_hits is not None
    assert scan.tested > 200  # 20 symbols x 12 months, give or take
    # Observed hits should sit near the shuffled baseline, not far above it.
    assert len(scan.hits) <= scan.expected_hits * 2 + 5
    assert "chance" in scan.verdict.lower()


def test_the_verdict_reports_the_null_alongside_the_count() -> None:
    prices = {f"S{i}": _random_series(years=8, seed=200 + i) for i in range(10)}
    scan = scan_seasonality(prices, min_years=4, permutations=5)

    assert "shuffling" in scan.verdict
    assert str(len(scan.hits)) in scan.verdict


def test_without_permutations_the_verdict_refuses_to_conclude() -> None:
    prices = {"S0": _random_series(years=8, seed=7)}
    scan = scan_seasonality(prices, min_years=4, permutations=0)

    assert scan.expected_hits is None
    assert "supports nothing" in scan.verdict


def test_restricting_to_one_month_scales_the_null_with_it() -> None:
    """A twelfth of the tests must be compared against a twelfth of the null."""
    prices = {f"S{i}": _random_series(years=10, seed=300 + i) for i in range(10)}
    everything = scan_seasonality(prices, min_years=4, permutations=5)
    september = scan_seasonality(prices, month=9, min_years=4, permutations=5)

    assert september.expected_hits is not None
    assert everything.expected_hits is not None
    assert all(p.month == 9 for p in september.patterns)
    assert september.expected_hits < everything.expected_hits


def test_symbols_without_prices_are_counted_not_crashed_on() -> None:
    prices = {"GOOD": _random_series(years=6, seed=9), "EMPTY": pd.DataFrame()}
    scan = scan_seasonality(prices, min_years=4, permutations=2)

    assert scan.symbols_scanned == 1
    assert scan.symbols_skipped == 1


def test_a_universe_with_no_history_says_so() -> None:
    scan = scan_seasonality({"X": _random_series(years=2, seed=11)}, min_years=8)
    assert "No month had enough years" in scan.verdict


# --- holdout ----------------------------------------------------------------


def test_a_real_pattern_survives_the_holdout() -> None:
    prices = _seasonal_series(years=12, month=9, boost=0.004, seed=4)
    result = holdout_check("X", prices, month=9, split_year=2022)

    assert result is not None
    assert result.pattern.years >= 2
    assert result.holdout_years >= 1
    assert result.repeated


def test_the_holdout_needs_years_on_both_sides() -> None:
    prices = _random_series(years=4, seed=5)  # starts 2016
    assert holdout_check("X", prices, month=9, split_year=2030) is None
