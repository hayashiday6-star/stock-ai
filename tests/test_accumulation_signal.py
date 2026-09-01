"""Tests for the JP accumulation pre-registration's 5-condition signal.

Formulas are pinned to the 2026-08-31 pre-registration (see
``stock_ai.backtest.accumulation_signal`` module docstring). These tests
check the pre-registration's exact wording, not a plausible approximation of
it - a silently "close enough" formula is the failure mode the registration
itself exists to prevent.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from stock_ai.backtest.accumulation_signal import (
    MIN_HISTORY_BARS,
    Signal5Thresholds,
    compute_signal_frame,
    count_signals,
    market_cap_series,
)
from stock_ai.data.schema import ADJ_CLOSE, CLOSE, HIGH, LOW, OPEN, VOLUME
from stock_ai.data.types import FinancialReport
from stock_ai.database.engine import Database
from stock_ai.database.repository import FinancialStatementRepository, PriceRepository

_BARS = MIN_HISTORY_BARS + 20


def _flat_frame(bars: int = _BARS, base: float = 100.0, volume: float = 10_000.0) -> pd.DataFrame:
    """A perfectly flat, low-volume series - every condition trivially passes.

    Flat means the 52-week low equals the last close (0% distance), the
    20-day range is 0%, the Bollinger width is 0%, and every SMA equals the
    close - so only what a test deliberately perturbs can fail.
    """
    index = pd.bdate_range(end=dt.date(2026, 6, 30), periods=bars, name="date")
    close = np.full(bars, base)
    return pd.DataFrame(
        {
            OPEN: close,
            HIGH: close,
            LOW: close,
            CLOSE: close,
            ADJ_CLOSE: close,
            VOLUME: np.full(bars, volume),
        },
        index=index,
    )


def _spike_volume(frame: pd.DataFrame, multiple: float = 6.0) -> pd.DataFrame:
    frame = frame.copy()
    frame.loc[frame.index[-1], VOLUME] = frame[VOLUME].iloc[0] * multiple
    return frame


def test_all_five_conditions_met_signals() -> None:
    frame = compute_signal_frame(_spike_volume(_flat_frame()))
    assert bool(frame["signal"].iloc[-1]) is True


def test_rows_before_enough_history_are_never_signals() -> None:
    """The 52-week window doubles as the '250営業日以上' universe requirement."""
    frame = compute_signal_frame(_spike_volume(_flat_frame()))
    assert not frame["signal"].iloc[: MIN_HISTORY_BARS - 1].any()


def test_volume_multiple_excludes_the_judged_day_from_its_own_average() -> None:
    """Section 3's formula: judged-day volume over the average *excluding* it.

    Folding the day into its own average would score a true 6x day as
    6/(20*1 + 6)/20 ≈ 4.6x - under the 5.0 threshold it exists to clear.
    """
    frame = compute_signal_frame(_spike_volume(_flat_frame(), multiple=6.0))
    assert frame["volume_multiple"].iloc[-1] == pytest.approx(6.0)


def test_volume_multiple_below_threshold_blocks_the_signal() -> None:
    frame = compute_signal_frame(_flat_frame())  # no spike: multiple == 1.0
    assert frame["volume_multiple"].iloc[-1] == pytest.approx(1.0)
    assert not frame["signal"].iloc[-1]


def test_above_52w_low_beyond_threshold_blocks_the_signal() -> None:
    base = _spike_volume(_flat_frame())
    base.loc[base.index[-100], LOW] = 40.0  # the 52-week low becomes 40
    base.loc[base.index[-1], [OPEN, HIGH, LOW, CLOSE, ADJ_CLOSE]] = 60.0  # +50% off it
    frame = compute_signal_frame(base)
    assert frame["above_52w_low"].iloc[-1] == pytest.approx(0.5)
    assert not frame["signal"].iloc[-1]


def test_range_20d_beyond_threshold_blocks_the_signal() -> None:
    base = _spike_volume(_flat_frame())
    base.loc[base.index[-10], HIGH] = 120.0  # 20% swing inside the 20-day window
    frame = compute_signal_frame(base)
    assert frame["range_20d"].iloc[-1] == pytest.approx(0.2)
    assert not frame["signal"].iloc[-1]


def test_bollinger_width_beyond_threshold_blocks_the_signal() -> None:
    base = _spike_volume(_flat_frame())
    # Alternate the last 20 closes between 90 and 110 - wide dispersion inside
    # the Bollinger window without needing a single outlier bar.
    tail = base.index[-20:]
    base.loc[tail[::2], [OPEN, HIGH, LOW, CLOSE, ADJ_CLOSE]] = 90.0
    base.loc[tail[1::2], [OPEN, HIGH, LOW, CLOSE, ADJ_CLOSE]] = 110.0
    frame = compute_signal_frame(base)
    assert frame["bollinger_width"].iloc[-1] > 0.05
    assert not frame["signal"].iloc[-1]


def test_ma_divergence_divides_by_ma20_not_price() -> None:
    """Section 3's formula divides by MA20 - not by the current price.

    The sibling US screen (``accumulation/analysis.py``, a different,
    differently-specified condition) divides its own MA-spread by price.
    Copying that convention here would silently change what was registered,
    so this pins the denominator directly: a ramp is built where dividing by
    MA20 clears the threshold but dividing by the (much lower) starting price
    would not.
    """
    bars = MIN_HISTORY_BARS + 40
    index = pd.bdate_range(end=dt.date(2026, 6, 30), periods=bars, name="date")
    close = np.full(bars, 100.0)
    # A gentle ramp over the last 30 bars spreads MA5/10/20/30 apart while
    # keeping MA20 itself near 100 - the ramp is too small to move MA20 much,
    # but large relative to the flat early history that only affects price.
    close[-30:] = np.linspace(100.0, 106.0, 30)
    frame_data = pd.DataFrame(
        {
            OPEN: close,
            HIGH: close,
            LOW: close,
            CLOSE: close,
            ADJ_CLOSE: close,
            VOLUME: np.full(bars, 10_000.0),
        },
        index=index,
    )
    frame_data = _spike_volume(frame_data)
    frame = compute_signal_frame(frame_data)

    divergence = frame["ma_max_divergence"].iloc[-1]
    price = float(close[-1])
    ma20 = frame_data[CLOSE].rolling(20).mean().iloc[-1]
    # Same spread, two candidate denominators - they must differ for this
    # case to actually distinguish the two conventions.
    assert ma20 != pytest.approx(price)
    divergence_over_price = divergence * ma20 / price
    assert divergence != pytest.approx(divergence_over_price)


def test_zero_volume_day_never_signals() -> None:
    base = _spike_volume(_flat_frame())
    base.loc[base.index[-1], VOLUME] = 0
    frame = compute_signal_frame(base)
    assert not frame["signal"].iloc[-1]


def test_thresholds_can_be_widened_for_the_sensitivity_sweep() -> None:
    base = _flat_frame()
    base.loc[base.index[-1], VOLUME] = base[VOLUME].iloc[0] * 3.0  # below the default 5.0
    default = compute_signal_frame(base)
    widened = compute_signal_frame(base, Signal5Thresholds(volume_multiple_min=3.0))
    assert not default["signal"].iloc[-1]
    assert widened["signal"].iloc[-1]


# --- count_signals -----------------------------------------------------------


def _seed(database: Database, symbol: str, market: str, frame: pd.DataFrame) -> None:
    with database.session() as session:
        PriceRepository(session).upsert_prices(symbol, frame, market=market)


def test_count_signals_scans_jp_only_by_default() -> None:
    database = Database("sqlite:///:memory:")
    database.create_all()
    _seed(database, "7203", "JP", _spike_volume(_flat_frame()))
    _seed(database, "AAPL", "US", _spike_volume(_flat_frame()))

    report = count_signals(database)

    assert report.symbols_scanned == 1
    assert {s.symbol for s in report.signals} == {"7203"}
    database.dispose()


def test_count_signals_skips_symbols_with_insufficient_history() -> None:
    database = Database("sqlite:///:memory:")
    database.create_all()
    _seed(database, "7203", "JP", _spike_volume(_flat_frame(bars=MIN_HISTORY_BARS - 1)))

    report = count_signals(database)

    assert report.symbols_scanned == 1
    assert report.symbols_with_enough_history == 0
    assert report.total == 0


def test_count_signals_by_year_deduplicates_same_day_across_symbols() -> None:
    database = Database("sqlite:///:memory:")
    database.create_all()
    frame = _spike_volume(_flat_frame())
    _seed(database, "7203", "JP", frame)
    _seed(database, "6501", "JP", frame)  # signals on the same last date

    report = count_signals(database)

    assert report.total == 2  # one per symbol
    assert report.unique_dates == 1  # same calendar date
    by_year = report.by_year()
    assert list(by_year["signals"]) == [2]
    assert list(by_year["signal_days"]) == [1]
    database.dispose()


def test_max_signals_per_day_reports_the_busiest_date() -> None:
    """A day where every symbol signals at once should stand out, not average away."""
    database = Database("sqlite:///:memory:")
    database.create_all()
    frame = _spike_volume(_flat_frame())
    for symbol in ("7203", "6501", "8306"):
        _seed(database, symbol, "JP", frame)  # all three signal on the same date

    report = count_signals(database)

    assert report.max_signals_per_day == 3
    by_date = report.by_date()
    assert by_date.iloc[0]["signals"] == 3
    database.dispose()


def test_max_signals_per_day_is_zero_with_no_signals() -> None:
    empty = Database("sqlite:///:memory:")
    empty.create_all()
    report_with_no_signals = count_signals(empty)
    assert report_with_no_signals.max_signals_per_day == 0
    assert report_with_no_signals.by_date().empty
    empty.dispose()


# --- market_cap_series --------------------------------------------------------


def _report(disclosed_on: dt.date | None, shares: float | None) -> FinancialReport:
    return FinancialReport(
        symbol="6501",
        fiscal_year=disclosed_on.year if disclosed_on else 2000,
        disclosed_on=disclosed_on,
        shares_outstanding=shares,
    )


def test_market_cap_series_steps_up_at_each_disclosure() -> None:
    index = pd.bdate_range("2024-01-01", periods=10, name="date")
    raw_close = pd.Series(100.0, index=index)
    statements = [
        _report(dt.date(2024, 1, 3), 1_000_000.0),
        _report(dt.date(2024, 1, 8), 2_000_000.0),
    ]

    market_cap = market_cap_series(raw_close, statements)

    assert pd.isna(market_cap.iloc[0])  # before the first disclosure
    assert market_cap.loc["2024-01-03"] == pytest.approx(100.0 * 1_000_000.0)
    assert market_cap.loc["2024-01-05"] == pytest.approx(100.0 * 1_000_000.0)  # still the old count
    assert market_cap.loc["2024-01-08"] == pytest.approx(100.0 * 2_000_000.0)


def test_market_cap_series_ignores_reports_missing_either_field() -> None:
    index = pd.bdate_range("2024-01-01", periods=5, name="date")
    raw_close = pd.Series(100.0, index=index)
    statements = [
        _report(None, 1_000_000.0),  # no disclosure date - can't be placed in time
        _report(dt.date(2024, 1, 2), None),  # no share count - nothing to multiply
    ]

    market_cap = market_cap_series(raw_close, statements)

    assert market_cap.isna().all()


def test_market_cap_series_uses_the_raw_close_not_a_split_adjusted_one() -> None:
    """The whole point of taking ``raw_close`` as a parameter, not the frame."""
    index = pd.bdate_range("2024-01-01", periods=3, name="date")
    raw_close = pd.Series([500.0, 500.0, 100.0], index=index)  # a 5:1 split on day 3
    statements = [_report(dt.date(2024, 1, 1), 1_000_000.0)]

    market_cap = market_cap_series(raw_close, statements)

    # A split changes the price scale, not the real company value - so market
    # cap must move with the actually-traded price, not stay constant the way
    # it would if a split-adjusted close (flat at 100 throughout) were used.
    assert market_cap.iloc[0] == pytest.approx(500.0 * 1_000_000.0)
    assert market_cap.iloc[-1] == pytest.approx(100.0 * 1_000_000.0)


# --- count_signals(min_market_cap=...) ----------------------------------------


def _seed_statement(database: Database, symbol: str, disclosed_on: dt.date, shares: float) -> None:
    report = FinancialReport(
        symbol=symbol,
        fiscal_year=disclosed_on.year,
        disclosed_on=disclosed_on,
        shares_outstanding=shares,
    )
    with database.session() as session:
        FinancialStatementRepository(session).upsert_reports(symbol, [report], market="JP")


def test_min_market_cap_excludes_a_signal_below_the_floor() -> None:
    database = Database("sqlite:///:memory:")
    database.create_all()
    frame = _spike_volume(_flat_frame(base=100.0))  # raw close == adjusted close (no split)
    _seed(database, "7203", "JP", frame)
    _seed_statement(database, "7203", dt.date(2024, 1, 1), shares=1_000_000.0)  # cap = 1億円

    without_filter = count_signals(database)
    with_filter = count_signals(database, min_market_cap=10_000_000_000.0)  # 100億円

    assert without_filter.total == 1
    assert without_filter.excluded_for_market_cap is None
    assert with_filter.total == 0
    assert with_filter.excluded_for_market_cap == 1
    database.dispose()


def test_min_market_cap_keeps_a_signal_above_the_floor() -> None:
    database = Database("sqlite:///:memory:")
    database.create_all()
    frame = _spike_volume(_flat_frame(base=100.0))
    _seed(database, "7203", "JP", frame)
    _seed_statement(database, "7203", dt.date(2024, 1, 1), shares=1_000_000_000.0)  # cap = 1000億円

    report = count_signals(database, min_market_cap=10_000_000_000.0)

    assert report.total == 1
    assert report.excluded_for_market_cap == 0
    database.dispose()


def test_min_market_cap_excludes_a_signal_with_no_known_shares_outstanding() -> None:
    """No disclosure at all means the floor cannot be confirmed - exclude, don't assume."""
    database = Database("sqlite:///:memory:")
    database.create_all()
    _seed(database, "7203", "JP", _spike_volume(_flat_frame(base=100.0)))

    report = count_signals(database, min_market_cap=10_000_000_000.0)

    assert report.total == 0
    assert report.excluded_for_market_cap == 1
    database.dispose()
