"""Phase 1: the cheap pass over the whole market, and the relaxation ladder.

The ordering here is the whole design. Price, volume, range and distance from
the 52-week low all come out of one bulk OHLCV download and cost nothing per
symbol. Market capitalisation does not - it is one request per ticker - and
funding flow is rate-limited to 30 calls per 30 seconds. So the expensive
questions are asked only of what already survived the cheap ones, which is the
difference between a screen that finishes in minutes and one that does not
finish.

The relaxation ladder exists because the unconditional brief is "always produce
a result". Loosening a screen until something passes is a good way to produce
noise presented as a finding, so each step is applied cumulatively, in the
order given, and the level reached is reported alongside the table - a result
at level ④ is a different claim from a result at level ⓪ and has to read like
one.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace

import pandas as pd

from stock_ai.accumulation.types import Measure, Missing, insufficient, not_implemented
from stock_ai.accumulation.universe import Listing
from stock_ai.data.schema import CLOSE, HIGH, LOW, VOLUME

#: Bars needed before the 52-week and 20-day figures mean anything. A symbol
#: with less history is dropped rather than measured against a short window:
#: a three-week-old listing is trivially "within 15% of its 52-week low".
MIN_HISTORY_BARS = 60

#: Trading days taken as a year.
YEAR_BARS = 252


@dataclass(frozen=True)
class Thresholds:
    """The phase-1 filter, as numbers that can be loosened one at a time."""

    min_market_cap: float = 300_000_000.0
    min_avg_volume: float = 500_000.0
    min_price: float = 5.0
    volume_multiple: float = 5.0
    max_above_52w_low: float = 0.15
    max_range_20d: float = 0.10


#: Applied cumulatively, in order, until the screen returns something. Named in
#: the report so a thin result is never mistaken for a strict one.
RELAXATIONS: tuple[tuple[str, dict[str, float]], ...] = (
    ("① 出来高3倍以上", {"volume_multiple": 3.0}),
    ("② 時価総額1億ドル以上", {"min_market_cap": 100_000_000.0}),
    ("③ 52週安値+20%以内", {"max_above_52w_low": 0.20}),
    ("④ レンジ15%以内", {"max_range_20d": 0.15}),
)


@dataclass(frozen=True)
class Metrics:
    """What one symbol's price history says, before any threshold is applied."""

    price: float
    avg_volume_20d: float
    last_volume: float
    volume_multiple: float
    above_52w_low: float
    range_20d: float
    bars: int
    as_of: dt.date


@dataclass
class Candidate:
    """A symbol that passed phase 1, plus everything attached to it later."""

    listing: Listing
    metrics: Metrics
    market_cap: Measure
    sector: str | Missing
    flow_net_in_10d: Measure = field(default_factory=lambda: not_implemented("phase 2 fills this"))

    @property
    def symbol(self) -> str:
        """The listing symbol."""
        return self.listing.symbol


def compute_metrics(prices: pd.DataFrame) -> Metrics | Missing:
    """Derive the phase-1 figures from one canonical OHLCV frame.

    The volume multiple is measured against the twenty sessions *before* the
    latest one. Including the day being judged in its own average pulls the
    average up exactly when the day is unusual, which is the one case the
    number exists to detect - a true 5x day scores about 4.2x against a
    self-inclusive mean.
    """
    frame = prices.dropna(subset=[CLOSE, HIGH, LOW])
    if len(frame) < MIN_HISTORY_BARS:
        return insufficient(f"{len(frame)} bars, need {MIN_HISTORY_BARS}")

    close = frame[CLOSE]
    price = float(close.iloc[-1])
    if price <= 0:
        return insufficient("last close is not positive")

    prior = frame[VOLUME].iloc[-21:-1]
    avg_volume = float(prior.mean())
    last_volume = float(frame[VOLUME].iloc[-1])
    if avg_volume <= 0:
        return insufficient("no traded volume in the prior 20 sessions")

    window = frame.iloc[-20:]
    low_20 = float(window[LOW].min())
    high_20 = float(window[HIGH].max())
    if low_20 <= 0:
        return insufficient("20-day low is not positive")

    year = frame.iloc[-YEAR_BARS:]
    low_52w = float(year[LOW].min())
    if low_52w <= 0:
        return insufficient("52-week low is not positive")

    return Metrics(
        price=price,
        avg_volume_20d=avg_volume,
        last_volume=last_volume,
        volume_multiple=last_volume / avg_volume,
        above_52w_low=(price - low_52w) / low_52w,
        range_20d=(high_20 - low_20) / low_20,
        bars=len(frame),
        as_of=frame.index[-1].date(),
    )


def passes_price_filters(metrics: Metrics, thresholds: Thresholds) -> bool:
    """Every phase-1 test that needs only the OHLCV frame."""
    return (
        metrics.price >= thresholds.min_price
        and metrics.avg_volume_20d >= thresholds.min_avg_volume
        and metrics.volume_multiple >= thresholds.volume_multiple
        and metrics.above_52w_low <= thresholds.max_above_52w_low
        and metrics.range_20d <= thresholds.max_range_20d
    )


@dataclass
class ScreenResult:
    """The phase-1 outcome, including how hard the screen had to be loosened."""

    candidates: list[Candidate]
    relaxation_level: int
    relaxations_applied: list[str]
    thresholds: Thresholds
    as_of: dt.date | None
    universe_size: int
    priced: int
    measurable: int

    @property
    def relaxation_label(self) -> str:
        """How the screen was run, in one phrase."""
        if not self.relaxations_applied:
            return "緩和なし（原条件）"
        return " → ".join(self.relaxations_applied)


def _size_test(cap: Measure | None, floor: float) -> bool:
    """Whether a symbol clears the size filter, or the filter could not run.

    A capitalisation that could not be read keeps the symbol rather than
    dropping it. Dropping would apply a test that was never evaluated, and the
    result would look identical to a symbol that was measured and failed. The
    row is marked instead, so the table says which names the size test did not
    actually run on.
    """
    if cap is None or isinstance(cap, Missing):
        return True
    return float(cap) >= floor


def _thresholds_for(level: int, base: Thresholds) -> Thresholds:
    """The thresholds after applying the first ``level`` relaxations."""
    changes: dict[str, float] = {}
    for _label, change in RELAXATIONS[:level]:
        changes.update(change)
    return replace(base, **changes) if changes else base


def run_screen(
    listings: Sequence[Listing],
    metrics_by_symbol: dict[str, Metrics],
    market_cap_of: Callable[[Iterable[str]], dict[str, Measure]],
    sector_of: Callable[[Iterable[str]], dict[str, str | Missing]],
    *,
    base: Thresholds | None = None,
    limit: int = 10,
) -> ScreenResult:
    """Run phase 1, loosening the screen only as far as it takes to find names.

    Args:
        listings: The universe to consider.
        metrics_by_symbol: Phase-1 figures, already computed from bulk prices.
        market_cap_of: Fetches market capitalisation for the symbols given. It
            is a callback, and it is called last, because it costs one request
            per symbol - the ladder asks it only about names that already
            passed everything free.
        sector_of: Same, for the sector label.
        base: Starting thresholds; the brief's numbers by default.
        limit: How many rows the table should hold.

    Returns:
        A :class:`ScreenResult`, empty only if nothing passed even level ④.
    """
    base = base or Thresholds()
    by_symbol = {listing.symbol: listing for listing in listings}
    as_of = max((m.as_of for m in metrics_by_symbol.values()), default=None)

    for level in range(len(RELAXATIONS) + 1):
        thresholds = _thresholds_for(level, base)
        survivors = [
            symbol
            for symbol, metrics in metrics_by_symbol.items()
            if symbol in by_symbol and passes_price_filters(metrics, thresholds)
        ]
        if not survivors:
            continue

        caps = market_cap_of(survivors)
        kept = [s for s in survivors if _size_test(caps.get(s), thresholds.min_market_cap)]
        if not kept:
            continue

        candidates = [
            Candidate(
                listing=by_symbol[symbol],
                metrics=metrics_by_symbol[symbol],
                market_cap=caps.get(symbol, not_implemented("not fetched")),
                sector=not_implemented("sector is fetched for the shown rows only"),
            )
            for symbol in kept
        ]
        # Tightest base first: a narrow 20-day range next to a 52-week low is
        # the shape being looked for, and the volume spike is what dates it.
        candidates.sort(
            key=lambda c: (c.metrics.range_20d, c.metrics.above_52w_low, -c.metrics.volume_multiple)
        )
        shown = candidates[:limit]
        # Sector needs the per-symbol profile call, which is the expensive one.
        # Asking only about the rows that will be printed keeps a screen over
        # hundreds of survivors from turning into hundreds of requests.
        sectors = sector_of([c.symbol for c in shown])
        for candidate in shown:
            candidate.sector = sectors.get(candidate.symbol, not_implemented("not fetched"))
        return ScreenResult(
            candidates=shown,
            relaxation_level=level,
            relaxations_applied=[label for label, _ in RELAXATIONS[:level]],
            thresholds=thresholds,
            as_of=as_of,
            universe_size=len(listings),
            priced=len(metrics_by_symbol),
            measurable=len(metrics_by_symbol),
        )

    return ScreenResult(
        candidates=[],
        relaxation_level=len(RELAXATIONS),
        relaxations_applied=[label for label, _ in RELAXATIONS],
        thresholds=_thresholds_for(len(RELAXATIONS), base),
        as_of=as_of,
        universe_size=len(listings),
        priced=len(metrics_by_symbol),
        measurable=len(metrics_by_symbol),
    )
