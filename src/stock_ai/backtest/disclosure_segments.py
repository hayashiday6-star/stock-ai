"""Conditional cuts of the labeled disclosures: timing, magnitude, size.

:mod:`~stock_ai.backtest.disclosure_impact` answers "how much did this kind
of disclosure move the price". This module asks the follow-up questions that
decide whether a *score* would have anything to weight:

- **Timing.** Does an after-hours disclosure behave like an intraday one?
  They are measured over different windows, so a difference between them is
  as much about the measurement as about the market.
- **Magnitude.** Does a bigger revision move the price further, or does the
  market only read the sign? A score can weight magnitude only if the answer
  is monotone.
- **Size.** Does the same revision move a small company further than a large
  one? A score can carry a size term only if it does.

Nothing here scores or ranks. Every function returns counts beside its
percentages, and :func:`reconcile` exists so a partition that quietly loses
rows cannot be mistaken for one that does not.

**A note on reading bin tables.** Sorting a noisy variable into bins and
reporting the extremes is a machine for producing gradients that are not
there - the top bin is the maximum of a sample, which is where noise is
loudest. :func:`monotonicity` therefore reports how many adjacent steps
actually rise, not merely whether the ends differ, because a "gradient"
that is flat in the middle and steep at one end is usually the tail talking.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from stock_ai.core.logging import get_logger

logger = get_logger(__name__)

#: The two windows an after-hours reaction can be measured over.
CLOSE_TO_CLOSE = "excess_return"
OPEN_TO_CLOSE = "open_to_close_excess_return"

#: Quantile counts. Five for magnitude - enough to see a shape without
#: thinning the tails past reading - and three for size, where the question
#: is only "small, middle, large".
MAGNITUDE_BINS = 5
SIZE_BINS = 3


def _stats(frame: pd.DataFrame, column: str) -> dict[str, float | int]:
    """Count, symbols, median, quartiles, dispersion and median-over-IQR.

    The quartiles are not decoration. A median of +0.4% describes a
    different world depending on whether the middle half of the outcomes
    spans one point or ten, and only the second number says which. The ratio
    ``median / IQR`` puts the two together: it asks how large the typical
    outcome is *relative to how uncertain it is*, which is the form the
    question takes once a holding period is involved - a longer hold can
    raise the median and still be a worse bet, because the spread grows
    faster.
    """
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    if values.empty:
        return {
            "n": 0,
            "symbols": 0,
            "median": float("nan"),
            "p25": float("nan"),
            "p75": float("nan"),
            "iqr": float("nan"),
            "median_over_iqr": float("nan"),
            "std": float("nan"),
        }
    median = float(values.median())
    p25 = float(values.quantile(0.25))
    p75 = float(values.quantile(0.75))
    iqr = p75 - p25
    return {
        "n": int(values.size),
        "symbols": int(frame.loc[values.index, "symbol"].nunique()),
        "median": median,
        "p25": p25,
        "p75": p75,
        "iqr": iqr,
        "median_over_iqr": median / iqr if iqr > 0 else float("nan"),
        "std": float(values.std()),
    }


#: Columns every summary carries, in reading order.
STAT_COLUMNS = ["n", "symbols", "median", "p25", "p75", "iqr", "median_over_iqr", "std"]


def summarize_by_timing(labeled: pd.DataFrame, column: str = CLOSE_TO_CLOSE) -> pd.DataFrame:
    """Split the excess return by when the disclosure was made.

    Three rows, not two. A disclosure with no ``DiscTime`` is *measured* as
    after-hours - the safe assumption - but reporting it inside that bucket
    would pad a real category with rows whose timing was guessed, so it is
    counted on its own.

    Returns:
        Columns ``timing, n, symbols, median, std``, ordered
        after-hours, intraday, time-unknown so the two comparable rows sit
        together.
    """
    order = ["after_hours", "intraday", "time_unknown"]
    rows = []
    for timing in order:
        subset = labeled[labeled["timing"] == timing]
        if subset.empty:
            continue
        rows.append({"timing": timing, **_stats(subset, column)})
    return pd.DataFrame(rows, columns=["timing", *STAT_COLUMNS])


def summarize_by_timing_and_direction(
    labeled: pd.DataFrame, column: str = CLOSE_TO_CLOSE
) -> pd.DataFrame:
    """The revision-direction table, produced once per timing bucket.

    This is the comparison that matters: a difference in the *level* between
    after-hours and intraday says little, because the two are measured over
    different windows. A difference in the *spread* between an upward and a
    downward revision says the news is priced differently depending on when
    it lands.
    """
    rows = []
    for timing in ["after_hours", "intraday", "time_unknown"]:
        bucket = labeled[labeled["timing"] == timing]
        if bucket.empty:
            continue
        for direction in ["up", "flat", "down", "no_forecast"]:
            subset = bucket[bucket["revision_direction"] == direction]
            if subset.empty:
                continue
            rows.append(
                {"timing": timing, "revision_direction": direction, **_stats(subset, column)}
            )
    return pd.DataFrame(rows, columns=["timing", "revision_direction", *STAT_COLUMNS])


@dataclass(frozen=True)
class WindowComparison:
    """Both after-hours return windows, measured on the same disclosures."""

    close_to_close: pd.DataFrame
    open_to_close: pd.DataFrame
    rows_compared: int
    """Disclosures where *both* windows could be computed. Comparing spreads
    taken over different row sets would confound the windows with whichever
    rows each one happened to cover."""

    def spread(self, frame: pd.DataFrame) -> float:
        """Median of the ``up`` row minus the median of the ``down`` row."""
        by_direction = frame.set_index("revision_direction")["median"]
        if "up" not in by_direction or "down" not in by_direction:
            return float("nan")
        return float(by_direction["up"] - by_direction["down"])

    @property
    def close_to_close_spread(self) -> float:
        """Up-minus-down spread over the window that contains the gap."""
        return self.spread(self.close_to_close)

    @property
    def open_to_close_spread(self) -> float:
        """Up-minus-down spread over the window that excludes the gap."""
        return self.spread(self.open_to_close)

    @property
    def share_in_the_gap(self) -> float:
        """How much of the reaction the overnight gap already carried.

        One minus the ratio of the two spreads. A value near 1 means the
        market had finished pricing the news before the opening auction, and
        the intraday session added nothing.
        """
        full = self.close_to_close_spread
        if not np.isfinite(full) or full == 0:
            return float("nan")
        return 1.0 - self.open_to_close_spread / full


def compare_return_windows(labeled: pd.DataFrame) -> WindowComparison:
    """Measure both after-hours windows on the same set of disclosures.

    Restricted to after-hours rows carrying both figures, because that is
    the only comparison that isolates the window: an intraday disclosure has
    no meaningful open-to-close reading (its open precedes the news), and a
    row missing either figure would put a different denominator under each
    side.
    """
    after_hours = labeled[labeled["timing"] == "after_hours"]
    both = after_hours[after_hours[CLOSE_TO_CLOSE].notna() & after_hours[OPEN_TO_CLOSE].notna()]
    return WindowComparison(
        close_to_close=_by_direction(both, CLOSE_TO_CLOSE),
        open_to_close=_by_direction(both, OPEN_TO_CLOSE),
        rows_compared=len(both),
    )


def _by_direction(frame: pd.DataFrame, column: str) -> pd.DataFrame:
    """One row per revision direction, for ``column``."""
    rows = []
    for direction in ["up", "flat", "down", "no_forecast"]:
        subset = frame[frame["revision_direction"] == direction]
        if subset.empty:
            continue
        rows.append({"revision_direction": direction, **_stats(subset, column)})
    return pd.DataFrame(rows, columns=["revision_direction", *STAT_COLUMNS])


# ---------------------------------------------------------------------------
# Holding horizons measured from the next open
# ---------------------------------------------------------------------------

#: Column per holding horizon, mirroring
#: :data:`stock_ai.backtest.disclosure_impact.FORWARD_COLUMNS`.
HORIZON_COLUMNS: dict[int, str] = {
    1: "open_to_close_excess_return",
    5: "open_to_close_5d",
    20: "open_to_close_20d",
}


def horizons_by_direction(labeled: pd.DataFrame) -> pd.DataFrame:
    """Every holding horizon, by revision direction, on after-hours rows.

    Each horizon is measured over the rows that *have* it, not over their
    intersection. The counts therefore differ between horizons, and that is
    reported rather than hidden: forcing a common row set would silently
    throw away every recent disclosure from the short horizons too, which
    answers a narrower question than the one asked.
    """
    after_hours = labeled[labeled["timing"] == "after_hours"]
    rows = []
    for horizon, column in HORIZON_COLUMNS.items():
        if column not in after_hours.columns:
            continue
        for direction in ["up", "flat", "down", "no_forecast"]:
            subset = after_hours[after_hours["revision_direction"] == direction]
            if subset.empty:
                continue
            rows.append(
                {"horizon": horizon, "revision_direction": direction, **_stats(subset, column)}
            )
    return pd.DataFrame(rows, columns=["horizon", "revision_direction", *STAT_COLUMNS])


def horizon_spreads(by_direction: pd.DataFrame) -> pd.DataFrame:
    """Up-minus-down spread per horizon, beside the dispersion it sits in.

    The spread alone would say a longer hold is better whenever the median
    drifts up. ``median_over_iqr`` for the ``up`` leg is carried next to it
    because the spread has to be read against how far apart the outcomes
    are: a wider edge inside a much wider distribution is a worse bet, not a
    better one.
    """
    rows = []
    for horizon in sorted(by_direction["horizon"].unique()):
        block = by_direction[by_direction["horizon"] == horizon].set_index("revision_direction")
        if "up" not in block.index or "down" not in block.index:
            continue
        rows.append(
            {
                "horizon": int(horizon),
                "spread": float(block.loc["up", "median"] - block.loc["down", "median"]),
                "up_median": float(block.loc["up", "median"]),
                "up_iqr": float(block.loc["up", "iqr"]),
                "up_median_over_iqr": float(block.loc["up", "median_over_iqr"]),
                "n_up": int(block.loc["up", "n"]),
                "n_down": int(block.loc["down", "n"]),
            }
        )
    return pd.DataFrame(
        rows,
        columns=[
            "horizon",
            "spread",
            "up_median",
            "up_iqr",
            "up_median_over_iqr",
            "n_up",
            "n_down",
        ],
    )


def horizon_magnitude_bins(
    labeled: pd.DataFrame,
    magnitude_column: str = "revision_magnitude",
    bins: int = MAGNITUDE_BINS,
) -> dict[int, pd.DataFrame]:
    """Magnitude quantiles per holding horizon, on after-hours rows.

    Binned once per horizon rather than once overall, because each horizon
    covers a different set of rows: a shared cut would place a disclosure in
    a bin using a row it does not have.
    """
    after_hours = labeled[labeled["timing"] == "after_hours"]
    return {
        horizon: magnitude_bins(after_hours, magnitude_column, column, bins)
        for horizon, column in HORIZON_COLUMNS.items()
        if column in after_hours.columns
    }


@dataclass(frozen=True)
class HorizonCoverage:
    """How many disclosures each horizon can actually answer for."""

    rows: pd.DataFrame
    after_hours_total: int

    @property
    def line(self) -> str:
        """One sentence naming the recency cost of the longest horizon."""
        if self.rows.empty:
            return "No after-hours rows to cover."
        longest = self.rows.iloc[-1]
        return (
            f"{int(longest['n'])} of {self.after_hours_total} after-hours disclosures "
            f"reach {int(longest['horizon'])} trading days; "
            f"{int(longest['too_recent'])} are too recent and "
            f"{int(longest['missing_price'])} lack a price."
        )


def horizon_coverage(labeled: pd.DataFrame) -> HorizonCoverage:
    """Split each horizon's absences into "too recent" and "no price".

    Both arrive as a blank column, and conflating them would let a growing
    hole in the data pass as a market fact. Only the first shrinks as the
    window ages; the second never does.
    """
    after_hours = labeled[labeled["timing"] == "after_hours"]
    # Without the reach column every absence has to be reported as a missing
    # price. Guessing "too recent" from a frame that never recorded the
    # calendar would invent the more forgivable of the two explanations.
    if "observable_horizon" in after_hours.columns:
        reach = pd.to_numeric(after_hours["observable_horizon"], errors="coerce")
    else:
        reach = pd.Series(np.inf, index=after_hours.index)

    rows = []
    for horizon, column in HORIZON_COLUMNS.items():
        if column not in after_hours.columns:
            continue
        present = after_hours[column].notna()
        too_recent = (reach < horizon).fillna(False)
        rows.append(
            {
                "horizon": horizon,
                "n": int(present.sum()),
                "too_recent": int((~present & too_recent).sum()),
                "missing_price": int((~present & ~too_recent).sum()),
            }
        )
    return HorizonCoverage(
        pd.DataFrame(rows, columns=["horizon", "n", "too_recent", "missing_price"]),
        len(after_hours),
    )


def quantile_bins(values: pd.Series, bins: int) -> tuple[pd.Series, list[str]]:
    """Cut ``values`` into ``bins`` equal-count groups, labelled by their range.

    ``pd.qcut`` with ``duplicates="drop"`` rather than a fixed cut: revision
    magnitudes pile up at small values, and equal-*width* bins would put
    almost every observation in one of them. Fewer bins than asked for come
    back when the distribution is too degenerate to split - which is itself
    worth seeing rather than papering over.
    """
    numeric = pd.to_numeric(values, errors="coerce")
    usable = numeric.dropna()
    if usable.empty:
        return pd.Series(index=values.index, dtype="object"), []
    try:
        cut = pd.qcut(usable, bins, duplicates="drop")
    except ValueError:  # every value identical
        return pd.Series(index=values.index, dtype="object"), []

    # The outer bins are open-ended in practice. A forecast revised off a
    # near-zero base produces a ratio in the thousands of percent, and
    # printing that as a bin edge suggests the arithmetic broke rather than
    # that one filer's denominator was tiny. Quantile membership is
    # unaffected either way - only the label is.
    categories = list(cut.cat.categories)
    labels = []
    for index, interval in enumerate(categories):
        if index == 0:
            labels.append(f"≤ {interval.right:+.1%}")
        elif index == len(categories) - 1:
            labels.append(f"≥ {interval.left:+.1%}")
        else:
            labels.append(f"{interval.left:+.1%} … {interval.right:+.1%}")
    # A tie-heavy series can leave a value unassigned once duplicate edges
    # are dropped. Such a row belongs to no bin, which the caller's counts
    # then report as uncovered - the honest outcome, and one reconcile()
    # will surface rather than let pass as a silent shortfall.
    mapping = dict(zip(cut.cat.categories, labels, strict=True))
    placed = pd.Series(index=values.index, dtype="object")
    placed.loc[usable.index] = [mapping.get(interval) for interval in cut]
    return placed, labels


def magnitude_bins(
    labeled: pd.DataFrame,
    magnitude_column: str,
    return_column: str = CLOSE_TO_CLOSE,
    bins: int = MAGNITUDE_BINS,
    exclude_unrevised: bool = True,
) -> pd.DataFrame:
    """Median excess return per quantile of *signed* revision magnitude.

    Signed, not absolute: the question is whether the reaction grows with
    the revision, and that only has a shape if the deepest cuts and the
    largest raises sit at opposite ends. Binning on ``abs()`` would fold a
    -40% cut onto a +40% raise and flatten the very gradient being tested.

    Disclosures that revised nothing are excluded by default, and that is
    not a detail. A held forecast has a magnitude of exactly zero, and there
    are thousands of them - 6,495 of 15,674 measurable rows in the TSE run.
    Left in, that spike at zero swallows the quantile edges around it:
    ``qcut`` drops the duplicated boundaries and the five requested bins
    collapse to three, with every cut sharing the bottom bin with every
    hold. Measured both ways on the same data, the contaminated bins
    reported a 3.45-point span where the revisions alone show 5.57.

    They are excluded rather than given their own bin because they are not a
    smaller revision - they are the absence of one, and a dose-response
    curve has no place for a row that took no dose. The by-direction tables
    are where a held forecast is reported.

    Args:
        labeled: Labeled disclosures.
        magnitude_column: Signed relative revision size.
        return_column: Which return window to summarise.
        bins: Quantiles requested. Fewer come back if the distribution
            cannot be split that finely.
        exclude_unrevised: Drop rows whose magnitude is exactly zero.

    Returns:
        Columns ``bin, n, symbols, median, std``, ordered from the most
        negative revision to the most positive.
    """
    usable = labeled[labeled[magnitude_column].notna() & labeled[return_column].notna()]
    if exclude_unrevised:
        usable = usable[pd.to_numeric(usable[magnitude_column], errors="coerce") != 0]
    if usable.empty:
        return pd.DataFrame(columns=["bin", *STAT_COLUMNS])

    placed, labels = quantile_bins(usable[magnitude_column], bins)
    rows = []
    for label in labels:
        subset = usable[placed == label]
        if subset.empty:
            continue
        rows.append({"bin": label, **_stats(subset, return_column)})
    return pd.DataFrame(rows, columns=["bin", *STAT_COLUMNS])


@dataclass(frozen=True)
class Monotonicity:
    """Whether a binned series rises step by step, and by how much overall."""

    steps_up: int
    steps_total: int
    span: float
    """Last bin's median minus the first's."""

    @property
    def is_monotone(self) -> bool:
        """Whether every adjacent step rose."""
        return self.steps_total > 0 and self.steps_up == self.steps_total

    @property
    def verdict(self) -> str:
        """One sentence, refusing to call a two-ended difference a gradient."""
        if self.steps_total == 0:
            return "Too few bins to describe a shape."
        if self.is_monotone:
            return (
                f"Monotone: all {self.steps_total} steps rise, "
                f"spanning {self.span:+.2%} end to end."
            )
        return (
            f"Not monotone: {self.steps_up} of {self.steps_total} steps rise, "
            f"spanning {self.span:+.2%} end to end. A gradient that reverses in "
            "the middle is usually the outer bins talking, not a dose response."
        )


def monotonicity(binned: pd.DataFrame, column: str = "median") -> Monotonicity:
    """Count how many adjacent bins actually increase.

    The count, rather than a correlation, because the decision this informs
    is whether magnitude deserves its own weight - and that needs the
    response to rise *throughout*, not merely to end higher than it started.
    """
    medians = pd.to_numeric(binned[column], errors="coerce").dropna().tolist()
    if len(medians) < 2:
        return Monotonicity(0, 0, float("nan"))
    # Not strict: the pairing is deliberately offset by one, so the two
    # sequences differ in length by construction.
    steps = [later > earlier for earlier, later in zip(medians, medians[1:], strict=False)]
    return Monotonicity(sum(steps), len(steps), medians[-1] - medians[0])


def magnitude_by_size(
    labeled: pd.DataFrame,
    magnitude_column: str,
    size_column: str,
    return_column: str = CLOSE_TO_CLOSE,
    magnitude_bin_count: int = MAGNITUDE_BINS,
    size_bin_count: int = SIZE_BINS,
    exclude_unrevised: bool = True,
) -> pd.DataFrame:
    """Median excess return by revision magnitude *and* company size.

    Read across a row: if a small company moves further on the same size of
    revision, the small column is wider at both ends than the large one.
    Reading down a column instead only re-finds the magnitude gradient.

    Size is binned within the labeled set, so "small" means small relative
    to the universe measured, not to some absolute yen figure.

    Returns:
        A frame indexed by magnitude bin with one column per size tertile,
        holding the median excess return, plus a matching ``n`` column per
        tertile so a thin cell is visible.
    """
    usable = labeled[
        labeled[magnitude_column].notna()
        & labeled[return_column].notna()
        & labeled[size_column].notna()
    ]
    if exclude_unrevised:
        # Same spike at zero as in magnitude_bins, and the same damage: the
        # bottom bin would hold every cut alongside every held forecast,
        # in every size column at once.
        usable = usable[pd.to_numeric(usable[magnitude_column], errors="coerce") != 0]
    if usable.empty:
        return pd.DataFrame()

    magnitude, magnitude_labels = quantile_bins(usable[magnitude_column], magnitude_bin_count)
    size, size_labels = quantile_bins(usable[size_column], size_bin_count)
    if not magnitude_labels or not size_labels:
        return pd.DataFrame()

    # Size intervals print as percentages via quantile_bins, which is wrong
    # for a yen figure; rename them by rank instead.
    rank_names = _size_names(len(size_labels))
    size = size.map(dict(zip(size_labels, rank_names, strict=True)))

    rows = []
    for label in magnitude_labels:
        row: dict[str, object] = {"bin": label}
        for name in rank_names:
            cell = usable[(magnitude == label) & (size == name)]
            stats = _stats(cell, return_column)
            row[name] = stats["median"]
            row[f"{name} n"] = stats["n"]
        rows.append(row)
    columns = ["bin"] + [part for name in rank_names for part in (name, f"{name} n")]
    return pd.DataFrame(rows, columns=columns)


def _size_names(count: int) -> list[str]:
    """Rank labels for size tertiles, smallest first."""
    if count == 3:
        return ["small", "mid", "large"]
    return [f"q{index + 1}" for index in range(count)]


def amplification(by_size: pd.DataFrame, size_names: list[str] | None = None) -> pd.DataFrame:
    """Top-bin-minus-bottom-bin spread, per size bucket.

    This is the number the size question reduces to: within a size bucket,
    how far apart are the reactions to the largest cuts and the largest
    raises? A size effect means that spread widens as the companies get
    smaller.
    """
    if by_size.empty:
        return pd.DataFrame(columns=["size", "spread", "n"])
    names = size_names or [c for c in by_size.columns if c != "bin" and not c.endswith(" n")]
    rows = []
    for name in names:
        medians = pd.to_numeric(by_size[name], errors="coerce")
        counts = pd.to_numeric(by_size.get(f"{name} n"), errors="coerce")
        if medians.dropna().size < 2:
            continue
        rows.append(
            {
                "size": name,
                "spread": float(medians.iloc[-1] - medians.iloc[0]),
                "n": int(counts.sum()) if counts is not None else 0,
            }
        )
    return pd.DataFrame(rows, columns=["size", "spread", "n"])


@dataclass(frozen=True)
class Reconciliation:
    """Whether every labeled row is accounted for by a table, or named as absent."""

    name: str
    expected: int
    counted: int
    residual_reason: str | None = None
    """Why rows are legitimately outside this table. A magnitude bin cannot
    hold a disclosure that revised nothing, so demanding that bins alone sum
    to the whole would be demanding the wrong thing - but the difference
    still has to be named rather than left as a gap the reader must notice.
    """

    @property
    def residual(self) -> int:
        """Rows the table does not cover."""
        return self.expected - self.counted

    @property
    def balances(self) -> bool:
        """Whether every row is either counted or covered by a stated reason."""
        if self.residual == 0:
            return True
        return self.residual > 0 and self.residual_reason is not None

    @property
    def line(self) -> str:
        """One line, accounting for every row or saying how many escaped."""
        if self.residual == 0:
            return f"{self.name}: {self.counted} of {self.expected} ✓"
        if self.residual > 0 and self.residual_reason is not None:
            return (
                f"{self.name}: {self.counted} + {self.residual} {self.residual_reason} "
                f"= {self.expected} ✓"
            )
        return f"{self.name}: {self.counted} of {self.expected} ({-self.residual:+d} unexplained)"


def reconcile(
    name: str, expected: int, *frames: pd.DataFrame, residual_reason: str | None = None
) -> Reconciliation:
    """Sum the ``n`` column across ``frames`` and account for ``expected``.

    Every table here is a slice of the same labeled set. A slice stops adding
    up the moment a filter drops something silently - a NaN magnitude, a size
    that could not be formed - and a median computed over a set that quietly
    lost a third of its rows looks exactly like one that did not.

    Args:
        name: What is being checked, for the reported line.
        expected: Rows that carry an excess return at all.
        frames: Tables whose ``n`` columns should account for them.
        residual_reason: Names the rows a table legitimately cannot hold, for
            a conditional table like a magnitude bin. Without it any shortfall
            is reported as unexplained, which is the correct default: an
            unnamed gap is indistinguishable from a bug.
    """
    counted = 0
    for frame in frames:
        if frame.empty or "n" not in frame.columns:
            continue
        counted += int(pd.to_numeric(frame["n"], errors="coerce").fillna(0).sum())
    result = Reconciliation(name, expected, counted, residual_reason)
    if not result.balances:
        logger.warning("Partition does not account for every row - %s", result.line)
    return result
