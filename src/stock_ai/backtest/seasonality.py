"""Calendar-month seasonality: does a name really rise every September?

The question is easy to ask and unusually easy to get wrong, so read this
before reading the numbers.

**A seasonality scan is a machine for manufacturing false positives**, and the
rate is far higher than the usual five-percent intuition suggests. Measured on
300 random walks containing no seasonality by construction:

===============  ==============  ============================================
History          Cleared |t|>=2  Strongest "pattern" found
===============  ==============  ============================================
4 years (n=3)    **15.4%**       ``+11.97%`` mean, ``t = +17.47``, up 100%
10 years (n=9)   **11.0%**       ``-5.61%`` mean, ``t = -6.41``, up 0%
===============  ==============  ============================================

Every one of those was a random number generator. A calendar month yields one
observation per year, so a few years of history leaves two or three degrees of
freedom, and the t-distribution's tail at ``df = 3`` is fat enough that one
month-symbol pair in six clears two sigma on noise alone. Scaled to a TSE
universe - 1,556 symbols times 12 months is 18,672 hypotheses - that is roughly
**2,900 "significant" months with nothing behind any of them**, and the
strongest will look more convincing than anything real.

**So the sample size is the binding constraint, not the method.** Four numbers
do not become a tendency by being averaged, and no correction rescues them.

So this module refuses to report a ranking on its own. Every scan carries:

- ``n`` (years observed) on every row, never hidden behind an average;
- an **empirical null** - the same scan re-run on the same returns with their
  month labels shuffled, which says how many hits this universe produces when
  no seasonality exists by construction;
- a verdict comparing the two, because "142 significant months" means nothing
  until you know that shuffled data produced 139.

The empirical null is used instead of a p-value on purpose. It assumes nothing
about the shape of monthly returns - which are skewed, fat-tailed and
autocorrelated across names - and it is the same device this repository already
uses to keep :mod:`~stock_ai.backtest.factor_test` honest.

What this module cannot do: correct for the fact that any month you go looking
at was chosen after seeing a chart, handle survivorship (delisted names are
absent), or make four observations into evidence.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass

import numpy as np
import pandas as pd

from stock_ai.core.logging import get_logger
from stock_ai.data.schema import ADJ_CLOSE, CLOSE

logger = get_logger(__name__)

#: |t| at or above this is called a hit. Two sigma, matching factor_test.
DEFAULT_T_THRESHOLD = 2.0

#: Years of observations a month needs before it is reported at all.
DEFAULT_MIN_YEARS = 4

#: Shuffled re-runs used to build the empirical null.
DEFAULT_PERMUTATIONS = 20


def month_name(month: int) -> str:
    """Return the English abbreviation for a month number."""
    return calendar.month_abbr[month]


def monthly_returns(prices: pd.DataFrame) -> pd.Series:
    """Return month-over-month returns from a daily OHLCV frame.

    Uses the adjusted close where present. Over the multi-year window a
    seasonality claim needs, splits and dividends are not a rounding error: an
    unadjusted series turns a 3-for-1 split into a -67% month.

    Returns:
        A Series of returns indexed by a ``PeriodIndex`` of months, with the
        first month dropped (it has no prior close to measure against).
    """
    if prices.empty:
        return pd.Series(dtype=float)

    column = ADJ_CLOSE if ADJ_CLOSE in prices.columns else CLOSE
    closes = pd.to_numeric(prices[column], errors="coerce").dropna()
    if closes.empty:
        return pd.Series(dtype=float)

    month_end = closes.groupby(pd.PeriodIndex(closes.index, freq="M")).last()
    return month_end.pct_change().dropna()


@dataclass(frozen=True)
class MonthlyPattern:
    """One symbol's record in one calendar month."""

    symbol: str
    month: int
    years: int
    mean_return: float
    t_stat: float
    hit_rate: float

    @property
    def label(self) -> str:
        """Return e.g. ``7203 Sep``."""
        return f"{self.symbol} {month_name(self.month)}"

    @property
    def clears_threshold(self) -> bool:
        """Whether ``|t|`` reaches :data:`DEFAULT_T_THRESHOLD`."""
        return abs(self.t_stat) >= DEFAULT_T_THRESHOLD


def _t_statistic(values: np.ndarray) -> float | None:
    """Return the one-sample t-statistic of ``values`` against zero."""
    if values.size < 2:
        return None
    deviation = float(values.std(ddof=1))
    if deviation == 0.0 or not np.isfinite(deviation):
        return None
    return float(values.mean()) / (deviation / float(np.sqrt(values.size)))


def symbol_patterns(
    symbol: str, returns: pd.Series, min_years: int = DEFAULT_MIN_YEARS
) -> list[MonthlyPattern]:
    """Return one :class:`MonthlyPattern` per calendar month with enough years."""
    if returns.empty:
        return []

    months = pd.Index([period.month for period in returns.index], name="month")
    patterns: list[MonthlyPattern] = []
    for month in range(1, 13):
        values = returns[months == month].to_numpy(dtype=float)
        if values.size < min_years:
            continue
        t_stat = _t_statistic(values)
        if t_stat is None:
            continue
        patterns.append(
            MonthlyPattern(
                symbol=symbol,
                month=month,
                years=int(values.size),
                mean_return=float(values.mean()),
                t_stat=t_stat,
                hit_rate=float((values > 0).mean()),
            )
        )
    return patterns


@dataclass(frozen=True)
class SeasonalityScan:
    """A universe-wide scan, with the null it has to be read against."""

    patterns: list[MonthlyPattern]
    tested: int
    symbols_scanned: int
    symbols_skipped: int
    t_threshold: float
    #: Mean hits across the shuffled re-runs. ``None`` when the null was skipped.
    expected_hits: float | None
    permutations: int

    @property
    def hits(self) -> list[MonthlyPattern]:
        """Patterns clearing the threshold, strongest first."""
        clearing = [p for p in self.patterns if abs(p.t_stat) >= self.t_threshold]
        return sorted(clearing, key=lambda p: abs(p.t_stat), reverse=True)

    @property
    def excess_hits(self) -> float | None:
        """Observed hits minus the hits shuffled data produced."""
        if self.expected_hits is None:
            return None
        return len(self.hits) - self.expected_hits

    @property
    def verdict(self) -> str:
        """Say what the scan supports, in one sentence, without overclaiming.

        Deliberately refuses to name a best month. The strongest row in a scan
        this wide is the maximum of a large sample, which is where noise is
        loudest - reporting it as a finding is the error the whole module
        exists to prevent.
        """
        observed = len(self.hits)
        if not self.patterns:
            return "No month had enough years of history to test."
        if self.expected_hits is None:
            return (
                f"{observed} of {self.tested} month-symbol pairs cleared "
                f"|t| >= {self.t_threshold:.0f}, with no null to compare against. "
                "That number alone supports nothing."
            )
        excess = self.excess_hits or 0.0
        base = (
            f"{observed} of {self.tested} month-symbol pairs cleared "
            f"|t| >= {self.t_threshold:.0f}; shuffling the month labels produced "
            f"{self.expected_hits:.0f} on the same data."
        )
        if observed <= self.expected_hits * 1.1:
            return f"{base} No seasonality beyond chance is visible."
        ratio = observed / self.expected_hits if self.expected_hits else float("inf")
        return (
            f"{base} That is {excess:.0f} more than chance ({ratio:.1f}x), which is "
            "worth a second look but is not evidence for any individual name - "
            "most of these rows are still noise."
        )


def _shuffled_hits(
    returns_by_symbol: dict[str, pd.Series],
    min_years: int,
    t_threshold: float,
    permutations: int,
    seed: int,
) -> float:
    """Return the mean hit count when month labels carry no information.

    Each symbol's monthly returns are permuted in place, so every name keeps its
    own volatility, drift and fat tails - only the link between a return and the
    calendar month it landed in is destroyed. Anything the real scan finds above
    this line is the part the calendar might explain.
    """
    rng = np.random.default_rng(seed)
    counts: list[int] = []
    for _ in range(permutations):
        hits = 0
        for symbol, returns in returns_by_symbol.items():
            shuffled = pd.Series(
                rng.permutation(returns.to_numpy(dtype=float)), index=returns.index
            )
            hits += sum(
                1
                for pattern in symbol_patterns(symbol, shuffled, min_years)
                if abs(pattern.t_stat) >= t_threshold
            )
        counts.append(hits)
    return float(np.mean(counts)) if counts else 0.0


def scan_seasonality(
    prices_by_symbol: dict[str, pd.DataFrame],
    month: int | None = None,
    min_years: int = DEFAULT_MIN_YEARS,
    t_threshold: float = DEFAULT_T_THRESHOLD,
    permutations: int = DEFAULT_PERMUTATIONS,
    seed: int = 0,
) -> SeasonalityScan:
    """Scan a universe for calendar-month patterns, against an empirical null.

    Args:
        prices_by_symbol: Daily OHLCV frames keyed by symbol.
        month: Restrict the report to one calendar month (1-12). The null is
            still built from all twelve, because restricting afterwards does
            not undo having looked at all of them.
        min_years: Years a month needs before it is reported.
        t_threshold: ``|t|`` at or above which a month counts as a hit.
        permutations: Shuffled re-runs behind the null. ``0`` skips it, which
            makes the result uninterpretable and is only for tests.
        seed: Seed for the shuffles, so a report is reproducible.

    Returns:
        A :class:`SeasonalityScan`. Read :attr:`SeasonalityScan.verdict` first.
    """
    returns_by_symbol: dict[str, pd.Series] = {}
    skipped = 0
    for symbol, prices in prices_by_symbol.items():
        returns = monthly_returns(prices)
        if returns.empty:
            skipped += 1
            continue
        returns_by_symbol[symbol] = returns

    patterns: list[MonthlyPattern] = []
    tested = 0
    for symbol, returns in returns_by_symbol.items():
        found = symbol_patterns(symbol, returns, min_years)
        tested += len(found)
        patterns.extend(p for p in found if month is None or p.month == month)

    expected: float | None = None
    if permutations > 0 and returns_by_symbol:
        expected = _shuffled_hits(returns_by_symbol, min_years, t_threshold, permutations, seed)
        if month is not None and tested:
            # The report was narrowed to one month; the null must be narrowed
            # by the same factor or it would be compared against twelve times
            # the number of tests it is standing in for.
            expected *= len(patterns) / tested

    logger.info(
        "Seasonality: %d symbols scanned, %d skipped for want of prices, %d month-symbol "
        "pairs tested with at least %d years each",
        len(returns_by_symbol),
        skipped,
        tested,
        min_years,
    )
    return SeasonalityScan(
        patterns=patterns,
        tested=tested if month is None else len(patterns),
        symbols_scanned=len(returns_by_symbol),
        symbols_skipped=skipped,
        t_threshold=t_threshold,
        expected_hits=expected,
        permutations=permutations,
    )


@dataclass(frozen=True)
class HoldoutResult:
    """What a pattern picked on early years did on the years held back."""

    pattern: MonthlyPattern
    holdout_years: int
    holdout_mean: float
    holdout_hit_rate: float

    @property
    def repeated(self) -> bool:
        """Whether the holdout return kept the sign the pattern was picked for."""
        return (self.holdout_mean > 0) == (self.pattern.mean_return > 0)


def holdout_check(
    symbol: str,
    prices: pd.DataFrame,
    month: int,
    split_year: int,
) -> HoldoutResult | None:
    """Pick a month's pattern before ``split_year``, then measure it after.

    This is the seasonality analogue of walk-forward validation, and it is the
    only part of this module that can support a claim rather than deflate one.
    A pattern that vanishes on the held-back years was a description of the
    past, not a property of the calendar.

    Returns:
        ``None`` when either side of the split is too thin to measure.
    """
    returns = monthly_returns(prices)
    if returns.empty:
        return None

    years = pd.Index([period.year for period in returns.index])
    months = pd.Index([period.month for period in returns.index])
    early = returns[(months == month) & (years < split_year)]
    late = returns[(months == month) & (years >= split_year)]
    if early.size < 2 or late.empty:
        return None

    t_stat = _t_statistic(early.to_numpy(dtype=float))
    if t_stat is None:
        return None

    values = early.to_numpy(dtype=float)
    pattern = MonthlyPattern(
        symbol=symbol,
        month=month,
        years=int(values.size),
        mean_return=float(values.mean()),
        t_stat=t_stat,
        hit_rate=float((values > 0).mean()),
    )
    late_values = late.to_numpy(dtype=float)
    return HoldoutResult(
        pattern=pattern,
        holdout_years=int(late_values.size),
        holdout_mean=float(late_values.mean()),
        holdout_hit_rate=float((late_values > 0).mean()),
    )
