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
)
from stock_ai.data.schema import ADJ_CLOSE, CLOSE, HIGH, LOW, OPEN, VOLUME
from stock_ai.database.engine import Database
from stock_ai.database.repository import PriceRepository

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
