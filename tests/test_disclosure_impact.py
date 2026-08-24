"""Tests for the timely-disclosure impact labeling foundation.

Includes one fully hand-calculated example per disclosure-timing rule (the
numbers below are re-derived independently in each assertion, not merely
compared against what the code itself produces), plus the three edge cases
the task calls out explicitly: a holiday spanning the reaction window, a
delisted/missing-price symbol, and a mid-series data gap.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from stock_ai.backtest.disclosure_impact import (
    FORECAST_METRICS,
    build_disclosure_events,
    disclosure_frame,
    label_disclosures,
    label_excess_return,
    reference_dates,
    summarize_by_doc_type,
    summarize_by_revision,
)
from stock_ai.data.schema import ADJ_CLOSE, CLOSE, DATE


def _bars(pairs: list[tuple[str, float]]) -> pd.DataFrame:
    """Build a minimal OHLCV-shaped frame: date -> adj_close."""
    dates = pd.to_datetime([d for d, _ in pairs])
    frame = pd.DataFrame({ADJ_CLOSE: [v for _, v in pairs]}, index=dates)
    frame.index.name = DATE
    return frame


def _topix(pairs: list[tuple[str, float]]) -> pd.DataFrame:
    dates = pd.to_datetime([d for d, _ in pairs])
    frame = pd.DataFrame({CLOSE: [v for _, v in pairs]}, index=dates)
    frame.index.name = DATE
    return frame


# ---------------------------------------------------------------------------
# Forecast-revision detection (before/after values)
# ---------------------------------------------------------------------------


def test_first_disclosure_of_a_fiscal_year_is_never_a_revision() -> None:
    records = [
        {
            "DiscDate": "2024-05-10",
            "DiscTime": "15:30",
            "DocType": "FYFinancialStatements_Consolidated_JP",
            "CurFYEn": "2025-03-31",
            "FNP": "1000",
        }
    ]
    events = build_disclosure_events("1234", records)
    assert len(events) == 1
    assert events[0].is_revision is False
    assert events[0].revised_from == {}
    assert events[0].forecasts["NP"] == 1000.0


def test_second_disclosure_with_a_changed_forecast_is_a_revision() -> None:
    records = [
        {
            "DiscDate": "2024-05-10",
            "DiscTime": "15:30",
            "DocType": "FYFinancialStatements_Consolidated_JP",
            "CurFYEn": "2025-03-31",
            "FNP": "1000",
            "FSales": "10000",
        },
        {
            # A revision-only notice a month later: same target fiscal year,
            # NP forecast raised, Sales forecast left untouched.
            "DiscDate": "2024-06-14",
            "DiscTime": "16:00",
            "DocType": "EarnForecastRevision",
            "CurFYEn": "2025-03-31",
            "FNP": "1200",
            "FSales": "10000",
        },
    ]
    events = build_disclosure_events("1234", records)
    assert len(events) == 2
    first, second = events
    assert first.is_revision is False
    assert second.is_revision is True
    assert second.revised_from == {"NP": 1000.0}
    assert second.forecasts["NP"] == 1200.0

    frame = disclosure_frame(events)
    row = frame.iloc[1]
    assert row["NP_before"] == 1000.0
    assert row["NP_after"] == 1200.0
    assert pd.isna(row["Sales_before"])  # unchanged, so not reported as a revision
    assert bool(row["is_revision"]) is True


def test_unchanged_forecast_across_disclosures_is_not_a_revision() -> None:
    records = [
        {"DiscDate": "2024-05-10", "DiscTime": "15:30", "CurFYEn": "2025-03-31", "FNP": "1000"},
        {"DiscDate": "2024-08-10", "DiscTime": "15:30", "CurFYEn": "2025-03-31", "FNP": "1000"},
    ]
    events = build_disclosure_events("1234", records)
    assert events[1].is_revision is False


def test_a_new_fiscal_years_first_forecast_does_not_compare_to_the_old_year() -> None:
    records = [
        {"DiscDate": "2024-05-10", "DiscTime": "15:30", "CurFYEn": "2025-03-31", "FNP": "1000"},
        # A different target fiscal year: NxFNP-style rollover would land
        # here as the *first* observation of FY2026, not a revision of FY2025.
        {"DiscDate": "2025-05-10", "DiscTime": "15:30", "CurFYEn": "2026-03-31", "FNP": "50"},
    ]
    events = build_disclosure_events("1234", records)
    assert events[1].is_revision is False


def test_forecast_metrics_cover_the_documented_fields() -> None:
    assert set(FORECAST_METRICS) == {"Sales", "OP", "OdP", "NP", "EPS", "DivAnn"}


# ---------------------------------------------------------------------------
# After-hours vs intraday base-date rule (item 3 of the task)
# ---------------------------------------------------------------------------


def test_missing_disc_time_defaults_to_after_hours() -> None:
    records = [{"DiscDate": "2024-05-10", "CurFYEn": "2025-03-31", "FNP": "1000"}]
    events = build_disclosure_events("1234", records)
    assert events[0].is_after_hours is True


def test_disc_time_before_session_close_is_intraday() -> None:
    records = [{"DiscDate": "2024-05-10", "DiscTime": "10:30", "CurFYEn": "2025-03-31"}]
    events = build_disclosure_events("1234", records)
    assert events[0].is_after_hours is False


def test_disc_time_at_session_close_is_after_hours() -> None:
    records = [{"DiscDate": "2024-05-10", "DiscTime": "15:00", "CurFYEn": "2025-03-31"}]
    events = build_disclosure_events("1234", records)
    assert events[0].is_after_hours is True


# Trading calendar with a 3-day gap (a holiday span): Jan 10/11/12, then a
# jump straight to 16/17 (13-15 do not trade).
_CALENDAR = pd.to_datetime(["2024-01-10", "2024-01-11", "2024-01-12", "2024-01-16", "2024-01-17"])


def test_after_hours_reference_dates_skip_the_holiday_gap() -> None:
    base, prior = reference_dates(dt.date(2024, 1, 12), is_after_hours=True, calendar=_CALENDAR)
    assert base == pd.Timestamp("2024-01-16")
    assert prior == pd.Timestamp("2024-01-12")


def test_intraday_reference_dates_use_the_disclosure_day_itself() -> None:
    base, prior = reference_dates(dt.date(2024, 1, 17), is_after_hours=False, calendar=_CALENDAR)
    assert base == pd.Timestamp("2024-01-17")
    assert prior == pd.Timestamp("2024-01-16")


def test_reference_dates_past_the_calendar_return_none() -> None:
    base, prior = reference_dates(dt.date(2024, 1, 17), is_after_hours=True, calendar=_CALENDAR)
    assert base is None
    assert prior is None


# ---------------------------------------------------------------------------
# Hand-calculated excess return (item 4 of the task)
# ---------------------------------------------------------------------------


def test_after_hours_excess_return_matches_hand_calculation() -> None:
    # Disclosed after the close on Jan 12 (Friday); Jan 13-15 do not trade.
    # Reaction window is close(Jan 16) over close(Jan 12).
    stock = _bars(
        [
            ("2024-01-10", 990),
            ("2024-01-11", 995),
            ("2024-01-12", 1000),
            ("2024-01-16", 1050),
            ("2024-01-17", 1029),
        ]
    )
    topix = _topix(
        [
            ("2024-01-10", 1990),
            ("2024-01-11", 1995),
            ("2024-01-12", 2000),
            ("2024-01-16", 1980),
            ("2024-01-17", 2000),
        ]
    )

    label = label_excess_return(dt.date(2024, 1, 12), True, stock, topix)

    expected_stock_return = 1050 / 1000 - 1  # +5.0%
    expected_topix_return = 1980 / 2000 - 1  # -1.0%
    assert label.exclude_reason is None
    assert label.base_date == pd.Timestamp("2024-01-16")
    assert label.prior_date == pd.Timestamp("2024-01-12")
    assert label.stock_return == pytest.approx(expected_stock_return)
    assert label.topix_return == pytest.approx(expected_topix_return)
    assert label.excess_return == pytest.approx(expected_stock_return - expected_topix_return)
    assert label.excess_return == pytest.approx(0.06)


def test_intraday_excess_return_matches_hand_calculation() -> None:
    # Disclosed during the session on Jan 17; reaction window is
    # close(Jan 17) over close(Jan 16) - the day before it, itself.
    stock = _bars([("2024-01-16", 1050), ("2024-01-17", 1029)])
    topix = _topix([("2024-01-16", 1980), ("2024-01-17", 2000)])

    label = label_excess_return(dt.date(2024, 1, 17), False, stock, topix)

    expected_stock_return = 1029 / 1050 - 1  # -2.0%
    expected_topix_return = 2000 / 1980 - 1  # +1.0101...%
    assert label.exclude_reason is None
    assert label.base_date == pd.Timestamp("2024-01-17")
    assert label.prior_date == pd.Timestamp("2024-01-16")
    assert label.stock_return == pytest.approx(expected_stock_return)
    assert label.topix_return == pytest.approx(expected_topix_return)
    assert label.excess_return == pytest.approx(expected_stock_return - expected_topix_return)


# ---------------------------------------------------------------------------
# Edge cases the task requires to pass cleanly: holiday, delisting, gap.
# ---------------------------------------------------------------------------


def test_holiday_spanning_disclosure_is_handled_explicitly() -> None:
    # Already the mechanism under test above (test_after_hours_excess_return_
    # matches_hand_calculation uses a 3-day gap); this test asserts the
    # no-crash / correct-pairing contract on its own, as the task's case (1).
    stock = _bars([("2024-01-12", 1000), ("2024-01-16", 1010)])
    topix = _topix([("2024-01-12", 2000), ("2024-01-16", 2000)])
    label = label_excess_return(dt.date(2024, 1, 12), True, stock, topix)
    assert label.exclude_reason is None
    assert label.base_date == pd.Timestamp("2024-01-16")


def test_delisted_symbol_missing_future_price_excludes_without_crashing() -> None:
    # Stock's price history simply stops - no row for the reaction day.
    stock = _bars([("2024-01-10", 990), ("2024-01-11", 995), ("2024-01-12", 1000)])
    topix = _topix(
        [("2024-01-10", 1990), ("2024-01-11", 1995), ("2024-01-12", 2000), ("2024-01-16", 1980)]
    )
    label = label_excess_return(dt.date(2024, 1, 12), True, stock, topix)
    assert label.exclude_reason == "missing_stock_price"
    assert label.excess_return is None
    assert label.stock_return is None


def test_mid_series_data_gap_excludes_without_crashing() -> None:
    # Stock has rows both before and after the required prior date, but not
    # on it (e.g. a trading halt) - distinct from delisting (a hard stop).
    stock = pd.DataFrame(
        {ADJ_CLOSE: [1000.0, np.nan, 1050.0]},
        index=pd.to_datetime(["2024-01-12", "2024-01-16", "2024-01-17"]),
    )
    topix = _topix([("2024-01-12", 2000), ("2024-01-16", 1980), ("2024-01-17", 2000)])
    label = label_excess_return(dt.date(2024, 1, 12), True, stock, topix)
    assert label.exclude_reason == "missing_stock_price"
    assert label.excess_return is None


def test_symbol_with_no_stored_prices_at_all_is_excluded_via_label_disclosures() -> None:
    records = [{"DiscDate": "2024-01-12", "DiscTime": "16:00", "CurFYEn": "2025-03-31"}]
    events = build_disclosure_events("9999", records)
    topix = _topix([("2024-01-12", 2000), ("2024-01-16", 1980)])
    labeled = label_disclosures(events, {}, topix)
    assert labeled.loc[0, "exclude_reason"] == "no_stock_prices"
    assert pd.isna(labeled.loc[0, "excess_return"])


# ---------------------------------------------------------------------------
# Aggregation (item 5 of the task: n / median / std by disclosure type)
# ---------------------------------------------------------------------------


def test_summarize_by_doc_type_counts_median_and_std() -> None:
    records_a = [
        {"DiscDate": "2024-01-12", "DiscTime": "16:00", "DocType": "T", "CurFYEn": "2025-03-31"},
    ]
    records_b = [
        {"DiscDate": "2024-01-12", "DiscTime": "16:00", "DocType": "T", "CurFYEn": "2025-03-31"},
    ]
    events = build_disclosure_events("AAAA", records_a) + build_disclosure_events("BBBB", records_b)
    stock_prices = {
        "AAAA": _bars([("2024-01-12", 1000), ("2024-01-16", 1050)]),
        "BBBB": _bars([("2024-01-12", 1000), ("2024-01-16", 900)]),
    }
    topix = _topix([("2024-01-12", 2000), ("2024-01-16", 2000)])

    labeled = label_disclosures(events, stock_prices, topix)
    summary = summarize_by_doc_type(labeled)

    assert list(summary["doc_type"]) == ["T"]
    assert int(summary.loc[0, "n"]) == 2
    returns = sorted([1050 / 1000 - 1, 900 / 1000 - 1])  # topix flat, so excess == raw return
    expected_median = float(np.median(returns))
    expected_std = float(np.std(returns, ddof=1))
    assert summary.loc[0, "median_excess_return"] == pytest.approx(expected_median)
    assert summary.loc[0, "std_excess_return"] == pytest.approx(expected_std)


def test_summarize_by_doc_type_drops_excluded_rows() -> None:
    events = build_disclosure_events(
        "9999",
        [{"DiscDate": "2024-01-12", "DiscTime": "16:00", "DocType": "T", "CurFYEn": "2025-03-31"}],
    )
    topix = _topix([("2024-01-12", 2000)])  # no Jan-16 bar -> calendar_out_of_range
    labeled = label_disclosures(events, {"9999": _bars([("2024-01-12", 1000)])}, topix)
    summary = summarize_by_doc_type(labeled)
    assert summary.empty


def test_summarize_by_doc_type_empty_input() -> None:
    summary = summarize_by_doc_type(pd.DataFrame())
    assert list(summary.columns) == ["doc_type", "n", "median_excess_return", "std_excess_return"]
    assert summary.empty


# ---------------------------------------------------------------------------
# Revision direction, and the doc_type x revision split
# ---------------------------------------------------------------------------


def _revision_pair(first_np: str, second_np: str, **second_extra: str) -> list[dict[str, str]]:
    """Two disclosures of the same fiscal year, the second revising FNP."""
    return [
        {
            "DiscDate": "2024-05-10",
            "DiscTime": "15:30",
            "DocType": "FYFinancialStatements_Consolidated_JP",
            "CurFYEn": "2025-03-31",
            "FNP": first_np,
        },
        {
            "DiscDate": "2024-08-09",
            "DiscTime": "15:30",
            "DocType": "1QFinancialStatements_Consolidated_JP",
            "CurFYEn": "2025-03-31",
            "FNP": second_np,
            **second_extra,
        },
    ]


def test_raised_profit_forecast_reads_as_up() -> None:
    events = build_disclosure_events("1234", _revision_pair("1000", "1200"))
    assert events[1].revision_direction == "up"
    assert events[1].direction_metric() == "NP"


def test_cut_profit_forecast_reads_as_down() -> None:
    events = build_disclosure_events("1234", _revision_pair("1000", "800"))
    assert events[1].revision_direction == "down"


def test_unrevised_disclosure_reads_as_none() -> None:
    events = build_disclosure_events("1234", _revision_pair("1000", "1000"))
    assert events[1].revision_direction == "none"
    assert events[1].direction_metric() is None


def test_a_loss_forecast_narrowing_is_an_upward_revision() -> None:
    # -100 -> -50 is an improvement, and a naive abs() or sign test would
    # call it a cut. Plain numeric comparison gets it right.
    events = build_disclosure_events("1234", _revision_pair("-100", "-50"))
    assert events[1].revision_direction == "up"


def test_a_forecast_falling_into_loss_is_a_downward_revision() -> None:
    events = build_disclosure_events("1234", _revision_pair("100", "-50"))
    assert events[1].revision_direction == "down"


def test_profit_outranks_revenue_when_the_two_disagree() -> None:
    # Revenue cut, profit raised: a margin story. Filed on profit, per
    # DIRECTION_PRIORITY, rather than on whichever field is read first.
    records = [
        {
            "DiscDate": "2024-05-10",
            "DiscTime": "15:30",
            "CurFYEn": "2025-03-31",
            "FNP": "1000",
            "FSales": "10000",
        },
        {
            "DiscDate": "2024-08-09",
            "DiscTime": "15:30",
            "CurFYEn": "2025-03-31",
            "FNP": "1200",
            "FSales": "9000",
        },
    ]
    events = build_disclosure_events("1234", records)
    assert events[1].direction_metric() == "NP"
    assert events[1].revision_direction == "up"


def test_operating_profit_decides_when_net_profit_is_absent() -> None:
    records = [
        {"DiscDate": "2024-05-10", "DiscTime": "15:30", "CurFYEn": "2025-03-31", "FOP": "500"},
        {"DiscDate": "2024-08-09", "DiscTime": "15:30", "CurFYEn": "2025-03-31", "FOP": "400"},
    ]
    events = build_disclosure_events("1234", records)
    assert events[1].direction_metric() == "OP"
    assert events[1].revision_direction == "down"


def test_dividend_only_revision_is_a_revision_but_has_no_earnings_direction() -> None:
    records = [
        {"DiscDate": "2024-05-10", "DiscTime": "15:30", "CurFYEn": "2025-03-31", "FDivAnn": "50"},
        {"DiscDate": "2024-08-09", "DiscTime": "15:30", "CurFYEn": "2025-03-31", "FDivAnn": "60"},
    ]
    events = build_disclosure_events("1234", records)
    assert events[1].is_revision is True  # it did revise something
    assert events[1].revision_direction == "none"  # but not an earnings figure
    assert events[1].direction_metric() is None


def test_disclosure_frame_carries_direction_columns() -> None:
    events = build_disclosure_events("1234", _revision_pair("1000", "1200"))
    frame = disclosure_frame(events)
    assert list(frame["revision_direction"]) == ["none", "up"]
    # pandas stores the absent metric as NaN, not None, in an object column.
    assert pd.isna(frame["direction_metric"].iloc[0])
    assert frame["direction_metric"].iloc[1] == "NP"


def test_summarize_by_revision_separates_directions_that_would_otherwise_cancel() -> None:
    """The whole point: +5% and -5% under one DocType must not read as 0%."""
    up = build_disclosure_events("AAAA", _revision_pair("1000", "1200"))[1:]
    down = build_disclosure_events("BBBB", _revision_pair("1000", "800"))[1:]
    events = up + down

    # AAAA rises 5%, BBBB falls 5%, TOPIX flat, over the same window.
    stock_prices = {
        "AAAA": _bars([("2024-08-09", 1000), ("2024-08-13", 1050)]),
        "BBBB": _bars([("2024-08-09", 1000), ("2024-08-13", 950)]),
    }
    topix = _topix([("2024-08-09", 2000), ("2024-08-13", 2000)])

    labeled = label_disclosures(events, stock_prices, topix)

    # Grouped by DocType alone the two cancel to roughly zero...
    by_doc_type = summarize_by_doc_type(labeled)
    assert int(by_doc_type.loc[0, "n"]) == 2
    assert by_doc_type.loc[0, "median_excess_return"] == pytest.approx(0.0)

    # ...and split by direction the signal is visible again.
    by_revision = summarize_by_revision(labeled)
    directions = dict(
        zip(by_revision["revision_direction"], by_revision["median_excess_return"], strict=True)
    )
    assert directions["up"] == pytest.approx(0.05)
    assert directions["down"] == pytest.approx(-0.05)


def test_summarize_by_revision_columns_and_empty_input() -> None:
    summary = summarize_by_revision(pd.DataFrame())
    assert list(summary.columns) == [
        "doc_type",
        "revision_direction",
        "n",
        "median_excess_return",
        "std_excess_return",
    ]
    assert summary.empty
