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


def test_unrevised_disclosure_reads_as_flat_not_unmeasurable() -> None:
    """A forecast that existed on both sides and held is 'flat', not 'n/a'."""
    events = build_disclosure_events("1234", _revision_pair("1000", "1000"))
    assert events[1].revision_direction == "flat"
    # The comparison was possible - that is what separates flat from n/a.
    assert events[1].direction_metric() == "NP"
    assert events[1].is_revision is False


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
    # ...but no earnings forecast exists to give it a direction. This is the
    # 8306 case: a filer that publishes a dividend forecast and nothing else.
    assert events[1].revision_direction == "n/a"
    assert events[1].direction_metric() is None


def test_disclosure_frame_carries_direction_columns() -> None:
    events = build_disclosure_events("1234", _revision_pair("1000", "1200"))
    frame = disclosure_frame(events)
    # The first disclosure of a fiscal year has no prior value: unmeasurable,
    # not unchanged.
    assert list(frame["revision_direction"]) == ["n/a", "up"]
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


# ---------------------------------------------------------------------------
# Next-year guidance seeding, against figures taken from a live 7203 payload
# ---------------------------------------------------------------------------

# Fields as returned by /fins/summary for 7203 on the dates shown. The
# full-year rows carry no F* at all - their guidance for the coming year is
# in NxF* - which is exactly what makes the seeding necessary.
_TOYOTA_FY_2026 = {
    "DiscDate": "2026-05-08",
    "DiscTime": "15:30",
    "DocType": "FYFinancialStatements_Consolidated_IFRS",
    "CurPerType": "FY",
    "CurFYEn": "2026-03-31",
    "NxtFYEn": "2027-03-31",
    "NxFSales": "51000000000000",
    "NxFOP": "3000000000000",
    "NxFNp": "3000000000000",
    "NxFEPS": "251.25",
}
_TOYOTA_1Q_2027 = {
    "DiscDate": "2026-08-04",
    "DiscTime": "15:30",
    "DocType": "1QFinancialStatements_Consolidated_IFRS",
    "CurPerType": "1Q",
    "CurFYEn": "2027-03-31",
    "NxtFYEn": "2028-03-31",
    "FSales": "54000000000000",
    "FOP": "3400000000000",
    "FNP": "3250000000000",
    "FEPS": "272.17",
}
_TOYOTA_FY_2025 = {
    "DiscDate": "2025-05-08",
    "DiscTime": "15:30",
    "DocType": "FYFinancialStatements_Consolidated_IFRS",
    "CurPerType": "FY",
    "CurFYEn": "2025-03-31",
    "NxtFYEn": "2026-03-31",
    "NxFSales": "48500000000000",
    "NxFOP": "3800000000000",
    "NxFNp": "3100000000000",
    "NxFEPS": "237.57",
}
_TOYOTA_1Q_2026 = {
    "DiscDate": "2025-08-07",
    "DiscTime": "15:30",
    "DocType": "1QFinancialStatements_Consolidated_IFRS",
    "CurPerType": "1Q",
    "CurFYEn": "2026-03-31",
    "NxtFYEn": "2027-03-31",
    "FSales": "48500000000000",
    "FOP": "3200000000000",
    "FNP": "2660000000000",
    "FEPS": "204.09",
}


def test_first_quarter_raise_is_detected_against_next_year_guidance() -> None:
    """7203 raised FY2027 net profit guidance at 1Q: 3.00兆 -> 3.25兆."""
    events = build_disclosure_events("7203", [_TOYOTA_FY_2026, _TOYOTA_1Q_2027])
    full_year, first_quarter = events

    # Issuing guidance is not revising it, and the year it reports is over,
    # so it carries no current-year forecast to compare either.
    assert full_year.is_revision is False
    assert full_year.revision_direction == "n/a"

    assert first_quarter.is_revision is True
    assert first_quarter.revision_direction == "up"
    assert first_quarter.direction_metric() == "NP"
    assert first_quarter.revised_from["NP"] == 3_000_000_000_000.0
    assert first_quarter.forecasts["NP"] == 3_250_000_000_000.0
    # +8.3%, computed independently of the code under test.
    raise_pct = first_quarter.forecasts["NP"] / first_quarter.revised_from["NP"] - 1
    assert raise_pct == pytest.approx(0.0833, abs=1e-4)


def test_first_quarter_cut_is_detected_against_next_year_guidance() -> None:
    """7203 cut FY2026 net profit guidance at 1Q: 3.10兆 -> 2.66兆."""
    events = build_disclosure_events("7203", [_TOYOTA_FY_2025, _TOYOTA_1Q_2026])
    _full_year, first_quarter = events

    assert first_quarter.revision_direction == "down"
    assert first_quarter.revised_from["NP"] == 3_100_000_000_000.0
    assert first_quarter.forecasts["NP"] == 2_660_000_000_000.0
    # -14.2%.
    cut_pct = first_quarter.forecasts["NP"] / first_quarter.revised_from["NP"] - 1
    assert cut_pct == pytest.approx(-0.1419, abs=1e-4)


def test_operating_profit_and_sales_revisions_are_seeded_too() -> None:
    events = build_disclosure_events("7203", [_TOYOTA_FY_2025, _TOYOTA_1Q_2026])
    first_quarter = events[1]
    # OP cut 3.80兆 -> 3.20兆; Sales guidance unchanged at 48.5兆, so absent.
    assert first_quarter.revised_from["OP"] == 3_800_000_000_000.0
    assert "Sales" not in first_quarter.revised_from


def test_seeding_uses_nxtfyen_rather_than_adding_a_year() -> None:
    """A company changing its fiscal year end must not have guidance misfiled."""
    records = [
        {
            "DiscDate": "2024-05-10",
            "DiscTime": "15:30",
            "CurFYEn": "2024-03-31",
            # A 9-month transition period ending in the same calendar year.
            "NxtFYEn": "2024-12-31",
            "NxFNp": "1000",
        },
        {
            "DiscDate": "2024-08-09",
            "DiscTime": "15:30",
            "CurFYEn": "2024-12-31",
            "FNP": "1200",
        },
    ]
    events = build_disclosure_events("1234", records)
    # Adding a year would have filed the guidance under 2025 and missed this.
    assert events[1].revision_direction == "up"
    assert events[1].revised_from["NP"] == 1000.0


def test_seeding_falls_back_to_the_following_year_without_nxtfyen() -> None:
    records = [
        {"DiscDate": "2024-05-10", "DiscTime": "15:30", "CurFYEn": "2024-03-31", "NxFNp": "1000"},
        {"DiscDate": "2024-08-09", "DiscTime": "15:30", "CurFYEn": "2025-03-31", "FNP": "900"},
    ]
    events = build_disclosure_events("1234", records)
    assert events[1].revision_direction == "down"


def test_uppercase_next_profit_spelling_is_accepted_as_a_fallback() -> None:
    records = [
        {"DiscDate": "2024-05-10", "DiscTime": "15:30", "CurFYEn": "2024-03-31", "NxFNP": "1000"},
        {"DiscDate": "2024-08-09", "DiscTime": "15:30", "CurFYEn": "2025-03-31", "FNP": "1100"},
    ]
    events = build_disclosure_events("1234", records)
    assert events[1].revision_direction == "up"


def test_guidance_with_no_following_disclosure_produces_no_revision() -> None:
    events = build_disclosure_events("7203", [_TOYOTA_FY_2026])
    assert len(events) == 1
    assert events[0].is_revision is False


def test_a_filer_publishing_only_a_dividend_forecast_never_reads_as_flat() -> None:
    """The 8306 shape: every F* earnings field empty on every record.

    Taken from a live payload - 8306 discloses FDivAnn and NxFDivAnn and no
    consolidated earnings forecast at all. Every such disclosure must read
    "n/a", because reporting it as "flat" would put "this company publishes
    no guidance" into the same bucket as "this company held its guidance".
    """
    records = [
        {
            "DiscDate": "2025-08-04",
            "DiscTime": "15:30",
            "DocType": "1QFinancialStatements_Consolidated_JP",
            "CurFYEn": "2026-03-31",
            "FDivAnn": "70.0",
        },
        {
            "DiscDate": "2025-11-14",
            "DiscTime": "15:30",
            "DocType": "2QFinancialStatements_Consolidated_JP",
            "CurFYEn": "2026-03-31",
            "FDivAnn": "74.0",
        },
    ]
    events = build_disclosure_events("8306", records)
    assert [event.revision_direction for event in events] == ["n/a", "n/a"]
    # The dividend raise is still recorded as a revision, just not as an
    # earnings direction.
    assert events[1].is_revision is True
    assert events[1].revised_from == {"DivAnn": 70.0}


def test_flat_and_unmeasurable_land_in_separate_summary_rows() -> None:
    held = build_disclosure_events("AAAA", _revision_pair("1000", "1000"))[1:]
    unmeasurable = build_disclosure_events(
        "BBBB",
        [
            {
                "DiscDate": "2024-08-09",
                "DiscTime": "15:30",
                "DocType": "1QFinancialStatements_Consolidated_JP",
                "CurFYEn": "2025-03-31",
                "FDivAnn": "70.0",
            }
        ],
    )
    events = held + unmeasurable
    stock_prices = {
        "AAAA": _bars([("2024-08-09", 1000), ("2024-08-13", 1050)]),
        "BBBB": _bars([("2024-08-09", 1000), ("2024-08-13", 950)]),
    }
    topix = _topix([("2024-08-09", 2000), ("2024-08-13", 2000)])

    summary = summarize_by_revision(label_disclosures(events, stock_prices, topix))
    directions = dict(
        zip(summary["revision_direction"], summary["median_excess_return"], strict=True)
    )
    assert directions["flat"] == pytest.approx(0.05)
    assert directions["n/a"] == pytest.approx(-0.05)
