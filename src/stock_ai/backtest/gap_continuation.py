"""Measure the gap-continuation screener against pre-registered criteria.

The strategy under test is ``integrated_screener_v4_prod.py``: after the close
of day *D* it looks for a stock that gapped up at *D*'s open, held its gain
into *D*'s close, and traded on heavily expanded volume; the position is then
opened at *D+1*'s open and closed at *D+1*'s close.

This module only *measures*. Every threshold below is copied from the
production screener and is never tuned here - the parameter sweep in
:func:`sweep_shells` is diagnostic, not selective. Reading a better number off
a neighbouring threshold and adopting it would be the in-sample fitting this
measurement exists to audit.

What is being tested is a bet on *continuation after* a gap, not a bet on
capturing the gap itself. The gap has already happened and is already in the
price by the time the position opens; the position lives entirely inside the
following session.

The acceptance criteria were fixed in advance, before any number was
computed, and are encoded in :data:`CRITERIA`:

1. ``median / IQR >= 0.10`` on the TOPIX-deducted return, with the median
   itself reported beside it so a wide-median/narrow-IQR pass cannot hide a
   result that round-trip costs would erase.
2. The gap threshold's neighbourhood is a hill rather than a spike: both
   shells adjacent to the production ``[8, 9)`` band reach at least half its
   ratio.
3. The median is distinguishable from zero by a sign test at the *clustered*
   sample size.

All three must hold. Below :data:`MIN_TRADES` trades or :data:`MIN_SYMBOLS`
symbols the answer is "undecidable" rather than either verdict, and that gate
is evaluated before any criterion is computed (see :func:`verdict`).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
import pandas as pd

from stock_ai.core.logging import get_logger
from stock_ai.data.schema import ADJ_CLOSE, CLOSE, HIGH, LOW, OPEN, VOLUME

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Production parameters, mirrored verbatim. Never tuned in this module.
# ---------------------------------------------------------------------------

PRICE_MIN = 500.0
PRICE_MAX = 10_000.0
PREV_VO_MIN = 50_000.0
GAP_MIN = 8.0
GAP_MAX = 30.0
DAY_GAIN_MIN = 1.0
HIGH_KEEP_MIN = 99.0
VO_RATIO_MIN = 5.0
TURNOVER_MIN = 50_000_000.0
MAX_SIGNALS = 3

#: The regime gate: today's benchmark close must hold within half a percent of
#: its own five-day mean, that mean including today. Mirrored from the
#: screener, which reads it off the 1306 ETF; this module reads it off TOPIX
#: itself. The ETF tracks the index, and TOPIX is already loaded here for the
#: excess-return deduction, so the substitution removes a data dependency
#: without changing the rule.
REGIME_WINDOW = 5
REGIME_TOLERANCE = 0.995

# ---------------------------------------------------------------------------
# The in-sample / out-of-sample boundary.
# ---------------------------------------------------------------------------

#: First day of the window the production parameters were fitted on, per the
#: header comment in ``integrated_screener_v4_prod.py``: "検証期間: 2024/10/01
#: 〜 2026/06/30". Everything strictly before this date was not available to
#: whoever chose 8% and 5x, and is therefore genuinely out of sample.
#:
#: This is a module constant on purpose. It is not a function parameter and
#: not a CLI flag: a boundary that can be passed in is a boundary that can be
#: moved after the numbers are in, and moving it is precisely the failure this
#: split exists to detect. :func:`split_is_oos` takes no date argument.
IS_START = dt.date(2024, 10, 1)

# ---------------------------------------------------------------------------
# Pre-registered acceptance criteria.
# ---------------------------------------------------------------------------

#: Minimum ratio of median to interquartile range. The disclosure study that
#: preceded this one returned 0.019 on a comparable window; an order of
#: magnitude above that is the bar for calling this a different result.
MEDIAN_OVER_IQR_MIN = 0.10

#: A shell adjacent to the production band must reach this fraction of the
#: band's ratio, or the band is an isolated spike rather than a hilltop.
NEIGHBOUR_FRACTION = 0.5

#: Two-sided z at which the sign test counts as distinguishable from chance.
SIGN_TEST_Z = 2.0

#: Below either of these the result is undecidable, whatever it looks like.
#: Without this gate a twelve-trade sample can clear all three criteria on
#: noise alone.
MIN_TRADES = 100
MIN_SYMBOLS = 50

CRITERIA = (
    f"median/IQR >= {MEDIAN_OVER_IQR_MIN}",
    f"both neighbouring shells >= {NEIGHBOUR_FRACTION:.0%} of the [8,9) shell",
    f"sign test |z| >= {SIGN_TEST_Z} at the clustered sample size",
)

#: Disjoint gap bands. The production threshold is a *floor*, so sweeping it
#: over 6/7/8/9/10 yields nested samples - the 10% set sits inside the 9% set
#: inside the 8% set - which share most of their trades and therefore cannot
#: show a spike even when one exists. These bands are disjoint, so each is an
#: independent sample and "is 8% special" becomes an answerable question.
GAP_SHELLS: tuple[tuple[float, float], ...] = (
    (6.0, 7.0),
    (7.0, 8.0),
    (8.0, 9.0),
    (9.0, 10.0),
    (10.0, GAP_MAX),
)

#: The shell the production parameter selects into, whose neighbours decide
#: criterion 2.
PRODUCTION_SHELL = (8.0, 9.0)

#: Floors the *sweep* runs at, below the production ones on purpose.
#:
#: A run screened at ``GAP_MIN`` never produces a trade that gapped 7%, so the
#: band below the production band is empty by construction and criterion 2
#: cannot be satisfied whatever the market did. Seeing the neighbourhood at
#: all requires collecting trades the strategy would decline, which is exactly
#: what "is 8% the top of a hill or an isolated spike" asks for.
#:
#: One factor moves at a time. The gap sweep holds the volume ratio at its
#: production value and vice versa, so every band stays directly comparable to
#: the production band rather than differing in two ways at once.
SWEEP_GAP_MIN = 6.0
SWEEP_VO_MIN = 3.0

#: Disjoint volume-ratio bands, for the same reason.
VO_SHELLS: tuple[tuple[float, float], ...] = (
    (3.0, 4.0),
    (4.0, 5.0),
    (5.0, 6.0),
    (6.0, 7.0),
    (7.0, float("inf")),
)


# ---------------------------------------------------------------------------
# Panel construction
# ---------------------------------------------------------------------------


def build_panel(prices_by_symbol: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Pivot per-symbol OHLCV frames into date x symbol matrices.

    The screener asks a question about the whole market on a single day, so
    the natural shape is one matrix per field rather than one frame per
    symbol.

    Returns:
        A dict keyed by field name (``open``, ``high``, ``low``, ``close``,
        ``adj_close``, ``volume``), each a frame indexed by date with one
        column per symbol.
    """
    fields = (OPEN, HIGH, LOW, CLOSE, ADJ_CLOSE, VOLUME)
    columns: dict[str, dict[str, pd.Series]] = {field: {} for field in fields}
    for symbol, frame in prices_by_symbol.items():
        if frame is None or frame.empty:
            continue
        for field in fields:
            if field in frame.columns:
                columns[field][symbol] = pd.to_numeric(frame[field], errors="coerce")

    panel: dict[str, pd.DataFrame] = {}
    for field in fields:
        if columns[field]:
            panel[field] = pd.DataFrame(columns[field]).sort_index()
        else:
            panel[field] = pd.DataFrame()
    return panel


def _split_factor(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Per-session multiplier carrying raw prices onto the adjusted scale.

    ``adj_close / close`` on the same session. Applied to that session's open,
    high and low it puts every price on one continuous scale, so a ratio taken
    across a split boundary measures the move rather than the split.
    """
    raw = panel[CLOSE]
    adjusted = panel[ADJ_CLOSE]
    factor = adjusted / raw.replace(0.0, np.nan)
    return factor.replace([np.inf, -np.inf], np.nan)


def _adjusted_volume(panel: dict[str, pd.DataFrame], factor: pd.DataFrame) -> pd.DataFrame:
    """Share volume restated on the adjusted price scale.

    A two-for-one split doubles the share count, so raw volume jumps by the
    same factor with nothing having traded differently. Left alone that lands
    straight in ``VoRatio`` and manufactures a signal: the production screener
    compares raw volume across the split boundary and would read a clean 2.0x
    on a day nobody bought anything.

    Volume divides by the factor that prices multiply by, which is what keeps
    ``price * volume`` - the turnover the money actually moved - invariant
    across the boundary.
    """
    return panel[VOLUME] / factor


# ---------------------------------------------------------------------------
# The screen
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScreenInputs:
    """Everything the screen reads, already on one price scale."""

    open_: pd.DataFrame
    high: pd.DataFrame
    close: pd.DataFrame
    volume: pd.DataFrame
    raw_close: pd.DataFrame


def _screen_inputs(panel: dict[str, pd.DataFrame]) -> ScreenInputs:
    factor = _split_factor(panel)
    return ScreenInputs(
        open_=panel[OPEN] * factor,
        high=panel[HIGH] * factor,
        close=panel[ADJ_CLOSE],
        volume=_adjusted_volume(panel, factor),
        raw_close=panel[CLOSE],
    )


def screen_day(
    inputs: ScreenInputs,
    day: pd.Timestamp,
    prev_day: pd.Timestamp,
    gap_min: float = GAP_MIN,
    vo_ratio_min: float = VO_RATIO_MIN,
    max_signals: int | None = MAX_SIGNALS,
) -> pd.DataFrame:
    """Run the screen over one session's bars.

    Every quantity here is final at ``day``'s close:

    ===================  ==========================================  ==============
    quantity             inputs                                      known by
    ===================  ==========================================  ==============
    ``gap_pct``          ``day`` open, ``prev_day`` close            ``day`` 09:00
    ``day_gain``         ``day`` close, ``day`` open                 ``day`` close
    ``high_keep``        ``day`` close, ``day`` high                 ``day`` close
    ``vo_ratio``         ``day`` volume, ``prev_day`` volume         ``day`` close
    ``turnover``         ``day`` close, ``day`` volume               ``day`` close
    ===================  ==========================================  ==============

    Nothing from the session the position trades in is read here. The price
    band is tested against the *raw* close, because that is the number a
    broker quotes and the reason the band exists.

    Returns:
        One row per surviving symbol, ranked and capped at
        :data:`MAX_SIGNALS`, or an empty frame.
    """
    if day not in inputs.close.index or prev_day not in inputs.close.index:
        return pd.DataFrame()

    open_ = inputs.open_.loc[day]
    high = inputs.high.loc[day]
    close = inputs.close.loc[day]
    volume = inputs.volume.loc[day]
    raw_close = inputs.raw_close.loc[day]
    prev_close = inputs.close.loc[prev_day]
    prev_volume = inputs.volume.loc[prev_day]

    frame = pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "close": close,
            "raw_close": raw_close,
            "volume": volume,
            "prev_close": prev_close,
            "prev_volume": prev_volume,
        }
    ).dropna(subset=["open", "high", "close", "raw_close", "prev_close", "prev_volume"])
    if frame.empty:
        return pd.DataFrame()

    # Guard the denominators before dividing rather than filtering NaNs after:
    # a zero previous close would otherwise produce an infinite gap that sails
    # through GAP_MAX as a comparison against inf.
    frame = frame[(frame["prev_close"] > 0) & (frame["open"] > 0) & (frame["high"] > 0)]
    if frame.empty:
        return pd.DataFrame()

    frame["gap_pct"] = (frame["open"] - frame["prev_close"]) / frame["prev_close"] * 100.0
    frame["day_gain"] = (frame["close"] - frame["open"]) / frame["open"] * 100.0
    frame["high_keep"] = frame["close"] / frame["high"] * 100.0
    frame["vo_ratio"] = np.where(
        frame["prev_volume"] > 0, frame["volume"] / frame["prev_volume"], np.nan
    )
    # Both sides on the adjusted scale, which multiplies out to exactly the
    # raw close times the raw volume - the yen that changed hands.
    frame["turnover"] = frame["close"] * frame["volume"]

    selected = frame[
        (frame["raw_close"] >= PRICE_MIN)
        & (frame["raw_close"] <= PRICE_MAX)
        & (frame["prev_volume"] >= PREV_VO_MIN)
        & (frame["gap_pct"] >= gap_min)
        & (frame["gap_pct"] <= GAP_MAX)
        & (frame["day_gain"] >= DAY_GAIN_MIN)
        & (frame["high_keep"] >= HIGH_KEEP_MIN)
        & (frame["vo_ratio"] >= vo_ratio_min)
        & (frame["turnover"] >= TURNOVER_MIN)
    ].copy()
    if selected.empty:
        return pd.DataFrame()

    selected["score"] = selected["gap_pct"] * selected["vo_ratio"]
    if max_signals is None:
        return selected.sort_values("score", ascending=False)
    return selected.nlargest(max_signals, "score")


def regime_ok(topix: pd.DataFrame, day: pd.Timestamp) -> bool:
    """Whether the benchmark is holding its own five-day mean on ``day``.

    The mean includes ``day``'s own close, exactly as the screener computes
    it. Both the close and the mean are known at ``day``'s close, so the gate
    reads nothing from the session the trade happens in.

    Fewer than :data:`REGIME_WINDOW` closes available means the gate cannot be
    evaluated, and this returns ``False``. The production screener returns
    ``True`` in that case - it fails open, silently disabling a risk filter
    across holiday-heavy weeks. Failing closed here keeps a day the strategy
    could not actually have judged out of the sample instead of counting it as
    a green light.
    """
    closes = topix[CLOSE].loc[:day]
    if len(closes) < REGIME_WINDOW:
        return False
    window = closes.iloc[-REGIME_WINDOW:]
    if window.isna().any():
        return False
    return bool(window.iloc[-1] >= window.mean() * REGIME_TOLERANCE)


# ---------------------------------------------------------------------------
# Entry, exit and the excess return
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Fill:
    """The two prices a trade is booked at, and whether they were reachable."""

    entry: float | None
    exit_: float | None
    locked: bool
    reason: str | None


def price_the_trade(
    inputs: ScreenInputs,
    panel: dict[str, pd.DataFrame],
    symbol: str,
    entry_day: pd.Timestamp,
) -> Fill:
    """Price one trade at the entry session's open and close.

    Both prices come from ``entry_day``, the first session that opens after
    the signal is known. The signal is complete at the previous close, so
    neither price is available to the screen that produced it.

    Both are put on the adjusted scale by the same session's factor. Within
    one session that factor cancels in the ratio and changes nothing; it is
    applied anyway so this function cannot be moved to a multi-session window
    later and quietly start booking splits as returns.

    ``locked`` marks a session whose open, high and low are identical. A stock
    that gapped 8% on heavy volume can open the next morning bid-limit with no
    trade printing until late in the day, and the opening auction a backtest
    happily fills at may never have existed. Those rows are kept and counted,
    not silently dropped, so the headline can be read with and without them.
    """
    if entry_day not in inputs.close.index:
        return Fill(None, None, False, "no_entry_session")
    if symbol not in inputs.close.columns:
        return Fill(None, None, False, "symbol_absent")

    entry = inputs.open_.at[entry_day, symbol]
    exit_ = inputs.close.at[entry_day, symbol]
    if pd.isna(entry) or pd.isna(exit_) or entry <= 0:
        return Fill(None, None, False, "no_entry_price")

    high = panel[HIGH].at[entry_day, symbol] if symbol in panel[HIGH].columns else np.nan
    low = panel[LOW].at[entry_day, symbol] if symbol in panel[LOW].columns else np.nan
    raw_open = panel[OPEN].at[entry_day, symbol]
    locked = bool(
        not pd.isna(high)
        and not pd.isna(low)
        and not pd.isna(raw_open)
        and high == low
        and raw_open == high
    )
    return Fill(float(entry), float(exit_), locked, None)


def benchmark_return(topix: pd.DataFrame, day: pd.Timestamp) -> float | None:
    """TOPIX's own open-to-close move on ``day``.

    Deducted over the same window the position is held, not close-to-close: a
    close-to-close benchmark against an open-to-close position would charge
    the trade for an overnight index move it was never exposed to.
    """
    if day not in topix.index:
        return None
    if OPEN not in topix.columns or CLOSE not in topix.columns:
        return None
    open_ = topix.at[day, OPEN]
    close = topix.at[day, CLOSE]
    if pd.isna(open_) or pd.isna(close) or open_ == 0:
        return None
    return float(close / open_ - 1.0)


TRADE_COLUMNS = [
    "signal_date",
    "entry_date",
    "symbol",
    "gap_pct",
    "vo_ratio",
    "day_gain",
    "high_keep",
    "turnover",
    "entry_price",
    "exit_price",
    "stock_return",
    "topix_return",
    "excess_return",
    "locked_open",
]


def run_backtest(
    prices_by_symbol: dict[str, pd.DataFrame],
    topix: pd.DataFrame,
    gap_min: float = GAP_MIN,
    vo_ratio_min: float = VO_RATIO_MIN,
    max_signals: int | None = MAX_SIGNALS,
) -> pd.DataFrame:
    """Walk the trading calendar and price every signal the screen fires.

    The calendar is TOPIX's own index, so holidays are skipped because they
    are absent from it rather than because a weekday rule guessed at them.
    The production screener walks back by weekday only and lands on holidays,
    which is why it reported a false "market closed" on 2026-08-12.

    ``max_signals`` is the production cap for the run the verdict is read
    from, and ``None`` for a sweep. Capping a sweep would let a widened floor
    change which trades the *production* band contains: the rank is gap times
    volume ratio, so a 6.5% gap on twenty times volume outranks an 8.5% gap on
    six, and would take a slot the strategy gave to the latter. Uncapped, each
    band keeps its own full sample and the bands stay comparable.
    """
    panel = build_panel(prices_by_symbol)
    if panel[CLOSE].empty or topix.empty:
        return pd.DataFrame(columns=TRADE_COLUMNS)

    inputs = _screen_inputs(panel)
    calendar = topix.index

    rows: list[dict[str, object]] = []
    # Start at 1 so a previous session exists, stop at -1 so an entry session
    # does: a signal on the last day of the data has nowhere to trade.
    for position in range(1, len(calendar) - 1):
        day = calendar[position]
        prev_day = calendar[position - 1]
        entry_day = calendar[position + 1]

        if not regime_ok(topix, day):
            continue
        signals = screen_day(inputs, day, prev_day, gap_min, vo_ratio_min, max_signals)
        if signals.empty:
            continue

        topix_move = benchmark_return(topix, entry_day)
        if topix_move is None:
            continue

        for symbol, signal in signals.iterrows():
            fill = price_the_trade(inputs, panel, str(symbol), entry_day)
            if fill.entry is None or fill.exit_ is None:
                continue
            stock_move = fill.exit_ / fill.entry - 1.0
            rows.append(
                {
                    "signal_date": day.date(),
                    "entry_date": entry_day.date(),
                    "symbol": str(symbol),
                    "gap_pct": float(signal["gap_pct"]),
                    "vo_ratio": float(signal["vo_ratio"]),
                    "day_gain": float(signal["day_gain"]),
                    "high_keep": float(signal["high_keep"]),
                    "turnover": float(signal["turnover"]),
                    "entry_price": fill.entry,
                    "exit_price": fill.exit_,
                    "stock_return": stock_move,
                    "topix_return": topix_move,
                    "excess_return": stock_move - topix_move,
                    "locked_open": fill.locked,
                }
            )

    logger.info("Priced %d gap-continuation trade(s)", len(rows))
    return pd.DataFrame(rows, columns=TRADE_COLUMNS)


# ---------------------------------------------------------------------------
# The split
# ---------------------------------------------------------------------------


def split_is_oos(trades: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Cut the trades at :data:`IS_START` into (out-of-sample, in-sample).

    Takes no date argument. The boundary is a module constant fixed before any
    result was computed, and the only way to move it is to edit the source and
    break :func:`tests.test_gap_continuation` - which asserts its value - in
    the same commit.

    The verdict is read off the out-of-sample half. The in-sample half is
    reported beside it, and the distance between them is the size of the
    overfit.
    """
    if trades.empty:
        return trades, trades
    signal_dates = pd.to_datetime(trades["signal_date"])
    boundary = pd.Timestamp(IS_START)
    return trades[signal_dates < boundary].copy(), trades[signal_dates >= boundary].copy()


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClusterCounts:
    """Sample sizes under each unit of independence."""

    trades: int
    symbols: int
    dates: int
    max_per_date: int

    @property
    def effective(self) -> int:
        """The binding sample size: the smaller of the two clusterings.

        Two things make trades non-independent, and they are not the same
        thing. The same symbol can fire repeatedly, which company-clustering
        handles. And several symbols fire on one strong-tape morning, whose
        returns then share that morning's market move - ten signals on a day
        the whole market gapped are closer to one observation than to ten.
        Whichever is scarcer is the honest denominator.
        """
        return min(self.symbols, self.dates)


def cluster_counts(trades: pd.DataFrame) -> ClusterCounts:
    """Count trades, distinct symbols, distinct signal dates and the daily max."""
    if trades.empty:
        return ClusterCounts(0, 0, 0, 0)
    per_date = trades.groupby("signal_date").size()
    return ClusterCounts(
        trades=int(len(trades)),
        symbols=int(trades["symbol"].nunique()),
        dates=int(trades["signal_date"].nunique()),
        max_per_date=int(per_date.max()),
    )


def collapse_to_clusters(trades: pd.DataFrame, by: str, column: str) -> pd.Series:
    """Reduce each cluster to one observation: its median return.

    Swapping the denominator in a standard error is the cheap half of a
    cluster correction. Collapsing first is the honest half - it removes the
    within-cluster correlation instead of only discounting it, so a day that
    fired three correlated signals contributes one number rather than three.
    """
    if trades.empty:
        return pd.Series(dtype="float64")
    values = pd.to_numeric(trades[column], errors="coerce")
    frame = pd.DataFrame({by: trades[by], "value": values}).dropna(subset=["value"])
    if frame.empty:
        return pd.Series(dtype="float64")
    return frame.groupby(by)["value"].median()


@dataclass(frozen=True)
class SignTest:
    """A sign test of the median against zero, run on collapsed clusters."""

    unit: str
    n: int
    positive: int
    share: float
    z: float

    @property
    def significant(self) -> bool:
        """Whether the share of positive clusters clears the pre-set z."""
        return bool(abs(self.z) >= SIGN_TEST_Z)


def sign_test(trades: pd.DataFrame, by: str, column: str = "excess_return") -> SignTest:
    """Test the median against zero with ``by`` as the unit of independence.

    A share of positive clusters above one half is the same statement as a
    median above zero, so this is the test criterion 1's median needs and not
    a second, looser question about win rate.
    """
    collapsed = collapse_to_clusters(trades, by, column)
    n = int(collapsed.size)
    if n == 0:
        return SignTest(by, 0, 0, float("nan"), float("nan"))
    positive = int((collapsed > 0).sum())
    share = positive / n
    standard_error = np.sqrt(0.25 / n)
    return SignTest(by, n, positive, share, float((share - 0.5) / standard_error))


def binding_sign_test(trades: pd.DataFrame, column: str = "excess_return") -> SignTest:
    """The weaker of the symbol-clustered and date-clustered sign tests."""
    by_symbol = sign_test(trades, "symbol", column)
    by_date = sign_test(trades, "signal_date", column)
    if np.isnan(by_symbol.z):
        return by_date
    if np.isnan(by_date.z):
        return by_symbol
    return by_symbol if abs(by_symbol.z) <= abs(by_date.z) else by_date


# ---------------------------------------------------------------------------
# Distribution statistics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Distribution:
    """Median, quartiles and the ratio criterion 1 is stated in."""

    n: int
    median: float
    p25: float
    p75: float
    iqr: float
    median_over_iqr: float
    mean: float


def describe(trades: pd.DataFrame, column: str = "excess_return") -> Distribution:
    """Summarise a return column, reporting the median beside its own ratio.

    The median is carried explicitly, not only inside the ratio: a narrow
    distribution can clear ``median / IQR >= 0.10`` on a median too small to
    survive the spread and commission of buying an eight-percent gapper at the
    open, and the ratio alone would not show it.
    """
    values = pd.to_numeric(trades[column], errors="coerce").dropna() if not trades.empty else None
    if values is None or values.empty:
        nan = float("nan")
        return Distribution(0, nan, nan, nan, nan, nan, nan)
    median = float(values.median())
    p25 = float(values.quantile(0.25))
    p75 = float(values.quantile(0.75))
    iqr = p75 - p25
    return Distribution(
        n=int(values.size),
        median=median,
        p25=p25,
        p75=p75,
        iqr=iqr,
        median_over_iqr=median / iqr if iqr > 0 else float("nan"),
        mean=float(values.mean()),
    )


# ---------------------------------------------------------------------------
# The shell sweep
# ---------------------------------------------------------------------------


def sweep_shells(
    trades: pd.DataFrame,
    shells: tuple[tuple[float, float], ...] = GAP_SHELLS,
    column: str = "gap_pct",
) -> pd.DataFrame:
    """Summarise each disjoint band separately.

    Diagnostic only. A shell that scores better than the production band is
    not a reason to move the parameter - moving it would refit on exactly the
    sample this measurement is auditing.
    """
    if trades.empty:
        return pd.DataFrame(
            columns=["shell", "low", "high", "n", "symbols", "median", "iqr", "median_over_iqr"]
        )

    values = pd.to_numeric(trades[column], errors="coerce")
    rows = []
    for low, high in shells:
        band = trades[(values >= low) & (values < high)]
        stats = describe(band)
        label = f"[{low:g},{high:g})" if np.isfinite(high) else f"[{low:g},inf)"
        rows.append(
            {
                "shell": label,
                "low": low,
                "high": high,
                "n": stats.n,
                "symbols": int(band["symbol"].nunique()) if not band.empty else 0,
                "median": stats.median,
                "iqr": stats.iqr,
                "median_over_iqr": stats.median_over_iqr,
            }
        )
    return pd.DataFrame(rows)


@dataclass(frozen=True)
class HillTest:
    """Whether the production band sits on a hill or on an isolated spike."""

    band: float
    left: float
    right: float
    is_hill: bool
    reason: str


def hill_test(shells: pd.DataFrame) -> HillTest:
    """Criterion 2: both neighbours of ``[8,9)`` reach half its ratio.

    Read off the disjoint shells rather than the nested thresholds. Sweeping a
    floor produces samples that contain one another, so adjacent points share
    most of their trades and the curve is smooth by construction - a spike
    cannot appear there even when the underlying band is one.
    """
    nan = float("nan")
    if shells.empty:
        return HillTest(nan, nan, nan, False, "no shells")

    indexed = shells.set_index("low")["median_over_iqr"]
    low = PRODUCTION_SHELL[0]
    if low not in indexed.index:
        return HillTest(nan, nan, nan, False, "production shell absent")

    order = list(indexed.index)
    position = order.index(low)
    band = float(indexed.loc[low])
    left = float(indexed.iloc[position - 1]) if position > 0 else nan
    right = float(indexed.iloc[position + 1]) if position + 1 < len(order) else nan

    if np.isnan(band):
        return HillTest(band, left, right, False, "production shell has no ratio")
    threshold = band * NEIGHBOUR_FRACTION
    # A negative band makes "half of it" a nonsense bar - the strategy is not
    # on a hill if its own centre is under water.
    if band <= 0:
        return HillTest(band, left, right, False, "production shell ratio is not positive")

    neighbours = [value for value in (left, right) if not np.isnan(value)]
    if len(neighbours) < 2:
        return HillTest(band, left, right, False, "a neighbouring shell is empty")
    if all(value >= threshold for value in neighbours):
        return HillTest(band, left, right, True, "both neighbours reach half the band")
    return HillTest(band, left, right, False, "an isolated spike: a neighbour falls below half")


# ---------------------------------------------------------------------------
# The verdict
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Coverage:
    """Whether the sample is large enough for the question to be asked."""

    trades: int
    symbols: int
    sufficient: bool
    reason: str


def coverage_gate(trades: pd.DataFrame) -> Coverage:
    """Decide whether the sample can carry a verdict at all.

    Evaluated from counts only. It never looks at a return, so it cannot be
    influenced by how the returns came out.
    """
    counts = cluster_counts(trades)
    if counts.trades < MIN_TRADES:
        return Coverage(
            counts.trades,
            counts.symbols,
            False,
            f"{counts.trades} trades, below the {MIN_TRADES} fixed in advance",
        )
    if counts.symbols < MIN_SYMBOLS:
        return Coverage(
            counts.trades,
            counts.symbols,
            False,
            f"{counts.symbols} symbols, below the {MIN_SYMBOLS} fixed in advance",
        )
    return Coverage(counts.trades, counts.symbols, True, "sample large enough to decide")


@dataclass(frozen=True)
class Verdict:
    """The pre-registered decision, or the refusal to make one."""

    outcome: str
    coverage: Coverage
    distribution: Distribution | None
    hill: HillTest | None
    sign: SignTest | None
    passed: dict[str, bool] | None

    @property
    def undecidable(self) -> bool:
        """Whether the sample was too thin to answer either way."""
        return self.outcome == "undecidable"


def verdict(trades: pd.DataFrame, gap_sweep: pd.DataFrame) -> Verdict:
    """Apply the three pre-registered criteria, coverage gate first.

    ``trades`` is the production sample, screened at the shipped thresholds;
    criteria 1 and 3 are read from it. ``gap_sweep`` is the widened sample
    that reaches below :data:`GAP_MIN`, and only criterion 2 reads it - the
    band below the production band does not exist in ``trades``, because the
    screen rejected those setups before they could become trades.

    It is a required argument rather than one defaulting to ``trades``: a
    default would silently evaluate criterion 2 against a sample whose left
    neighbour is empty, and score it a failure for a reason that has nothing
    to do with the market.

    The gate runs before any return is touched and returns early when it
    fails, so a thin sample yields ``undecidable`` with no criteria computed
    at all - there is nothing for a small lucky sample to pass. That ordering
    is the point, and :func:`tests.test_gap_continuation` asserts the criteria
    are absent on a short frame.

    All three criteria must hold. Any one failing is a withdrawal, as agreed
    before the numbers existed.
    """
    coverage = coverage_gate(trades)
    if not coverage.sufficient:
        return Verdict("undecidable", coverage, None, None, None, None)

    distribution = describe(trades)
    hill = hill_test(sweep_shells(gap_sweep))
    sign = binding_sign_test(trades)

    passed = {
        CRITERIA[0]: bool(
            not np.isnan(distribution.median_over_iqr)
            and distribution.median_over_iqr >= MEDIAN_OVER_IQR_MIN
        ),
        CRITERIA[1]: hill.is_hill,
        CRITERIA[2]: sign.significant and sign.share > 0.5,
    }
    outcome = "pass" if all(passed.values()) else "withdraw"
    return Verdict(outcome, coverage, distribution, hill, sign, passed)
