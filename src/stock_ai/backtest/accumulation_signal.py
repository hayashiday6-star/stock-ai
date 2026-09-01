"""The JP accumulation pre-registration's five conditions, and a signal count.

Formulas here are frozen by the 2026-08-31 pre-registration
(``アキュムレーション5条件スクリーナー バックテスト``, sections 3 and 6):
volume multiple, 52-week-low distance, 20-day range, Bollinger width, and
moving-average spread. Conditions 1 and 7 of the original seven (SUPER
cumulative inflow, large-order volume share) are out of scope - moomoo has no
history for either, so this measures what J-Quants alone can tell.

Changing a threshold or a window here after a backtest has run against it
makes that run's own pre-registration a lie. Don't - re-register instead.

This module only counts *how often* the five conditions co-occur; it computes
no return. That split is deliberate: a frequency count answers "is there
enough sample to bother with a return test at all", and answering it cannot
leak information about whether the return test would pass.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import pandas as pd

from stock_ai.data.schema import CLOSE, HIGH, LOW, VOLUME
from stock_ai.database.engine import Database
from stock_ai.database.repository import PriceRepository, list_securities
from stock_ai.technical.indicators import bollinger_bands, sma

#: The pre-registration's windows, in trading days.
VOLUME_WINDOW = 20
FIFTY_TWO_WEEK_WINDOW = 250
RANGE_WINDOW = 20
BOLLINGER_WINDOW = 20
#: MA5/10/20/30 - not MA5/10/20/50. The US accumulation screen (phase 2's
#: seventh-condition score, `accumulation/analysis.py`) uses 50; this
#: pre-registration explicitly specifies 30. The two are not the same
#: condition wearing different clothes - copying one into the other silently
#: changes what was registered.
MA_WINDOWS: tuple[int, ...] = (5, 10, 20, 30)
#: The tightest window in play. A bar needs this many prior bars before any
#: metric below is meaningful, so it doubles as the "上場後250営業日以上経過"
#: universe requirement - no separate listing-date lookup is needed.
MIN_HISTORY_BARS = FIFTY_TWO_WEEK_WINDOW


@dataclass(frozen=True)
class Signal5Thresholds:
    """The five condition thresholds, at the pre-registration's center values.

    Section 8 of the pre-registration sweeps a band around each of these for
    a sensitivity check - that sweep must use this same dataclass with
    different values, not a second, drifting copy of the numbers.
    """

    volume_multiple_min: float = 5.0
    above_52w_low_max: float = 0.15
    range_20d_max: float = 0.10
    bollinger_width_max: float = 0.05
    ma_max_divergence_max: float = 0.05


def compute_signal_frame(
    prices: pd.DataFrame, thresholds: Signal5Thresholds | None = None
) -> pd.DataFrame:
    """Compute all five condition metrics for every bar, and the signal flag.

    Args:
        prices: A split-adjusted OHLCV frame, as returned by
            :meth:`PriceRepository.get_prices` - never the raw stored bars.
            The 52-week window spans up to a year of history, and one
            unadjusted split anywhere in that span silently invalidates it:
            exactly the "different-scale values combined" failure this
            project keeps re-finding, so this is not merely a convenience.
        thresholds: Overrides for the sensitivity sweep; the registered
            center values by default.

    Returns:
        A frame indexed like ``prices``, with one column per metric plus
        ``signal`` (bool). Rows before ``MIN_HISTORY_BARS`` of history are
        ``NaN``/``False`` throughout, not dropped, so a caller can still see
        how much of the series was too young to judge.
    """
    thresholds = thresholds or Signal5Thresholds()
    close, high, low, volume = prices[CLOSE], prices[HIGH], prices[LOW], prices[VOLUME]

    # Condition 2 excludes the day being judged from its own average: folding
    # it in pulls the average up exactly when the day is unusual, which is the
    # one case a volume spike exists to catch. A true 5x day would otherwise
    # score under 4.2x.
    avg_volume_prior = volume.rolling(VOLUME_WINDOW).mean().shift(1)
    volume_multiple = volume / avg_volume_prior

    low_52w = low.rolling(FIFTY_TWO_WEEK_WINDOW).min()
    above_52w_low = (close - low_52w) / low_52w

    high_20d = high.rolling(RANGE_WINDOW).max()
    low_20d = low.rolling(RANGE_WINDOW).min()
    range_20d = (high_20d - low_20d) / low_20d

    bands = bollinger_bands(prices, window=BOLLINGER_WINDOW, num_std=2.0)
    bollinger_width = (bands["upper"] - bands["lower"]) / bands["middle"]

    averages = pd.concat({w: sma(prices, window=w) for w in MA_WINDOWS}, axis=1)
    # Divided by MA20, per the pre-registration - not by the current price,
    # which is what the unrelated US completion score (analysis.py) divides
    # by for its own, differently-specified condition.
    ma20 = averages[20]
    ma_max_divergence = (averages.max(axis=1) - averages.min(axis=1)) / ma20

    frame = pd.DataFrame(
        {
            "volume_multiple": volume_multiple,
            "above_52w_low": above_52w_low,
            "range_20d": range_20d,
            "bollinger_width": bollinger_width,
            "ma_max_divergence": ma_max_divergence,
            "volume": volume,
        }
    )

    # Comparisons against NaN already evaluate False in pandas, so this mask
    # would fall out on its own - it is kept explicit because "why is the
    # first year all False" should read as a stated requirement, not an
    # accident of NaN arithmetic.
    ready = low_52w.notna()
    passes = (
        ready
        & (volume != 0)
        & (frame["volume_multiple"] >= thresholds.volume_multiple_min)
        & (frame["above_52w_low"] <= thresholds.above_52w_low_max)
        & (frame["range_20d"] <= thresholds.range_20d_max)
        & (frame["bollinger_width"] <= thresholds.bollinger_width_max)
        & (frame["ma_max_divergence"] <= thresholds.ma_max_divergence_max)
    )
    frame["signal"] = passes.fillna(False)
    return frame


@dataclass(frozen=True)
class Signal:
    """One symbol clearing all five conditions on one judgment date."""

    symbol: str
    date: dt.date


@dataclass
class SignalCountReport:
    """How often the five conditions co-occur - frequency only, no return.

    This is reconnaissance for the pre-registration's sections 6 and 7
    (period split, sample-size requirements), run before those blanks are
    filled in and the document is sealed. It is deliberately **not** the
    sealed backtest, and these counts must never be read as a pass/fail
    result - the registration explicitly reserves that to the OOS excess
    return, which this report does not compute.

    Known gaps against the pre-registration's universe (section 2), left
    unresolved because this is a reconnaissance pass rather than the sealed
    run:

    - No market-cap filter. Section 2 wants judgment-day market cap; that
      needs the shares outstanding on file as of that specific date, and
      building that point-in-time lookup is deferred to the sealed run.
    - No independent market-segment check. Whatever is in ``symbols_scanned``
      is whatever ``stock-ai universe`` last loaded into this database - the
      ``Security`` table does not itself record Prime/Standard/Growth, so a
      database loaded with ``--segment all`` would count names section 2
      would exclude.
    - Delisted names are entirely absent. ``stock-ai universe`` only ever
      lists what trades today, so a name that delisted partway through the
      sample is invisible here and to the sealed run alike, until that gap is
      closed with its own point-in-time listing lookup.
    """

    signals: list[Signal] = field(default_factory=list)
    symbols_scanned: int = 0
    symbols_with_enough_history: int = 0

    @property
    def total(self) -> int:
        """Total (symbol, date) signals - the same date can repeat across symbols."""
        return len(self.signals)

    @property
    def unique_dates(self) -> int:
        """Independent judgment dates with at least one signal.

        Section 7's sample requirement counts *this*, not ``total``: signals
        on the same date share a market-wide shock and are not independent
        observations, which is also why section 7 asks for a date-clustered
        standard error rather than a plain one.
        """
        return len({s.date for s in self.signals})

    def by_year(self) -> pd.DataFrame:
        """Signals and independent signal-days, one row per calendar year."""
        if not self.signals:
            return pd.DataFrame(columns=["year", "signals", "signal_days"])
        frame = pd.DataFrame({"date": [s.date for s in self.signals]})
        frame["year"] = frame["date"].map(lambda d: d.year)
        grouped = frame.groupby("year").agg(
            signals=("date", "size"), signal_days=("date", "nunique")
        )
        return grouped.reset_index().sort_values("year").reset_index(drop=True)

    def by_date(self) -> pd.DataFrame:
        """Signal count per judgment date, busiest day first.

        Section 7's date-clustered t-statistic corrects for same-day
        correlation, but only if no single day dominates the cluster
        structure - a day that alone contributed a large share of all
        signals is one market-wide event standing in for many, and the
        correction cannot fully undo that. This is what makes that
        concentration checkable before sealing the registration.
        """
        if not self.signals:
            return pd.DataFrame(columns=["date", "signals"])
        frame = pd.DataFrame({"date": [s.date for s in self.signals]})
        grouped = frame.groupby("date").size().rename("signals").reset_index()
        return grouped.sort_values("signals", ascending=False).reset_index(drop=True)

    @property
    def max_signals_per_day(self) -> int:
        """The busiest single judgment date's signal count, 0 if there were none."""
        by_date = self.by_date()
        return int(by_date["signals"].max()) if not by_date.empty else 0


def count_signals(
    database: Database,
    symbols: list[str] | None = None,
    thresholds: Signal5Thresholds | None = None,
) -> SignalCountReport:
    """Scan stored JP symbols and count how often all five conditions align.

    Args:
        database: Source of the stored securities and their price history.
        symbols: Scan only these; defaults to every stored ``market="JP"``
            security (see :class:`SignalCountReport` for what that universe
            does and does not already exclude).
        thresholds: Overrides for the sensitivity sweep (section 8); the
            registered center values by default.
    """
    with database.session() as session:
        if symbols is None:
            symbols = [sym for sym, market in list_securities(session) if market == "JP"]
        repo = PriceRepository(session)
        prices_by_symbol = {symbol: repo.get_prices(symbol) for symbol in symbols}

    signals: list[Signal] = []
    with_history = 0
    for symbol, prices in prices_by_symbol.items():
        if len(prices) < MIN_HISTORY_BARS:
            continue
        with_history += 1
        frame = compute_signal_frame(prices, thresholds)
        signals.extend(Signal(symbol, ts.date()) for ts in frame.index[frame["signal"]])

    return SignalCountReport(
        signals=signals,
        symbols_scanned=len(symbols),
        symbols_with_enough_history=with_history,
    )
