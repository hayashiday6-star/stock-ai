"""Tests for the gap-continuation measurement.

The four properties this measurement has to get right are each pinned by a
test here, because each of them is the kind of thing that looks correct while
reading and is wrong in the numbers: whether the entry price could have been
reached, whether the in-sample boundary can be moved after the fact, whether
correlated trades are counted as independent, and whether the undecidable
branch is reached before anything is measured.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from stock_ai.backtest.gap_continuation import (
    GAP_MIN,
    IS_START,
    MIN_SYMBOLS,
    MIN_TRADES,
    VO_RATIO_MIN,
    ClusterCounts,
    _screen_inputs,
    benchmark_return,
    binding_sign_test,
    build_panel,
    cluster_counts,
    collapse_to_clusters,
    coverage_gate,
    describe,
    hill_test,
    price_the_trade,
    regime_ok,
    run_backtest,
    screen_day,
    sign_test,
    split_is_oos,
    sweep_shells,
    verdict,
)
from stock_ai.data.schema import ADJ_CLOSE, CLOSE, DATE, HIGH, LOW, OPEN, VOLUME


def _bars(
    dates: pd.DatetimeIndex,
    open_: list[float],
    high: list[float],
    low: list[float],
    close: list[float],
    volume: list[float],
    adj_close: list[float] | None = None,
) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            OPEN: open_,
            HIGH: high,
            LOW: low,
            CLOSE: close,
            ADJ_CLOSE: adj_close if adj_close is not None else close,
            VOLUME: volume,
        },
        index=dates,
    )
    frame.index.name = DATE
    return frame


def _flat_topix(dates: pd.DatetimeIndex, level: float = 2000.0) -> pd.DataFrame:
    frame = pd.DataFrame(
        {OPEN: level, HIGH: level, LOW: level, CLOSE: level},
        index=dates,
    )
    frame.index.name = DATE
    return frame


# ---------------------------------------------------------------------------
# 1. Was the entry price reachable when the signal fired?
# ---------------------------------------------------------------------------


def test_the_screen_reads_nothing_from_the_session_it_trades_in() -> None:
    """Rewriting the entry day cannot change which symbol the screen picks.

    This is the property that matters: if any input to the screen came from
    the session the position trades in, changing that session would change the
    signal. The test mutates the entry day beyond recognition and asserts the
    selection is byte-identical.
    """
    dates = pd.bdate_range("2024-03-01", periods=4)
    prices = {
        "AAAA": _bars(
            dates,
            open_=[1000.0, 1000.0, 1100.0, 1200.0],
            high=[1000.0, 1000.0, 1130.0, 1300.0],
            low=[1000.0, 1000.0, 1090.0, 1150.0],
            close=[1000.0, 1000.0, 1129.0, 1180.0],
            volume=[100_000, 100_000, 1_000_000, 1_000_000],
        )
    }
    inputs = _screen_inputs(build_panel(prices))
    before = screen_day(inputs, dates[2], dates[1])

    # Now make the entry session an outlier in every direction.
    wrecked = {
        "AAAA": _bars(
            dates,
            open_=[1000.0, 1000.0, 1100.0, 5.0],
            high=[1000.0, 1000.0, 1130.0, 5.0],
            low=[1000.0, 1000.0, 1090.0, 5.0],
            close=[1000.0, 1000.0, 1129.0, 5.0],
            volume=[100_000, 100_000, 1_000_000, 1],
        )
    }
    after = screen_day(_screen_inputs(build_panel(wrecked)), dates[2], dates[1])

    assert not before.empty
    pd.testing.assert_frame_equal(before, after)


def test_the_trade_is_priced_at_the_session_after_the_signal() -> None:
    """Entry is the next session's open; exit is that same session's close."""
    dates = pd.bdate_range("2024-03-01", periods=4)
    prices = {
        "AAAA": _bars(
            dates,
            open_=[100.0, 100.0, 110.0, 121.0],
            high=[100.0, 100.0, 111.0, 130.0],
            low=[100.0, 100.0, 109.0, 118.0],
            close=[100.0, 100.0, 111.0, 127.05],
            volume=[100_000, 100_000, 900_000, 900_000],
        )
    }
    panel = build_panel(prices)
    fill = price_the_trade(_screen_inputs(panel), panel, "AAAA", dates[3])

    assert fill.entry == pytest.approx(121.0)
    assert fill.exit_ == pytest.approx(127.05)
    assert fill.reason is None


def test_a_locked_session_is_flagged_rather_than_filled_silently() -> None:
    """An open that equals the high and the low never traded a range.

    A stock that gapped hard can open bid-limit the next morning with nothing
    printing. A backtest fills at that open regardless, so the row is marked
    instead of being quietly counted as an ordinary trade.
    """
    dates = pd.bdate_range("2024-03-01", periods=4)
    locked = {
        "AAAA": _bars(
            dates,
            open_=[100.0, 100.0, 110.0, 130.0],
            high=[100.0, 100.0, 111.0, 130.0],
            low=[100.0, 100.0, 109.0, 130.0],
            close=[100.0, 100.0, 111.0, 130.0],
            volume=[100_000, 100_000, 900_000, 10],
        )
    }
    panel = build_panel(locked)
    fill = price_the_trade(_screen_inputs(panel), panel, "AAAA", dates[3])
    assert fill.locked is True

    tradeable = {
        "AAAA": _bars(
            dates,
            open_=[100.0, 100.0, 110.0, 130.0],
            high=[100.0, 100.0, 111.0, 134.0],
            low=[100.0, 100.0, 109.0, 126.0],
            close=[100.0, 100.0, 111.0, 131.0],
            volume=[100_000, 100_000, 900_000, 500_000],
        )
    }
    panel = build_panel(tradeable)
    assert price_the_trade(_screen_inputs(panel), panel, "AAAA", dates[3]).locked is False


def test_a_split_between_the_two_sessions_is_not_read_as_a_gap() -> None:
    """A two-for-one halves the raw price; the screen must not see +/-50%.

    The production screener compares raw prices across this boundary. On the
    ex-date it reads a -50% gap, and on the volume side it reads a clean 2.0x
    on a session where nothing changed hands differently.
    """
    dates = pd.bdate_range("2024-03-01", periods=4)
    # Raw prices halve on the third bar; the adjusted series does not move.
    split = {
        "AAAA": _bars(
            dates,
            open_=[100.0, 100.0, 50.0, 50.0],
            high=[100.0, 100.0, 50.5, 51.0],
            low=[100.0, 100.0, 49.5, 49.0],
            close=[100.0, 100.0, 50.0, 50.0],
            volume=[100_000, 100_000, 200_000, 200_000],
            adj_close=[100.0, 100.0, 100.0, 100.0],
        )
    }
    inputs = _screen_inputs(build_panel(split))

    open_ = inputs.open_.at[dates[2], "AAAA"]
    prev_close = inputs.close.at[dates[1], "AAAA"]
    gap = (open_ - prev_close) / prev_close * 100.0
    assert gap == pytest.approx(0.0)

    ratio = inputs.volume.at[dates[2], "AAAA"] / inputs.volume.at[dates[1], "AAAA"]
    assert ratio == pytest.approx(1.0)


def test_the_benchmark_is_deducted_over_the_window_the_position_is_held() -> None:
    """TOPIX open-to-close on the entry day, not close-to-close."""
    dates = pd.bdate_range("2024-03-01", periods=3)
    topix = pd.DataFrame(
        {OPEN: [2000.0, 2000.0, 2000.0], CLOSE: [2000.0, 2000.0, 2040.0]},
        index=dates,
    )
    topix.index.name = DATE
    assert benchmark_return(topix, dates[2]) == pytest.approx(0.02)


def test_a_signal_on_the_last_session_has_nowhere_to_trade() -> None:
    """No entry session means no trade, not a trade priced at the signal."""
    dates = pd.bdate_range("2024-03-01", periods=3)
    prices = {
        "AAAA": _bars(
            dates,
            open_=[100.0, 100.0, 110.0],
            high=[100.0, 100.0, 111.0],
            low=[100.0, 100.0, 109.0],
            close=[100.0, 100.0, 111.0],
            volume=[100_000, 100_000, 900_000],
        )
    }
    trades = run_backtest(prices, _flat_topix(dates))
    assert trades.empty


def test_the_regime_gate_fails_closed_when_it_cannot_be_evaluated() -> None:
    """Fewer than five closes is not a green light.

    The production screener returns "bullish" when it cannot collect five
    benchmark closes, which silently disables the filter across holiday-heavy
    weeks.
    """
    dates = pd.bdate_range("2024-03-01", periods=6)
    topix = _flat_topix(dates)
    assert regime_ok(topix, dates[3]) is False
    assert regime_ok(topix, dates[4]) is True


def test_the_regime_gate_blocks_a_benchmark_under_its_own_mean() -> None:
    dates = pd.bdate_range("2024-03-01", periods=5)
    falling = pd.DataFrame(
        {OPEN: 100.0, HIGH: 100.0, LOW: 100.0, CLOSE: [110.0, 108.0, 106.0, 104.0, 100.0]},
        index=dates,
    )
    falling.index.name = DATE
    assert regime_ok(falling, dates[4]) is False


# ---------------------------------------------------------------------------
# 2. Is the IS/OOS boundary fixed in code?
# ---------------------------------------------------------------------------


def test_the_boundary_is_the_date_fixed_before_any_result() -> None:
    """Pinned so the split cannot be nudged without breaking the suite.

    The production parameters were fitted on 2024/10/01 onward, so anything
    before that date was unavailable to whoever chose them.
    """
    fitted_window_start = dt.date(2024, 10, 1)
    assert fitted_window_start == IS_START


def test_the_split_accepts_no_date_argument() -> None:
    """A boundary that can be passed in is a boundary that can be moved."""
    import inspect

    parameters = list(inspect.signature(split_is_oos).parameters)
    assert parameters == ["trades"]


def test_the_split_puts_the_fitted_window_in_sample() -> None:
    trades = pd.DataFrame(
        {
            "signal_date": [dt.date(2024, 9, 30), dt.date(2024, 10, 1), dt.date(2023, 5, 2)],
            "symbol": ["A", "B", "C"],
            "excess_return": [0.01, 0.02, 0.03],
        }
    )
    out_of_sample, in_sample = split_is_oos(trades)
    assert sorted(out_of_sample["symbol"]) == ["A", "C"]
    assert list(in_sample["symbol"]) == ["B"]


# ---------------------------------------------------------------------------
# 3. Are same-day signals counted as independent?
# ---------------------------------------------------------------------------


def test_ten_signals_on_one_morning_are_not_ten_observations() -> None:
    """The binding denominator is the scarcer clustering, not the trade count."""
    trades = pd.DataFrame(
        {
            "signal_date": [dt.date(2024, 5, 7)] * 10,
            "symbol": [f"S{i}" for i in range(10)],
            "excess_return": [0.01] * 10,
        }
    )
    counts = cluster_counts(trades)
    assert counts.trades == 10
    assert counts.symbols == 10
    assert counts.dates == 1
    assert counts.max_per_date == 10
    # Ten symbols, but they all share one morning's market move.
    assert counts.effective == 1


def test_a_cluster_collapses_to_one_observation_before_the_test() -> None:
    """Discounting the standard error is not the same as removing the overlap."""
    trades = pd.DataFrame(
        {
            "signal_date": [dt.date(2024, 5, 7)] * 3 + [dt.date(2024, 5, 8)],
            "symbol": ["A", "B", "C", "D"],
            "excess_return": [0.01, 0.03, 0.05, -0.02],
        }
    )
    collapsed = collapse_to_clusters(trades, "signal_date", "excess_return")
    assert collapsed.size == 2
    assert collapsed.loc[dt.date(2024, 5, 7)] == pytest.approx(0.03)
    assert collapsed.loc[dt.date(2024, 5, 8)] == pytest.approx(-0.02)


def test_the_binding_test_takes_the_weaker_of_the_two_clusterings() -> None:
    """One symbol firing on many days, and many symbols on one day, differ."""
    # Twenty distinct symbols, all on the same two mornings.
    trades = pd.DataFrame(
        {
            "signal_date": [dt.date(2024, 5, 7)] * 10 + [dt.date(2024, 5, 8)] * 10,
            "symbol": [f"S{i}" for i in range(20)],
            "excess_return": [0.01] * 20,
        }
    )
    by_symbol = sign_test(trades, "symbol")
    by_date = sign_test(trades, "signal_date")
    assert by_symbol.n == 20
    assert by_date.n == 2
    binding = binding_sign_test(trades)
    assert binding.unit == "signal_date"
    assert abs(binding.z) < abs(by_symbol.z)


def test_a_sign_test_on_an_all_positive_sample_scales_with_the_cluster_count() -> None:
    trades = pd.DataFrame(
        {
            "signal_date": [dt.date(2024, 5, 1) + dt.timedelta(days=i) for i in range(100)],
            "symbol": [f"S{i}" for i in range(100)],
            "excess_return": [0.01] * 100,
        }
    )
    result = sign_test(trades, "signal_date")
    assert result.n == 100
    assert result.share == pytest.approx(1.0)
    # (1.0 - 0.5) / sqrt(0.25/100) == 10
    assert result.z == pytest.approx(10.0)
    assert result.significant is True


# ---------------------------------------------------------------------------
# 4. Is the undecidable branch reached before anything is measured?
# ---------------------------------------------------------------------------


def _sample(n: int, symbols: int, value: float = 0.05) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "signal_date": [dt.date(2024, 1, 1) + dt.timedelta(days=i) for i in range(n)],
            "symbol": [f"S{i % symbols}" for i in range(n)],
            "excess_return": [value] * n,
            "gap_pct": [8.5] * n,
        }
    )


def test_a_thin_sample_is_undecidable_and_no_criterion_is_computed() -> None:
    """The gate runs first and returns before any return is looked at.

    Twelve trades all returning +5% would clear every threshold on the list.
    The point of the gate is that the list is never reached.
    """
    thin = _sample(12, 12)
    result = verdict(thin)

    assert result.undecidable
    assert result.outcome == "undecidable"
    assert result.distribution is None
    assert result.hill is None
    assert result.sign is None
    assert result.passed is None


def test_enough_trades_but_too_few_symbols_is_still_undecidable() -> None:
    """Both floors bind, not just the trade count."""
    concentrated = _sample(MIN_TRADES + 20, symbols=MIN_SYMBOLS - 1)
    result = verdict(concentrated)
    assert result.undecidable
    assert "symbols" in result.coverage.reason


def test_the_gate_reads_counts_and_never_a_return() -> None:
    """The same shape decides the same way whatever the returns are."""
    winners = _sample(40, 40, value=0.20)
    losers = _sample(40, 40, value=-0.20)
    assert coverage_gate(winners).sufficient is False
    assert coverage_gate(losers).sufficient is False
    assert coverage_gate(winners).reason == coverage_gate(losers).reason


def test_a_sufficient_sample_reports_all_three_criteria() -> None:
    ample = _sample(MIN_TRADES + 50, symbols=MIN_SYMBOLS + 10)
    result = verdict(ample)
    assert not result.undecidable
    assert result.distribution is not None
    assert result.sign is not None
    assert result.passed is not None
    assert len(result.passed) == 3


def test_one_failed_criterion_is_a_withdrawal() -> None:
    """All three must hold, as agreed before the numbers existed."""
    ample = _sample(MIN_TRADES + 50, symbols=MIN_SYMBOLS + 10)
    result = verdict(ample)
    # A constant series has a zero IQR, so criterion 1 cannot be met.
    assert result.passed is not None
    assert result.outcome == "withdraw"


# ---------------------------------------------------------------------------
# Shells and the hill test
# ---------------------------------------------------------------------------


def test_shells_are_disjoint_so_their_counts_sum_to_the_whole() -> None:
    """Nested thresholds share trades; these bands do not."""
    trades = pd.DataFrame(
        {
            "signal_date": [dt.date(2024, 1, 1) + dt.timedelta(days=i) for i in range(10)],
            "symbol": [f"S{i}" for i in range(10)],
            "gap_pct": [6.5, 6.9, 7.5, 7.1, 8.2, 8.8, 9.4, 9.9, 12.0, 25.0],
            "excess_return": [0.01] * 10,
        }
    )
    shells = sweep_shells(trades)
    assert list(shells["n"]) == [2, 2, 2, 2, 2]
    assert shells["n"].sum() == len(trades)


def test_an_isolated_spike_fails_the_hill_test() -> None:
    """A band whose neighbours collapse is noise, not a hilltop."""
    shells = pd.DataFrame(
        {
            "low": [6.0, 7.0, 8.0, 9.0, 10.0],
            "median_over_iqr": [0.01, 0.02, 0.40, 0.01, 0.02],
        }
    )
    result = hill_test(shells)
    assert result.is_hill is False
    assert "spike" in result.reason


def test_a_hill_passes_when_both_neighbours_reach_half() -> None:
    shells = pd.DataFrame(
        {
            "low": [6.0, 7.0, 8.0, 9.0, 10.0],
            "median_over_iqr": [0.10, 0.22, 0.30, 0.24, 0.12],
        }
    )
    result = hill_test(shells)
    assert result.is_hill is True
    assert result.left == pytest.approx(0.22)
    assert result.right == pytest.approx(0.24)


def test_a_band_under_water_is_not_a_hill_however_its_neighbours_look() -> None:
    """Half of a negative number is a lower bar than the number itself."""
    shells = pd.DataFrame(
        {
            "low": [6.0, 7.0, 8.0, 9.0, 10.0],
            "median_over_iqr": [-0.30, -0.20, -0.40, -0.20, -0.30],
        }
    )
    assert hill_test(shells).is_hill is False


def test_an_empty_neighbour_is_not_treated_as_passing() -> None:
    shells = pd.DataFrame(
        {
            "low": [6.0, 7.0, 8.0, 9.0, 10.0],
            "median_over_iqr": [0.10, 0.22, 0.30, float("nan"), 0.12],
        }
    )
    result = hill_test(shells)
    assert result.is_hill is False
    assert "empty" in result.reason


# ---------------------------------------------------------------------------
# Distribution reporting
# ---------------------------------------------------------------------------


def test_the_median_is_reported_beside_its_own_ratio() -> None:
    """A tight distribution can clear the ratio on a median too small to trade."""
    values = np.linspace(0.0012, 0.0028, 200)
    trades = pd.DataFrame(
        {
            "symbol": [f"S{i}" for i in range(200)],
            "excess_return": values,
        }
    )
    stats = describe(trades)
    assert stats.median_over_iqr > 0.10
    # ...on a median of roughly 0.2%, which a round trip would erase.
    assert stats.median < 0.003


def test_an_empty_frame_describes_without_raising() -> None:
    stats = describe(pd.DataFrame(columns=["excess_return"]))
    assert stats.n == 0
    assert np.isnan(stats.median)


def test_cluster_counts_on_an_empty_frame_are_zero() -> None:
    assert cluster_counts(pd.DataFrame()) == ClusterCounts(0, 0, 0, 0)


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


def test_a_qualifying_setup_produces_one_priced_trade() -> None:
    """A hand-built stock that meets every threshold, and the trade it makes."""
    dates = pd.bdate_range("2024-03-01", periods=8)
    # Day 5 is the signal: opens +10% on the prior close, closes above its
    # open, holds its high, on ten times the prior volume.
    prices = {
        "AAAA": _bars(
            dates,
            open_=[1000.0] * 5 + [1100.0, 1210.0, 1210.0],
            high=[1000.0] * 5 + [1130.0, 1250.0, 1210.0],
            low=[1000.0] * 5 + [1090.0, 1200.0, 1210.0],
            close=[1000.0] * 5 + [1129.0, 1240.0, 1210.0],
            volume=[100_000] * 5 + [1_000_000, 1_000_000, 1_000_000],
        )
    }
    topix = _flat_topix(dates)
    trades = run_backtest(prices, topix)

    assert len(trades) == 1
    trade = trades.iloc[0]
    assert trade["signal_date"] == dates[5].date()
    assert trade["entry_date"] == dates[6].date()
    assert trade["gap_pct"] == pytest.approx(10.0)
    assert trade["vo_ratio"] == pytest.approx(10.0)
    assert trade["entry_price"] == pytest.approx(1210.0)
    assert trade["exit_price"] == pytest.approx(1240.0)
    # Flat benchmark, so the excess return is the stock's own move.
    assert trade["excess_return"] == pytest.approx(1240.0 / 1210.0 - 1.0)


def test_the_production_thresholds_are_the_defaults() -> None:
    """The sweep is diagnostic; the measured strategy is the shipped one."""
    assert GAP_MIN == 8.0
    assert VO_RATIO_MIN == 5.0
