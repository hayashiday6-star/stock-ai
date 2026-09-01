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
from dataclasses import dataclass, field, replace

import pandas as pd

from stock_ai.data.schema import CLOSE, HIGH, LOW, VOLUME
from stock_ai.data.types import FinancialReport
from stock_ai.database.engine import Database
from stock_ai.database.repository import (
    FinancialStatementRepository,
    PriceRepository,
    list_securities,
)
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

#: Section 2's liquidity floor, in yen: D's close times D's volume.
#:
#: This replaced a market-cap floor. Market cap needs the shares outstanding
#: disclosed as of D, and that only exists for about five years back, which
#: silently truncated the whole study to 2021+. Turnover needs only the close
#: and the volume, so it is computable over the full history and cannot look
#: ahead by construction. It is also the same quantity section 4's size
#: constraint is expressed in.
DEFAULT_MIN_TURNOVER = 100_000_000.0

#: Trading days either side of a material date that the flags also cover.
#:
#: Symmetric on purpose: the volume a disclosure moves shows up the session
#: before (anticipation) and the session after (reaction), not only on the
#: day itself. This does mean a signal is dropped for an announcement that
#: lands the *next* day - fine for cleaning a research sample, but a live
#: screener must use the company's pre-announced 決算発表予定日 instead of
#: the realised disclosure date, or it would be using what it cannot know.
MATERIAL_WINDOW_DAYS = 1

#: Japan moved to T+2 settlement on this date; it was T+3 before.
#:
#: 権利付最終日 (the last day to buy and still be on the register) is that
#: many business days before the record date, so the flag's anchor moves
#: with it. The primary window (2021+) is entirely T+2; the long-history
#: secondary analysis crosses the change.
T_PLUS_2_FROM = dt.date(2019, 7, 16)

#: How far from a disclosure the earnings history is still taken to reach.
#:
#: A listed company reports at least annually, so a date with no disclosure
#: within this many days sits outside what the database knows: the flag can
#: be neither set nor cleared there, and the day is not material-free, it is
#: unexamined. Matches the EDINET annual-report search window.
EARNINGS_COVERAGE_DAYS = 400


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


def _flag_around(index: pd.DatetimeIndex, anchors: list[dt.date], window: int) -> pd.Series:
    """Flag every trading day within ``window`` sessions of any anchor date.

    The trading calendar is taken from ``index`` itself - the days this
    symbol actually traded - rather than from a holiday table. That is the
    calendar the windows are meant to be counted in, and it is exact for the
    symbol in question without needing a separate data source.

    An anchor that falls on a non-trading day (a record date on a Sunday, a
    disclosure published over a weekend) is pulled back to the last trading
    day on or before it, which is where its volume actually lands.
    """
    flags = pd.Series(False, index=index)
    if not anchors or len(index) == 0:
        return flags

    positions = index.searchsorted(pd.to_datetime(sorted(anchors)), side="right") - 1
    for position in positions:
        if position < 0:  # the anchor predates every bar this symbol has
            continue
        start = max(0, position - window)
        flags.iloc[start : position + window + 1] = True
    return flags


def earnings_flag_series(index: pd.DatetimeIndex, statements: list[FinancialReport]) -> pd.Series:
    """Flag trading days within ±1 session of a results disclosure.

    Section 3-1. Anchors are the ``disclosed_on`` dates already stored on the
    statements - a symbol with none gets an all-False series, which the
    caller must not read as "no earnings happened": see
    :func:`material_free_mask`.
    """
    anchors = [r.disclosed_on for r in statements if r.disclosed_on is not None]
    return _flag_around(index, anchors, MATERIAL_WINDOW_DAYS)


def record_dates(statements: list[FinancialReport], years: range) -> list[dt.date]:
    """The 権利確定日 a company's fiscal calendar implies, over ``years``.

    Japanese record dates sit on the fiscal year end (the year-end dividend
    and the AGM register) and on its half-year point (the interim dividend).
    Both follow from the fiscal year-end *date*, so one known fiscal calendar
    covers every year - which matters because the statements on file cover
    only a few years while the price history runs much longer.

    Returns an empty list when no statement carries a fiscal year-end date;
    the company's closing month is then simply unknown.
    """
    ends = [r.fiscal_year_end for r in statements if r.fiscal_year_end is not None]
    if not ends:
        return []

    # Any of them will do - a company's closing month is a property of the
    # company, not of the period. The newest is the one most likely to
    # reflect a fiscal-calendar change.
    month_day = max(ends)
    dates: list[dt.date] = []
    for year in years:
        try:
            year_end = month_day.replace(year=year)
        except ValueError:  # 2/29 in a non-leap year
            year_end = month_day.replace(year=year, day=month_day.day - 1)
        dates.append(year_end)
        # The half-year point. Subtracting six months lands on the previous
        # month's same day; the interim record date is the month end, which
        # is what a fiscal-period end always is.
        month = year_end.month - 6
        half_year = year - 1 if month <= 0 else year
        month = month + 12 if month <= 0 else month
        dates.append(_month_end(half_year, month))
    return dates


def _month_end(year: int, month: int) -> dt.date:
    """The last calendar day of ``year``-``month``."""
    if month == 12:
        return dt.date(year, 12, 31)
    return dt.date(year, month + 1, 1) - dt.timedelta(days=1)


def exrights_flag_series(index: pd.DatetimeIndex, statements: list[FinancialReport]) -> pd.Series:
    """Flag trading days within ±1 session of 権利付最終日 or 権利落ち日.

    Section 3-1. 権利付最終日 is two business days before the record date
    under T+2 (three before :data:`T_PLUS_2_FROM`), and 権利落ち日 is the
    session after it. Both anchors are flagged with their own ±1 window, so
    the covered stretch is four sessions around each record date.
    """
    if len(index) == 0:
        return pd.Series(False, index=index)

    years = range(index[0].year - 1, index[-1].year + 2)
    first, last = index[0].date(), index[-1].date()
    anchors: list[dt.date] = []
    for record_date in record_dates(statements, years):
        # Only record dates this series actually covers. Past the last bar,
        # ``searchsorted`` would clamp to that bar and invent an ex-rights day
        # at the end of every series; before the first, there is nothing to
        # count back from. The cost is under-flagging the final sessions of a
        # series, which is bounded and never fabricates a flag.
        if not first <= record_date <= last:
            continue
        # A record date on a holiday moves back to the preceding business day.
        position = index.searchsorted(pd.Timestamp(record_date), side="right") - 1
        if position < 0:
            continue
        settlement_days = 2 if record_date >= T_PLUS_2_FROM else 3
        last_with_rights = position - settlement_days
        if last_with_rights < 0:
            continue
        anchors.append(index[last_with_rights].date())  # 権利付最終日
        anchors.append(index[last_with_rights + 1].date())  # 権利落ち日
    return _flag_around(index, anchors, MATERIAL_WINDOW_DAYS)


def earnings_coverage(index: pd.DatetimeIndex, statements: list[FinancialReport]) -> pd.Series:
    """Dates the disclosure history can actually say anything about.

    A company reports at least once a year, so a date more than
    :data:`EARNINGS_COVERAGE_DAYS` from every disclosure on file sits in a
    stretch this database has no disclosures for. Nothing is known about
    whether it was an earnings day.

    This is per *date*, not per symbol, and that distinction was got wrong
    once already: checking only that a symbol had some disclosure somewhere
    marked a 2002 signal material-free on the strength of a 2026 filing.
    Every year before the disclosure history began was being reported as
    verified quiet when it had never been looked at.
    """
    disclosed = [r.disclosed_on for r in statements if r.disclosed_on is not None]
    if not disclosed:
        return pd.Series(False, index=index)

    margin = pd.Timedelta(days=EARNINGS_COVERAGE_DAYS)
    stamps = pd.to_datetime(sorted(disclosed))
    nearest = pd.Series(index.map(lambda day: stamps[abs(stamps - day).argmin()]), index=index)
    return (nearest - pd.Series(index, index=index)).abs() <= margin


def material_free_mask(
    index: pd.DatetimeIndex, statements: list[FinancialReport]
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Return ``(material_free, earnings_flag, exrights_flag)`` for ``index``.

    ``material_free`` is the pre-registration's primary subset: neither flag
    set **and** both flags actually evaluable. A symbol with no disclosure
    dates on file is not a symbol that never reported - it is one whose
    material days cannot be identified, and section 3-1 excludes those rather
    than let them pass as clean.
    """
    earnings = earnings_flag_series(index, statements)
    exrights = exrights_flag_series(index, statements)

    has_calendar = any(r.fiscal_year_end is not None for r in statements)
    covered = (
        earnings_coverage(index, statements) if has_calendar else pd.Series(False, index=index)
    )
    return covered & ~earnings & ~exrights, earnings, exrights


def market_cap_series(raw_close: pd.Series, statements: list[FinancialReport]) -> pd.Series:
    """Market cap at each date in ``raw_close``, section 2's "判定日時点の値".

    Args:
        raw_close: The *actually traded* close (:meth:`PriceRepository.
            get_raw_prices`, never :meth:`~PriceRepository.get_prices`'s
            split-adjusted one). A split changes the adjustment factor, not
            the real yen value the market put on the company that day -
            multiplying an adjusted close by an as-reported (never adjusted)
            share count would reintroduce the combined-scale mistake
            :func:`~stock_ai.data.schema.split_adjusted` exists to prevent,
            just from the other direction.
        statements: The security's full disclosure history, any period. Each
            report's ``shares_outstanding`` only becomes usable on its
            ``disclosed_on`` date - a report with neither is dropped, since a
            share count with no known disclosure date cannot be placed in
            time and would otherwise look known on every date.

    Returns:
        A series indexed like ``raw_close``, ``NaN`` before the first known
        disclosure with both fields set.
    """
    known = sorted(
        (report.disclosed_on, report.shares_outstanding)
        for report in statements
        if report.disclosed_on is not None and report.shares_outstanding is not None
    )
    if not known or raw_close.empty:
        return pd.Series(float("nan"), index=raw_close.index, name="market_cap")

    dates, shares = zip(*known, strict=True)
    # Both sides must share one datetime64 resolution before merge_asof will
    # compare them - a plain ``pd.to_datetime`` on ``dt.date`` objects can
    # land on a different one (e.g. seconds) than ``raw_close.index`` (e.g.
    # microseconds) despite both being midnight-dates, and merge_asof refuses
    # to compare mismatched resolutions rather than silently coercing.
    unit = raw_close.index.dtype
    disclosures = pd.DataFrame(
        {"date": pd.to_datetime(list(dates)).astype(unit), "shares": shares}
    ).sort_values("date")
    shares_asof = pd.merge_asof(
        pd.DataFrame({"date": raw_close.index}),
        disclosures,
        on="date",
        direction="backward",
    )
    return (
        pd.Series(shares_asof["shares"].to_numpy(), index=raw_close.index, name="market_cap")
        * raw_close
    )


@dataclass(frozen=True)
class Signal:
    """One symbol clearing all five conditions on one judgment date."""

    symbol: str
    date: dt.date
    #: Section 3-1's material-day flags. ``material_free`` is not simply
    #: ``not (earnings or exrights)``: a symbol whose disclosure dates are
    #: unknown has both flags False and is still not material-free, because
    #: nothing was checked. See :func:`material_free_mask`.
    earnings_flag: bool = False
    exrights_flag: bool = False
    material_free: bool = False


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

    - No independent market-segment check. Whatever is in ``symbols_scanned``
      is whatever ``stock-ai universe`` last loaded into this database - the
      ``Security`` table does not itself record Prime/Standard/Growth, so a
      database loaded with ``--segment all`` would count names section 2
      would exclude.
    - Delisted names are entirely absent. ``stock-ai universe`` only ever
      lists what trades today, so a name that delisted partway through the
      sample is invisible here and to the sealed run alike, until that gap is
      closed with its own point-in-time listing lookup.

    The market-cap filter (section 2's third requirement) *is* covered when
    ``count_signals`` is called with ``min_market_cap`` - see
    ``excluded_for_market_cap``.
    """

    signals: list[Signal] = field(default_factory=list)
    symbols_scanned: int = 0
    symbols_with_enough_history: int = 0
    #: Signals that cleared the 5 conditions but were dropped for being under
    #: ``min_market_cap`` on that date. ``None`` means the filter was not
    #: requested at all - never confuse that with "requested and found zero".
    excluded_for_market_cap: int | None = None
    #: Same, for the turnover floor.
    excluded_for_turnover: int | None = None
    #: The earliest date on which every requested filter could actually be
    #: evaluated, per the data on file. A thin first year usually means the
    #: data starts mid-year rather than the market being quiet.
    first_evaluable_date: dt.date | None = None

    @property
    def total(self) -> int:
        """Total (symbol, date) signals - the same date can repeat across symbols."""
        return len(self.signals)

    @property
    def material_free(self) -> SignalCountReport:
        """The pre-registration's primary subset: no earnings, no ex-rights.

        Section 3-2 judges on this, not on ``self``. The difference between
        the two is the answer to "how much of this signal was material-day
        volume all along".
        """
        return replace(self, signals=[s for s in self.signals if s.material_free])

    @property
    def earnings_count(self) -> int:
        """Signals flagged as sitting on or beside a results disclosure."""
        return sum(1 for s in self.signals if s.earnings_flag)

    @property
    def exrights_count(self) -> int:
        """Signals flagged as sitting on or beside an ex-rights date."""
        return sum(1 for s in self.signals if s.exrights_flag)

    @property
    def unflagged_but_unevaluable(self) -> int:
        """Signals neither flag fired on, yet which are not material-free.

        These are the symbols with no disclosure dates on file: nothing was
        checked, so nothing can be called clean. Counting them separately
        keeps "verified quiet" apart from "never looked".
        """
        return sum(
            1
            for s in self.signals
            if not s.material_free and not (s.earnings_flag or s.exrights_flag)
        )

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
    min_market_cap: float | None = None,
    min_turnover: float | None = None,
    flag_material_days: bool = False,
) -> SignalCountReport:
    """Scan stored JP symbols and count how often all five conditions align.

    Args:
        database: Source of the stored securities and their price history.
        symbols: Scan only these; defaults to every stored ``market="JP"``
            security (see :class:`SignalCountReport` for what that universe
            does and does not already exclude).
        thresholds: Overrides for the sensitivity sweep (section 8); the
            registered center values by default.
        min_market_cap: Section 2's *former* judgment-day market-cap floor
            (yen). Kept as a secondary measure - it needs shares outstanding
            disclosed as of D, which only exists for about five years back.
            ``None`` (the default) skips it.
        min_turnover: Section 2's liquidity floor (yen), D's close times D's
            volume - see :data:`DEFAULT_MIN_TURNOVER`. ``None`` skips it,
            which the sealed run must not do.
        flag_material_days: Evaluate section 3-1's earnings and ex-rights
            flags on every signal. Off by default because it needs the
            statement history for each symbol; the sealed run's primary
            subset depends on it.
    """
    needs_statements = min_market_cap is not None or flag_material_days
    with database.session() as session:
        if symbols is None:
            symbols = [sym for sym, market in list_securities(session) if market == "JP"]
        price_repo = PriceRepository(session)
        prices_by_symbol = {symbol: price_repo.get_prices(symbol) for symbol in symbols}
        raw_prices_by_symbol: dict[str, pd.DataFrame] = {}
        statements_by_symbol: dict[str, list[FinancialReport]] = {}
        if min_market_cap is not None or min_turnover is not None:
            # Both floors are yen amounts of actually-traded value, so both
            # need the unadjusted close: an adjusted close against a raw
            # volume understates turnover by the split factor for every bar
            # before a split.
            raw_prices_by_symbol = {symbol: price_repo.get_raw_prices(symbol) for symbol in symbols}
        if needs_statements:
            statement_repo = FinancialStatementRepository(session)
            statements_by_symbol = {
                symbol: statement_repo.get_reports(symbol, period=None) for symbol in symbols
            }

    signals: list[Signal] = []
    with_history = 0
    excluded_for_market_cap = 0
    excluded_for_turnover = 0
    first_evaluable: dt.date | None = None
    for symbol, prices in prices_by_symbol.items():
        if len(prices) < MIN_HISTORY_BARS:
            continue
        with_history += 1
        frame = compute_signal_frame(prices, thresholds)
        signal_mask = frame["signal"]
        statements = statements_by_symbol.get(symbol, [])

        if min_turnover is not None:
            raw = raw_prices_by_symbol[symbol]
            turnover = (raw[CLOSE] * raw[VOLUME]).reindex(frame.index)
            meets_floor = (turnover >= min_turnover).fillna(False)
            excluded_for_turnover += int((signal_mask & ~meets_floor).sum())
            signal_mask = signal_mask & meets_floor

        if min_market_cap is not None:
            market_cap = market_cap_series(raw_prices_by_symbol[symbol][CLOSE], statements).reindex(
                frame.index
            )
            meets_floor = (market_cap >= min_market_cap).fillna(False)
            excluded_for_market_cap += int((signal_mask & ~meets_floor).sum())
            signal_mask = signal_mask & meets_floor
            evaluable_from = market_cap.first_valid_index()
            if evaluable_from is not None:
                seen = evaluable_from.date()
                first_evaluable = seen if first_evaluable is None else min(first_evaluable, seen)

        if flag_material_days:
            material_free, earnings, exrights = material_free_mask(frame.index, statements)
            signals.extend(
                Signal(
                    symbol,
                    ts.date(),
                    earnings_flag=bool(earnings.loc[ts]),
                    exrights_flag=bool(exrights.loc[ts]),
                    material_free=bool(material_free.loc[ts]),
                )
                for ts in frame.index[signal_mask]
            )
        else:
            signals.extend(Signal(symbol, ts.date()) for ts in frame.index[signal_mask])

    return SignalCountReport(
        signals=signals,
        symbols_scanned=len(symbols),
        symbols_with_enough_history=with_history,
        excluded_for_market_cap=excluded_for_market_cap if min_market_cap is not None else None,
        excluded_for_turnover=excluded_for_turnover if min_turnover is not None else None,
        first_evaluable_date=first_evaluable,
    )


def explain_date(
    database: Database,
    on: dt.date,
    symbols: list[str] | None = None,
    thresholds: Signal5Thresholds | None = None,
    min_turnover: float | None = None,
) -> pd.DataFrame:
    """Why each symbol signalling on ``on`` is, or is not, material-free.

    Built to tell three explanations for an unflagged earnings day apart,
    because they need opposite fixes and the counts alone cannot separate
    them:

    - **Coverage.** ``disclosed`` is 1, or ``nearest_disclosed_days`` runs to
      hundreds: the disclosure history simply is not on file for this date,
      so no window could have caught it. Fetch more statements.
    - **Window.** ``nearest_disclosed_days`` is 2 or 3: the date *is* on
      file and the ±1 session window is too tight. Widen
      :data:`MATERIAL_WINDOW_DAYS`.
    - **Matching.** ``nearest_disclosed_days`` is 0 or 1 yet ``earnings``
      is False: the flag is not reading what is stored. A code fault.

    Returns one row per symbol that signalled on ``on``, or an empty frame
    when nothing did.
    """
    with database.session() as session:
        if symbols is None:
            symbols = [sym for sym, market in list_securities(session) if market == "JP"]
        price_repo = PriceRepository(session)
        statement_repo = FinancialStatementRepository(session)
        prices_by_symbol = {symbol: price_repo.get_prices(symbol) for symbol in symbols}
        raw_by_symbol = {symbol: price_repo.get_raw_prices(symbol) for symbol in symbols}
        statements_by_symbol = {
            symbol: statement_repo.get_reports(symbol, period=None) for symbol in symbols
        }

    stamp = pd.Timestamp(on)
    rows: list[dict[str, object]] = []
    for symbol, prices in prices_by_symbol.items():
        if len(prices) < MIN_HISTORY_BARS or stamp not in prices.index:
            continue
        frame = compute_signal_frame(prices, thresholds)
        if not bool(frame.loc[stamp, "signal"]):
            continue

        raw = raw_by_symbol[symbol]
        turnover = float(raw.loc[stamp, CLOSE] * raw.loc[stamp, VOLUME])
        if min_turnover is not None and turnover < min_turnover:
            continue

        statements = statements_by_symbol[symbol]
        material_free, earnings, exrights = material_free_mask(frame.index, statements)
        disclosed = sorted(r.disclosed_on for r in statements if r.disclosed_on is not None)
        # Distance in *trading* sessions, which is what the window counts in.
        position = frame.index.get_loc(stamp)
        nearest_days: int | None = None
        nearest_date: dt.date | None = None
        for date in disclosed:
            other = frame.index.searchsorted(pd.Timestamp(date), side="right") - 1
            if other < 0:
                continue
            distance = abs(int(position) - int(other))
            if nearest_days is None or distance < nearest_days:
                nearest_days, nearest_date = distance, date

        rows.append(
            {
                "symbol": symbol,
                "volume_multiple": round(float(frame.loc[stamp, "volume_multiple"]), 2),
                "turnover": turnover,
                "statements": len(statements),
                "disclosed": len(disclosed),
                "earliest_disclosed": disclosed[0] if disclosed else None,
                "latest_disclosed": disclosed[-1] if disclosed else None,
                "nearest_disclosed": nearest_date,
                "nearest_disclosed_days": nearest_days,
                "fiscal_year_end": next(
                    (r.fiscal_year_end for r in statements if r.fiscal_year_end is not None), None
                ),
                "earnings": bool(earnings.loc[stamp]),
                "exrights": bool(exrights.loc[stamp]),
                "material_free": bool(material_free.loc[stamp]),
            }
        )
    return pd.DataFrame(rows)


@dataclass(frozen=True)
class MarketVolumeContext:
    """How busy the whole market was on one date, in condition 2's own terms.

    Condition 2 compares a symbol's volume with its own 20-session average and
    nothing else. On a day the entire market trades twice its normal size, a
    5x reading is a smaller event than the same reading on a quiet day, and
    the condition cannot tell the two apart. This measures the difference so
    a concentration of signals on earnings-season days can be checked against
    the market's own volume rather than guessed at.
    """

    on: dt.date
    symbols_measured: int
    median_multiple: float
    #: Symbols clearing condition 2 alone - no other condition applied.
    over_5x: int
    over_2x: int


def market_volume_context(
    database: Database, on: dt.date, symbols: list[str] | None = None
) -> MarketVolumeContext:
    """Measure the market-wide volume multiple on ``on``.

    Compare a suspect date against ordinary ones: if the median symbol traded
    near its own average while a handful spiked, the concentration is about
    those names; if the median itself is elevated, the day is.
    """
    with database.session() as session:
        if symbols is None:
            symbols = [sym for sym, market in list_securities(session) if market == "JP"]
        repo = PriceRepository(session)
        prices_by_symbol = {symbol: repo.get_prices(symbol) for symbol in symbols}

    stamp = pd.Timestamp(on)
    multiples: list[float] = []
    for prices in prices_by_symbol.values():
        if len(prices) < VOLUME_WINDOW + 2 or stamp not in prices.index:
            continue
        volume = prices[VOLUME]
        prior = volume.rolling(VOLUME_WINDOW).mean().shift(1)
        value = volume.loc[stamp] / prior.loc[stamp]
        if pd.notna(value):
            multiples.append(float(value))

    series = pd.Series(multiples, dtype="float64")
    return MarketVolumeContext(
        on=on,
        symbols_measured=len(series),
        median_multiple=float(series.median()) if not series.empty else float("nan"),
        over_5x=int((series >= 5.0).sum()),
        over_2x=int((series >= 2.0).sum()),
    )
