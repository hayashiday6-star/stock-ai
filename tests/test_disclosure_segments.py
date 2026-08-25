"""Tests for the conditional cuts: timing, return window, magnitude, size.

Two of these questions cannot be checked against the exported labels - the
open-to-close window needs opening prices and the size cut needs shares and
volume, neither of which the CSV carries - so those paths are exercised on
constructed data with hand-chosen answers.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stock_ai.backtest.disclosure_segments import (
    CLOSE_TO_CLOSE,
    OPEN_TO_CLOSE,
    _stats,
    amplification,
    compare_return_windows,
    horizon_coverage,
    magnitude_bins,
    magnitude_by_size,
    monotonicity,
    quantile_bins,
    reconcile,
    summarize_by_timing,
    summarize_by_timing_and_direction,
)
from stock_ai.data.schema import (
    ADJ_CLOSE,
    CLOSE,
    DATE,
    HIGH,
    LOW,
    OPEN,
    VOLUME,
)


def _labeled(rows: list[dict[str, object]]) -> pd.DataFrame:
    """Build a labeled frame with the columns the segment functions read."""
    frame = pd.DataFrame(rows)
    for column in (
        "symbol",
        "timing",
        "revision_direction",
        CLOSE_TO_CLOSE,
        OPEN_TO_CLOSE,
        "revision_magnitude",
        "dividend_magnitude",
        "market_cap",
        "avg_trading_value",
    ):
        if column not in frame.columns:
            frame[column] = np.nan
    return frame


# ---------------------------------------------------------------------------
# 1. Timing
# ---------------------------------------------------------------------------


def test_an_unknown_disclosure_time_is_counted_apart_from_after_hours() -> None:
    """It is *measured* as after-hours, but must not be *reported* as one."""
    frame = _labeled(
        [
            {"symbol": "1", "timing": "after_hours", CLOSE_TO_CLOSE: 0.02},
            {"symbol": "2", "timing": "after_hours", CLOSE_TO_CLOSE: 0.04},
            {"symbol": "3", "timing": "intraday", CLOSE_TO_CLOSE: 0.01},
            {"symbol": "4", "timing": "time_unknown", CLOSE_TO_CLOSE: -0.05},
        ]
    )
    timing = summarize_by_timing(frame)
    by_bucket = timing.set_index("timing")["n"].to_dict()

    assert by_bucket == {"after_hours": 2, "intraday": 1, "time_unknown": 1}


def test_the_timing_split_covers_every_labeled_row() -> None:
    frame = _labeled(
        [
            {"symbol": str(i), "timing": t, CLOSE_TO_CLOSE: 0.01}
            for i, t in enumerate(["after_hours"] * 7 + ["intraday"] * 3 + ["time_unknown"] * 2)
        ]
    )
    total = int(frame[CLOSE_TO_CLOSE].notna().sum())
    check = reconcile("timing", total, summarize_by_timing(frame))

    assert check.balances
    assert check.counted == 12


def test_timing_and_direction_is_also_a_full_partition() -> None:
    rows = []
    for index in range(12):
        rows.append(
            {
                "symbol": str(index),
                "timing": "after_hours" if index % 2 else "intraday",
                "revision_direction": ["up", "flat", "down", "no_forecast"][index % 4],
                CLOSE_TO_CLOSE: 0.01,
            }
        )
    frame = _labeled(rows)
    check = reconcile("timing x direction", 12, summarize_by_timing_and_direction(frame))

    assert check.balances


def test_a_partition_that_loses_rows_is_reported_as_unexplained() -> None:
    """The check must fail loudly when a slice sheds rows with no reason given."""
    check = reconcile("lossy", 100, pd.DataFrame([{"n": 60}]))

    assert not check.balances
    assert "unexplained" in check.line


def test_a_conditional_table_reconciles_once_its_residual_is_named() -> None:
    check = reconcile(
        "magnitude",
        100,
        pd.DataFrame([{"n": 60}]),
        residual_reason="with no comparable revision",
    )

    assert check.balances
    assert "60 + 40 with no comparable revision = 100" in check.line


# ---------------------------------------------------------------------------
# 2. Which after-hours window
# ---------------------------------------------------------------------------


def test_the_window_comparison_uses_only_rows_carrying_both_figures() -> None:
    """Spreads taken over different row sets would compare the rows, not the windows."""
    frame = _labeled(
        [
            {
                "symbol": "1",
                "timing": "after_hours",
                "revision_direction": "up",
                CLOSE_TO_CLOSE: 0.05,
                OPEN_TO_CLOSE: 0.01,
            },
            {
                "symbol": "2",
                "timing": "after_hours",
                "revision_direction": "down",
                CLOSE_TO_CLOSE: -0.05,
                OPEN_TO_CLOSE: -0.01,
            },
            # Only one window present: must be excluded from both sides.
            {
                "symbol": "3",
                "timing": "after_hours",
                "revision_direction": "up",
                CLOSE_TO_CLOSE: 0.30,
                OPEN_TO_CLOSE: np.nan,
            },
            # Intraday has no meaningful open-to-close reading at all.
            {
                "symbol": "4",
                "timing": "intraday",
                "revision_direction": "up",
                CLOSE_TO_CLOSE: 0.09,
                OPEN_TO_CLOSE: np.nan,
            },
        ]
    )
    comparison = compare_return_windows(frame)

    assert comparison.rows_compared == 2
    assert comparison.close_to_close_spread == pytest.approx(0.10)
    assert comparison.open_to_close_spread == pytest.approx(0.02)


def test_the_gap_share_says_how_much_was_priced_before_the_open() -> None:
    frame = _labeled(
        [
            {
                "symbol": "1",
                "timing": "after_hours",
                "revision_direction": "up",
                CLOSE_TO_CLOSE: 0.10,
                OPEN_TO_CLOSE: 0.02,
            },
            {
                "symbol": "2",
                "timing": "after_hours",
                "revision_direction": "down",
                CLOSE_TO_CLOSE: -0.10,
                OPEN_TO_CLOSE: -0.02,
            },
        ]
    )
    comparison = compare_return_windows(frame)

    # 0.20 of spread close-to-close, 0.04 of it after the open: 80% in the gap.
    assert comparison.share_in_the_gap == pytest.approx(0.80)


# ---------------------------------------------------------------------------
# 3. Magnitude
# ---------------------------------------------------------------------------


def test_magnitude_bins_are_equal_count_and_ordered_by_signed_revision() -> None:
    rows = [
        {"symbol": str(i), "revision_magnitude": value, CLOSE_TO_CLOSE: value / 10}
        for i, value in enumerate(np.linspace(-0.5, 0.5, 50))
    ]
    binned = magnitude_bins(_labeled(rows), "revision_magnitude")

    assert len(binned) == 5
    assert binned["n"].sum() == 50
    assert binned["n"].tolist() == [10, 10, 10, 10, 10]
    assert binned["median"].is_monotonic_increasing


def test_monotonicity_counts_steps_rather_than_comparing_the_ends() -> None:
    """A shape that dips in the middle is not a dose response, however wide."""
    dips = pd.DataFrame({"median": [0.0, 0.05, 0.01, 0.06, 0.10]})
    verdict = monotonicity(dips)

    assert not verdict.is_monotone
    assert verdict.steps_up == 3
    assert verdict.steps_total == 4
    assert "reverses in the middle" in verdict.verdict


def test_monotonicity_reports_a_clean_gradient_as_monotone() -> None:
    rising = pd.DataFrame({"median": [-0.02, -0.01, 0.0, 0.02, 0.03]})
    verdict = monotonicity(rising)

    assert verdict.is_monotone
    assert verdict.steps_up == 4
    assert verdict.span == pytest.approx(0.05)


def test_the_outer_bins_are_labelled_open_ended() -> None:
    """A revision off a near-zero base gives a ratio in the thousands of percent.

    Printing that as a bin edge reads as broken arithmetic rather than as one
    filer's tiny denominator, so the outer bins say "at most" and "at least".
    """
    values = pd.Series([-90.0, -0.1, -0.02, 0.0, 0.02, 0.1, 400.0])
    _placed, labels = quantile_bins(values, 3)

    assert labels[0].startswith("≤")
    assert labels[-1].startswith("≥")
    assert "9000" not in labels[0]


def test_magnitude_bins_ignore_rows_with_no_revision() -> None:
    rows = [
        {"symbol": "1", "revision_magnitude": -0.2, CLOSE_TO_CLOSE: -0.02},
        {"symbol": "2", "revision_magnitude": 0.2, CLOSE_TO_CLOSE: 0.02},
        {"symbol": "3", "revision_magnitude": np.nan, CLOSE_TO_CLOSE: 0.5},
    ]
    binned = magnitude_bins(_labeled(rows), "revision_magnitude", bins=2)

    assert binned["n"].sum() == 2  # the unrevised row contributes nowhere


# ---------------------------------------------------------------------------
# 4. Size
# ---------------------------------------------------------------------------


def _amplifying_universe() -> pd.DataFrame:
    """Small caps react twice as hard to the identical revision.

    Built so the answer is known: the reaction is magnitude x 0.2 for large
    companies and magnitude x 0.4 for small ones, with the middle between.
    """
    rows = []
    caps = {"small": 1e9, "mid": 1e11, "large": 1e13}
    gains = {"small": 0.4, "mid": 0.3, "large": 0.2}
    for bucket, cap in caps.items():
        for index, magnitude in enumerate(np.linspace(-0.5, 0.5, 40)):
            rows.append(
                {
                    "symbol": f"{bucket}-{index}",
                    "revision_magnitude": magnitude,
                    "market_cap": cap,
                    CLOSE_TO_CLOSE: magnitude * gains[bucket],
                }
            )
    return _labeled(rows)


def test_a_size_effect_shows_as_a_wider_spread_in_the_small_column() -> None:
    by_size = magnitude_by_size(_amplifying_universe(), "revision_magnitude", "market_cap")
    spreads = amplification(by_size).set_index("size")["spread"]

    assert spreads["small"] > spreads["mid"] > spreads["large"]
    # Constructed as 0.4 / 0.3 / 0.2 of the magnitude span.
    assert spreads["small"] / spreads["large"] == pytest.approx(2.0, rel=0.05)


def test_no_size_effect_shows_as_equal_spreads() -> None:
    """The negative case has to be distinguishable, or the test proves nothing."""
    rows = []
    for bucket, cap in {"small": 1e9, "mid": 1e11, "large": 1e13}.items():
        for index, magnitude in enumerate(np.linspace(-0.5, 0.5, 40)):
            rows.append(
                {
                    "symbol": f"{bucket}-{index}",
                    "revision_magnitude": magnitude,
                    "market_cap": cap,
                    CLOSE_TO_CLOSE: magnitude * 0.3,
                }
            )
    by_size = magnitude_by_size(_labeled(rows), "revision_magnitude", "market_cap")
    spreads = amplification(by_size).set_index("size")["spread"]

    assert spreads["small"] == pytest.approx(spreads["large"], rel=0.02)


def test_size_columns_carry_their_own_counts_so_a_thin_cell_is_visible() -> None:
    by_size = magnitude_by_size(_amplifying_universe(), "revision_magnitude", "market_cap")

    for name in ["small", "mid", "large"]:
        assert f"{name} n" in by_size.columns
    assert by_size[[f"{n} n" for n in ["small", "mid", "large"]]].to_numpy().sum() == 120


def test_size_bins_are_named_by_rank_not_by_a_percentage_range() -> None:
    """The quantile labeller formats percentages, which is wrong for yen."""
    by_size = magnitude_by_size(_amplifying_universe(), "revision_magnitude", "market_cap")

    assert [c for c in by_size.columns if not c.endswith(" n") and c != "bin"] == [
        "small",
        "mid",
        "large",
    ]


# ---------------------------------------------------------------------------
# Regressions found by running the command end to end
# ---------------------------------------------------------------------------


def test_monotonicity_pairs_offset_sequences_of_unequal_length() -> None:
    """Adjacent pairs are offset by one, so the halves differ in length.

    Zipping them strictly raises, which took out every magnitude verdict.
    """
    verdict = monotonicity(pd.DataFrame({"median": [0.1, 0.2, 0.3]}))

    assert verdict.steps_total == 2
    assert verdict.is_monotone


def test_a_tie_heavy_series_bins_without_raising() -> None:
    """Dropping duplicate edges can leave a value in no bin at all.

    Looking that value up in the label map raised KeyError: nan and killed
    the size table. Such a row belongs to no bin, and the counts say so.
    """
    values = pd.Series([5.0] * 20 + [7.0])
    placed, labels = quantile_bins(values, 3)

    assert len(labels) >= 1
    assert placed.notna().sum() <= len(values)  # some rows may fall outside


def test_size_columns_are_readable_by_label_not_by_position() -> None:
    """`itertuples` renames "small n" because it is not an identifier.

    The renderer looks the counts up by label, so the frame has to be walked
    with something that preserves them.
    """
    by_size = magnitude_by_size(_amplifying_universe(), "revision_magnitude", "market_cap")

    for _index, row in by_size.iterrows():
        assert "small n" in row.index
        assert int(row["small n"]) >= 0


def test_a_missing_side_gives_no_spread_rather_than_a_nan() -> None:
    """With no down row the spread is undefined, and must be reported as such."""
    frame = _labeled(
        [
            {
                "symbol": "1",
                "timing": "after_hours",
                "revision_direction": "up",
                CLOSE_TO_CLOSE: 0.05,
                OPEN_TO_CLOSE: 0.01,
            }
        ]
    )
    comparison = compare_return_windows(frame)

    assert np.isnan(comparison.close_to_close_spread)
    assert np.isnan(comparison.share_in_the_gap)


def test_unrevised_rows_are_kept_out_of_the_magnitude_bins() -> None:
    """A spike of zero-magnitude holds collapses the quantiles.

    Measured on the TSE run: with holds included, the five requested bins
    came back as three and every cut shared the bottom bin with every hold,
    reporting a 3.45-point span where the revisions alone show 5.57.
    """
    revisions = [
        {"symbol": f"r{i}", "revision_magnitude": value, CLOSE_TO_CLOSE: value / 10}
        for i, value in enumerate(np.linspace(-0.5, 0.5, 50))
        if value != 0
    ]
    holds = [
        {"symbol": f"h{i}", "revision_magnitude": 0.0, CLOSE_TO_CLOSE: -0.005} for i in range(200)
    ]
    frame = _labeled(revisions + holds)

    contaminated = magnitude_bins(frame, "revision_magnitude", exclude_unrevised=False)
    clean = magnitude_bins(frame, "revision_magnitude", exclude_unrevised=True)

    assert len(clean) == 5
    assert len(contaminated) < len(clean)  # the spike collapsed the edges
    assert clean["n"].sum() == len(revisions)  # every hold excluded, every revision kept
    # And the gradient the collapse was hiding is wider.
    assert monotonicity(clean).span > monotonicity(contaminated).span


def test_the_size_cross_also_excludes_unrevised_rows() -> None:
    """Otherwise the bottom row is contaminated in every size column at once."""
    rows = []
    for bucket, cap in {"small": 1e9, "mid": 1e11, "large": 1e13}.items():
        for index, magnitude in enumerate(np.linspace(-0.5, 0.5, 40)):
            rows.append(
                {
                    "symbol": f"{bucket}-{index}",
                    "revision_magnitude": magnitude,
                    "market_cap": cap,
                    CLOSE_TO_CLOSE: magnitude * 0.3,
                }
            )
        for index in range(60):
            rows.append(
                {
                    "symbol": f"{bucket}-hold-{index}",
                    "revision_magnitude": 0.0,
                    "market_cap": cap,
                    CLOSE_TO_CLOSE: -0.01,
                }
            )
    by_size = magnitude_by_size(_labeled(rows), "revision_magnitude", "market_cap")
    counted = by_size[[f"{n} n" for n in ["small", "mid", "large"]]].to_numpy().sum()

    # 120 revisions kept, all 180 holds dropped. linspace over an even count
    # never lands exactly on zero, so no revision is caught by the filter.
    assert counted == 120


# ---------------------------------------------------------------------------
# Holding horizons measured from the next open
# ---------------------------------------------------------------------------


def _price_frame(closes: list[float], adj: list[float] | None = None) -> pd.DataFrame:
    """Daily bars on consecutive business days; open equals the prior close."""

    days = pd.bdate_range("2024-01-01", periods=len(closes))
    opens = [closes[0]] + closes[:-1]
    frame = pd.DataFrame(
        {
            OPEN: opens,
            HIGH: closes,
            LOW: opens,
            CLOSE: closes,
            ADJ_CLOSE: adj if adj is not None else closes,
            VOLUME: [1_000_000] * len(closes),
        },
        index=days,
    )
    frame.index.name = DATE
    return frame


def _flat_topix(length: int) -> pd.DataFrame:
    days = pd.bdate_range("2024-01-01", periods=length)
    frame = pd.DataFrame({OPEN: 2000.0, CLOSE: 2000.0}, index=days)
    frame.index.name = DATE
    return frame


def test_a_horizon_counts_holding_days_inclusive_of_the_base_session() -> None:
    """+1d ends at the base day's own close, not the next one."""
    from stock_ai.backtest.disclosure_impact import _forward_excess

    # Open 100 on day 0, then closes 110, 120, 130, 140, 150.
    stock = _price_frame([100.0, 110.0, 120.0, 130.0, 140.0, 150.0])
    topix = _flat_topix(6)
    base = stock.index[1]  # open here is 100 (the prior close)

    one = _forward_excess(base, 1, True, stock, topix)
    five = _forward_excess(base, 5, True, stock, topix)

    # One day held: open 100 -> close 110 on the same session.
    assert one == pytest.approx(0.10)
    # Five days held: open 100 -> close of the fourth session after it, 150.
    assert five == pytest.approx(0.50)


def test_a_split_inside_the_window_is_not_booked_as_a_return() -> None:
    """A 2-for-1 halves the raw prices; the adjusted series does not move.

    Pairing a raw open against an adjusted close across that boundary would
    report roughly -50% where nothing happened.
    """
    from stock_ai.backtest.disclosure_impact import _forward_excess

    # Unsplit: a clean +10% over the window.
    plain_closes = [100.0, 100.0, 105.0, 110.0]
    plain = _price_frame(plain_closes)

    # Same economics, but a 2-for-1 lands on the third bar: raw halves,
    # adj_close keeps the unsplit scale.
    split_raw = [100.0, 100.0, 52.5, 55.0]
    split_adj = [100.0, 100.0, 105.0, 110.0]
    split = _price_frame(split_raw, adj=split_adj)

    topix = _flat_topix(4)
    base = plain.index[1]

    assert _forward_excess(base, 3, True, plain, topix) == pytest.approx(0.10)
    assert _forward_excess(base, 3, True, split, topix) == pytest.approx(0.10)


def test_a_window_past_the_end_of_the_calendar_has_no_answer() -> None:
    """Not the same as a missing price - only this one shrinks as data arrives."""
    from stock_ai.backtest.disclosure_impact import _forward_excess

    stock = _price_frame([100.0, 110.0, 120.0])
    topix = _flat_topix(3)
    base = stock.index[1]

    assert _forward_excess(base, 2, True, stock, topix) is not None
    assert _forward_excess(base, 20, True, stock, topix) is None


def test_coverage_separates_a_recent_disclosure_from_a_missing_price() -> None:
    frame = _labeled(
        [
            # Reaches far enough, has the value.
            {
                "symbol": "1",
                "timing": "after_hours",
                "observable_horizon": 30,
                "open_to_close_20d": 0.02,
            },
            # Reaches far enough but the price is absent: a real gap.
            {
                "symbol": "2",
                "timing": "after_hours",
                "observable_horizon": 30,
                "open_to_close_20d": np.nan,
            },
            # Too recent for the window to exist at all.
            {
                "symbol": "3",
                "timing": "after_hours",
                "observable_horizon": 4,
                "open_to_close_20d": np.nan,
            },
        ]
    )
    rows = horizon_coverage(frame).rows.set_index("horizon")

    assert int(rows.loc[20, "n"]) == 1
    assert int(rows.loc[20, "too_recent"]) == 1
    assert int(rows.loc[20, "missing_price"]) == 1


def test_quartiles_distinguish_a_tight_median_from_a_wide_one() -> None:
    """Two series with the same median and very different bets."""
    tight = _labeled(
        [{"symbol": str(i), CLOSE_TO_CLOSE: v} for i, v in enumerate(np.linspace(0.0, 0.008, 101))]
    )
    wide = _labeled(
        [
            {"symbol": str(i), CLOSE_TO_CLOSE: v}
            for i, v in enumerate(np.linspace(-0.20, 0.208, 101))
        ]
    )
    tight_stats = _stats(tight, CLOSE_TO_CLOSE)
    wide_stats = _stats(wide, CLOSE_TO_CLOSE)

    assert tight_stats["median"] == pytest.approx(wide_stats["median"], abs=1e-9)
    assert wide_stats["iqr"] > tight_stats["iqr"] * 10
    # The ratio is what separates them, and it is the number to read.
    assert tight_stats["median_over_iqr"] > wide_stats["median_over_iqr"] * 10


def test_median_over_iqr_is_absent_rather_than_infinite_on_a_degenerate_spread() -> None:
    same = _labeled([{"symbol": str(i), CLOSE_TO_CLOSE: 0.01} for i in range(5)])
    assert np.isnan(_stats(same, CLOSE_TO_CLOSE)["median_over_iqr"])
