"""Foundation for measuring the price impact of timely disclosures (適時開示).

This module answers one narrow question and stops there: for each disclosure
in J-Quants' ``/fins/summary`` history, what was the excess return (stock
return minus TOPIX return) around it, and does that differ by disclosure type?
It does **not** score or rank anything - that is a separate, later step.

Scope and the two design decisions this rests on:

- ``/fins/summary`` has no field pair for "the forecast before this revision"
  and "the forecast after it" - each row only carries the forecast *as of
  that disclosure* (``FSales``, ``FOP``, ``FOdP``, ``FNP``, ``FEPS``,
  ``FDivAnn``, all for the current fiscal year). A revision is therefore
  detected the only way the data supports: group a symbol's disclosures by
  the fiscal year the forecast targets (``CurFYEn``'s year - this is the
  target of ``F*``, not the reporting period ``CurPerType``, so a Q2 actuals
  release and a same-week revision-only notice land in the same group), sort
  by disclosure time, and diff each row against the previous one in the
  group.

  A fiscal year's opening forecast does **not** arrive in that year's own
  ``F*`` fields. The full-year announcement leaves ``F*`` blank - the year it
  reports is over - and issues its guidance for the coming year in ``NxF*``
  instead (verified on 7203, 2026-08). So ``NxF*`` is carried forward as the
  opening value of the year named by ``NxtFYEn``, and the next 1Q is measured
  against it. Skipping that step is not a small loss: on 7203 it hid a +8.3%
  raise and a -14.2% cut, one in each of the two years measured, and made
  every 1Q in the sample read as "no revision".

  Issuing guidance is not revising it, so a ``NxF*`` value only seeds - it is
  never itself reported as a revision. A full-year announcement therefore
  still reads as "none", correctly: it originates a forecast rather than
  changing one.

- After-hours disclosures (disclosed once the TSE session has closed) react
  on the next trading day; intraday disclosures (during the session) can
  already move the day's own close. So the reference date is not the same
  column for every row:

  - after-hours: reference day R = the next trading day after the disclosure
    date; the return is close(R) / close(day before R) - 1, and "the day
    before R" is the disclosure date itself once holidays are skipped.
  - intraday: reference day R = the disclosure date itself; the return is
    close(R) / close(trading day before R) - 1.

  Both cases reduce to the same shape - close(R) over close(the trading day
  immediately preceding R) - once R is chosen correctly, which is what makes
  a holiday or a long weekend between the two closes a non-issue: the
  "trading day immediately preceding" is read off the actual calendar of
  days the market traded, not a fixed offset.

A missing close on either side of the pair (a delisting, a trading halt, a
gap in the stored history) yields ``None`` rather than a guess, and the row
is excluded from the summary rather than counted as a zero.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from stock_ai.core.logging import get_logger
from stock_ai.data.schema import ADJ_CLOSE, CLOSE

logger = get_logger(__name__)

#: Regular TSE session close. A disclosure timestamped at or after this is
#: treated as after-hours; before it, as intraday.
SESSION_CLOSE = dt.time(15, 0)

#: Forecast metrics tracked for revisions. Each maps to a ``F{metric}``
#: J-Quants field carrying the current-fiscal-year forecast as of that
#: disclosure (e.g. ``NP`` -> ``FNP``).
FORECAST_METRICS: tuple[str, ...] = ("Sales", "OP", "OdP", "NP", "EPS", "DivAnn")

#: Interim (half-year) forecast metrics, tracked alongside the full-year set.
#:
#: A company may revise only its first-half outlook and leave the full year
#: standing, and reading the full year alone files that as "no revision".
#: Measured: of 154 disclosures typed ``EarnForecastRevision`` across 45
#: symbols, 44 read ``flat`` on the full year - and their median excess
#: return was **+1.70%**, against -0.50% to -0.98% for the genuinely
#: unchanged guidance in the quarterly rows. A bucket that is supposed to
#: mean "nothing changed" does not behave like a mild upward revision unless
#: it is holding real revisions.
#:
#: The names follow the same ``F{metric}`` rule as the full-year set, so
#: ``NP2Q`` reads ``FNP2Q``.
INTERIM_METRICS: tuple[str, ...] = ("Sales2Q", "OP2Q", "OdP2Q", "NP2Q", "EPS2Q")

#: Every metric carried on an event. ``DivAnn`` has no interim counterpart:
#: ``FDiv2Q`` is the second-quarter *dividend*, not a half-year earnings
#: forecast, and pairing them would compare a per-quarter payout against an
#: annual one.
TRACKED_METRICS: tuple[str, ...] = FORECAST_METRICS + INTERIM_METRICS

_FORECAST_FIELD = {metric: f"F{metric}" for metric in TRACKED_METRICS}

#: Order in which a metric decides the revision's direction. Profit leads
#: because a Japanese 業績予想修正 is read on 純利益 first, and the metrics
#: genuinely disagree - a revenue cut alongside a profit raise is a margin
#: story, and calling that "down" would file it against its own sign.
#:
#: The full year outranks the interim throughout: it is the headline figure,
#: so the interim only decides direction for a disclosure that left the full
#: year alone. ``DivAnn`` is absent on purpose - a dividend revision is a
#: separate event, not an earnings direction (see
#: :attr:`DisclosureEvent.revision_direction`).
DIRECTION_PRIORITY: tuple[str, ...] = (
    "NP",
    "OP",
    "Sales",
    "EPS",
    "NP2Q",
    "OP2Q",
    "Sales2Q",
    "EPS2Q",
)

#: Next-fiscal-year forecast fields, per metric. Verified against a live
#: payload (7203, 2026-08): the full-year announcement leaves every ``F*``
#: blank and carries its new guidance in ``NxF*`` instead, so without these
#: a fiscal year's first ``F*`` has nothing to compare against and every 1Q
#: reads as "no revision". ``NP`` is spelled ``NxFNp`` - lowercase ``p``,
#: unlike ``FNP`` - and the uppercase form is listed only as a fallback in
#: case the payload is ever normalized.
_NEXT_FORECAST_FIELDS: dict[str, tuple[str, ...]] = {
    "Sales": ("NxFSales",),
    "OP": ("NxFOP",),
    "OdP": ("NxFOdP",),
    "NP": ("NxFNp", "NxFNP"),
    "EPS": ("NxFEPS",),
    "DivAnn": ("NxFDivAnn",),
    # The interim set carries the same lowercase-p quirk on NP.
    "Sales2Q": ("NxFSales2Q",),
    "OP2Q": ("NxFOP2Q",),
    "OdP2Q": ("NxFOdP2Q",),
    "NP2Q": ("NxFNp2Q", "NxFNP2Q"),
    "EPS2Q": ("NxFEPS2Q",),
}


def _to_float(value: Any) -> float | None:
    """Parse a J-Quants numeric field (strings, blanks) to ``float`` or ``None``."""
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) else None


def _text(record: dict[str, Any], *keys: str) -> str | None:
    """Return the first non-empty string among ``keys``."""
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _parse_date(value: str | None) -> dt.date | None:
    """Parse an ISO-ish J-Quants date, tolerating slashes and stray time parts."""
    if not value:
        return None
    text = value.replace("/", "-")[:10]
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        return None


def _parse_time(value: str | None) -> dt.time | None:
    """Parse a J-Quants ``HH:MM`` / ``HH:MM:SS`` disclosure time."""
    if not value:
        return None
    text = value.strip()
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return dt.datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    return None


@dataclass(frozen=True)
class DisclosureEvent:
    """One row of ``/fins/summary`` history, normalized for impact analysis."""

    symbol: str
    disc_date: dt.date
    disc_time: dt.time | None
    doc_type: str | None
    target_fiscal_year: int | None
    """The fiscal year the row's forecast fields (``F*``) apply to, from
    ``CurFYEn``. This - not ``CurPerType`` - is the axis a forecast revision
    is tracked against, because ``F*`` always means "this fiscal year's
    forecast" regardless of which quarter's actuals the row also carries."""
    is_after_hours: bool
    forecasts: dict[str, float | None]
    """Current-as-of-this-disclosure value per :data:`TRACKED_METRICS`."""
    previous_forecasts: dict[str, float]
    """The running forecast for this fiscal year *before* this disclosure,
    per metric that had one. A metric absent here had no earlier value to be
    measured against - the fiscal year's first disclosure, or a filer that
    does not publish that forecast at all."""

    @property
    def revised_from(self) -> dict[str, float]:
        """Prior value per metric that this disclosure actually changed."""
        return {
            metric: previous
            for metric, previous in self.previous_forecasts.items()
            if (current := self.forecasts.get(metric)) is not None and current != previous
        }

    @property
    def is_revision(self) -> bool:
        """Whether at least one tracked forecast metric changed here."""
        return bool(self.revised_from)

    @property
    def revision_direction(self) -> str:
        """How this disclosure moved the full-year earnings forecast.

        One of:

        - ``"up"`` / ``"down"`` - the forecast was raised or cut.
        - ``"flat"`` - a forecast exists on both sides and did not move.
        - ``"no_forecast"`` - **there was nothing to compare.** Either this
          disclosure carries no earnings forecast (a full-year announcement,
          whose year is already over), or the filer publishes none at all.
          8306 is the second case: every ``F*`` earnings field is empty on
          every record, and only a dividend forecast is disclosed.

        The label is spelled ``no_forecast`` rather than the ``n/a`` it
        reads as, because ``n/a`` is one of pandas' default NA strings: an
        exported CSV read back with ``read_csv`` would turn it into ``NaN``,
        making "no forecast to compare" indistinguishable from a value that
        was never written. ``None`` and ``null`` are on that list too.

        The ``flat``/``no_forecast`` split exists because merging them repeats, one
        level over, the error that made every 1Q read as "no revision":
        "guidance held" and "guidance unmeasurable" are different findings,
        and averaging an excess return over both describes neither.

        Direction is read off the first metric that **moved**, in
        :data:`DIRECTION_PRIORITY` order - profit before revenue, and the
        full year before the interim, because a Japanese 業績予想修正 is
        judged on 純利益 first and the metrics can disagree (revenue cut
        while profit is raised on a margin improvement).
        :func:`direction_metric` reports which one decided it.

        The priority ranks the metrics that *moved*, not merely the ones
        present. A metric that held steady has no direction to contribute,
        so it must not shadow one that did move: ranking by presence alone
        let an unchanged full-year figure hide a raised half-year forecast
        beneath it, which is the whole case this fallback exists for. Only
        when nothing moved does presence decide, and then solely to separate
        ``"flat"`` from ``"no_forecast"``.

        Deliberately not a plain revised/not-revised boolean: lumping upward
        and downward revisions together cancels them against each other,
        which is the same averaging error that flattens the by-``DocType``
        view one level up.

        A dividend-only revision reads ``"no_forecast"`` or ``"flat"`` here
        depending on whether an earnings forecast was comparable, because a
        dividend revision is a different event from an earnings revision.
        :attr:`is_revision` still reports it as a revision.
        """
        metric = self.direction_metric()
        if metric is None:
            return "no_forecast"
        current = self.forecasts[metric]
        previous = self.previous_forecasts[metric]
        if current is None:  # unreachable: direction_metric requires a value
            return "no_forecast"
        if current == previous:
            return "flat"
        return "up" if current > previous else "down"

    def direction_metric(self) -> str | None:
        """Which metric decided the direction, in :data:`DIRECTION_PRIORITY` order.

        The first metric that actually moved wins. Failing that, the first
        metric that could be compared at all - that one carries no direction,
        but its presence is what makes the disclosure ``"flat"`` rather than
        ``"no_forecast"``.

        ``None`` means no earnings forecast could be compared on either
        count, which is what ``"no_forecast"`` reports.
        """
        comparable = [
            metric
            for metric in DIRECTION_PRIORITY
            if metric in self.previous_forecasts and self.forecasts.get(metric) is not None
        ]
        for metric in comparable:
            if self.forecasts[metric] != self.previous_forecasts[metric]:
                return metric
        return comparable[0] if comparable else None


def _target_fiscal_year(record: dict[str, Any]) -> int | None:
    """The fiscal year ``F*`` forecasts apply to, from ``CurFYEn``."""
    end = _parse_date(_text(record, "CurFYEn"))
    return end.year if end else None


def _next_fiscal_year(record: dict[str, Any]) -> int | None:
    """The fiscal year ``NxF*`` forecasts apply to.

    Prefers the payload's own ``NxtFYEn`` over adding a year to ``CurFYEn``:
    a company that moves its fiscal year end has a next year that is not
    twelve months later, and arithmetic would file its guidance against a
    year that does not exist.
    """
    explicit = _parse_date(_text(record, "NxtFYEn"))
    if explicit is not None:
        return explicit.year
    current = _target_fiscal_year(record)
    return current + 1 if current is not None else None


def _first_float(record: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    """First parseable numeric value among ``keys``."""
    for key in keys:
        value = _to_float(record.get(key))
        if value is not None:
            return value
    return None


def build_disclosure_events(symbol: str, records: list[dict[str, Any]]) -> list[DisclosureEvent]:
    """Turn raw ``/fins/summary`` records into disclosure events with revisions.

    Records with no resolvable ``DiscDate`` are dropped - there is nothing to
    date the disclosure by. Records with no resolvable target fiscal year
    (``CurFYEn``) keep their forecast values but are never compared against a
    neighbour, so they are never flagged as a revision.

    Returns:
        Events sorted by ``(disc_date, disc_time)``, oldest first.
    """
    dated: list[tuple[dt.date, dt.time | None, dict[str, Any]]] = []
    for record in records:
        disc_date = _parse_date(_text(record, "DiscDate", "DisclosedDate"))
        if disc_date is None:
            continue
        disc_time = _parse_time(_text(record, "DiscTime", "DisclosedTime"))
        dated.append((disc_date, disc_time, record))

    # Sort with a missing time last within its date - a record whose time is
    # unknown is, by the caller's own safety-first rule, treated as
    # after-hours (see is_after_hours below), so it belongs after whatever
    # timed disclosures happened that same day.
    dated.sort(key=lambda item: (item[0], item[1] or dt.time(23, 59, 59)))

    # Tracks the last-seen value per (fiscal_year, metric); reset is
    # unnecessary because a (fiscal_year, metric) key is only ever compared
    # to its own history.
    last_forecast: dict[tuple[int, str], float] = {}

    events: list[DisclosureEvent] = []
    for disc_date, disc_time, record in dated:
        doc_type = _text(record, "DocType")
        fiscal_year = _target_fiscal_year(record)
        is_after_hours = disc_time is None or disc_time >= SESSION_CLOSE

        forecasts = {
            metric: _to_float(record.get(field)) for metric, field in _FORECAST_FIELD.items()
        }

        # The running forecast as it stood *before* this disclosure. Recorded
        # for every metric that had one, not only the ones that changed:
        # telling "held steady" apart from "never published" needs to know
        # that a comparison was possible at all.
        previous_forecasts: dict[str, float] = {}
        if fiscal_year is not None:
            for metric, value in forecasts.items():
                if value is None:
                    continue
                key = (fiscal_year, metric)
                previous = last_forecast.get(key)
                # ``previous`` is only ever a float: None-valued metrics are
                # skipped above, so nothing None is ever stored to compare to.
                if previous is not None:
                    previous_forecasts[metric] = previous
                last_forecast[key] = value

        # Carry next-year guidance forward as the opening value for that year,
        # so the year's first F* has something to be measured against. This
        # only *seeds*: issuing guidance is not revising it, and the full-year
        # announcement that carries NxF* has no earlier forecast of that year
        # to differ from. Without this every 1Q reads as "no revision" - and
        # on 7203 that hid a +8.3% raise and a -14.2% cut, one in each of the
        # two years the window covers.
        next_year = _next_fiscal_year(record)
        if next_year is not None:
            for metric, keys in _NEXT_FORECAST_FIELDS.items():
                guidance = _first_float(record, keys)
                if guidance is not None:
                    last_forecast[(next_year, metric)] = guidance

        events.append(
            DisclosureEvent(
                symbol=symbol,
                disc_date=disc_date,
                disc_time=disc_time,
                doc_type=doc_type,
                target_fiscal_year=fiscal_year,
                is_after_hours=is_after_hours,
                forecasts=forecasts,
                previous_forecasts=previous_forecasts,
            )
        )
    return events


def disclosure_frame(events: list[DisclosureEvent]) -> pd.DataFrame:
    """Flatten :class:`DisclosureEvent` rows into a DataFrame for analysis.

    One row per disclosure, with a ``{metric}_before`` / ``{metric}_after``
    column pair per :data:`TRACKED_METRICS` - populated only where that
    disclosure actually revised the metric, ``NaN`` everywhere else.
    """
    rows: list[dict[str, Any]] = []
    for event in events:
        row: dict[str, Any] = {
            "symbol": event.symbol,
            "disc_date": event.disc_date,
            "disc_time": event.disc_time,
            "doc_type": event.doc_type,
            "target_fiscal_year": event.target_fiscal_year,
            "is_after_hours": event.is_after_hours,
            "is_revision": event.is_revision,
            "revision_direction": event.revision_direction,
            "direction_metric": event.direction_metric(),
        }
        for metric in TRACKED_METRICS:
            row[f"{metric}_after"] = (
                event.forecasts.get(metric) if metric in event.revised_from else np.nan
            )
            row[f"{metric}_before"] = event.revised_from.get(metric, np.nan)
        rows.append(row)
    columns = [
        "symbol",
        "disc_date",
        "disc_time",
        "doc_type",
        "target_fiscal_year",
        "is_after_hours",
        "is_revision",
        "revision_direction",
        "direction_metric",
        *(f"{m}_{suffix}" for m in TRACKED_METRICS for suffix in ("before", "after")),
    ]
    return pd.DataFrame(rows, columns=columns)


def _trading_day_on_or_after(day: dt.date, calendar: pd.DatetimeIndex) -> pd.Timestamp | None:
    """The earliest calendar date ``>= day``, or ``None`` past the end."""
    ts = pd.Timestamp(day)
    idx = calendar.searchsorted(ts, side="left")
    return calendar[idx] if idx < len(calendar) else None


def reference_dates(
    disc_date: dt.date, is_after_hours: bool, calendar: pd.DatetimeIndex
) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    """Return ``(base_date, prior_date)`` for one disclosure.

    ``calendar`` is the set of actual trading days (typically TOPIX's own
    index), so holidays and long weekends between the disclosure and its
    reaction day are skipped automatically rather than assumed to be five
    calendar days apart.

    Args:
        disc_date: The disclosure date.
        is_after_hours: Whether the disclosure was made after the session
            closed (see :data:`SESSION_CLOSE`).
        calendar: Sorted, unique trading days.

    Returns:
        ``base_date`` is the day whose close reflects the reaction;
        ``prior_date`` is the trading day immediately before it. Either may
        be ``None`` if ``calendar`` does not extend far enough.
    """
    if is_after_hours:
        base = _trading_day_on_or_after(disc_date + dt.timedelta(days=1), calendar)
    else:
        base = _trading_day_on_or_after(disc_date, calendar)
    if base is None:
        return None, None
    idx = calendar.searchsorted(base)
    prior = calendar[idx - 1] if idx > 0 else None
    return base, prior


@dataclass(frozen=True)
class ExcessReturnLabel:
    """The computed reaction to one disclosure, or the reason it could not be."""

    base_date: pd.Timestamp | None
    prior_date: pd.Timestamp | None
    stock_return: float | None
    topix_return: float | None
    excess_return: float | None
    exclude_reason: str | None


def _price_column(prices: pd.DataFrame) -> str:
    """Prefer the split-adjusted close where the frame has one."""
    return ADJ_CLOSE if ADJ_CLOSE in prices.columns else CLOSE


def label_excess_return(
    disc_date: dt.date,
    is_after_hours: bool,
    stock_prices: pd.DataFrame,
    topix_prices: pd.DataFrame,
) -> ExcessReturnLabel:
    """Compute one disclosure's excess return, or say why it could not be computed.

    The trading calendar is TOPIX's own index: TOPIX trades whenever the TSE
    is open regardless of any single stock's own halts, so it is the
    reference for *which* dates to look up. Whether the *stock* actually
    priced on those dates is then checked separately - a delisting or a
    trading halt shows up as a missing stock close on an otherwise-valid
    trading day, which is exactly the case this is meant to catch rather than
    silently produce a return for.
    """
    calendar = topix_prices.index
    base, prior = reference_dates(disc_date, is_after_hours, calendar)
    if base is None or prior is None:
        return ExcessReturnLabel(base, prior, None, None, None, "calendar_out_of_range")

    stock_col = _price_column(stock_prices)
    if base not in stock_prices.index or prior not in stock_prices.index:
        return ExcessReturnLabel(base, prior, None, None, None, "missing_stock_price")
    stock_before = stock_prices.loc[prior, stock_col]
    stock_after = stock_prices.loc[base, stock_col]
    if pd.isna(stock_before) or pd.isna(stock_after) or stock_before == 0:
        return ExcessReturnLabel(base, prior, None, None, None, "missing_stock_price")

    if base not in topix_prices.index or prior not in topix_prices.index:
        return ExcessReturnLabel(base, prior, None, None, None, "missing_topix_price")
    topix_before = topix_prices.loc[prior, CLOSE]
    topix_after = topix_prices.loc[base, CLOSE]
    if pd.isna(topix_before) or pd.isna(topix_after) or topix_before == 0:
        return ExcessReturnLabel(base, prior, None, None, None, "missing_topix_price")

    stock_return = float(stock_after / stock_before - 1.0)
    topix_return = float(topix_after / topix_before - 1.0)
    return ExcessReturnLabel(
        base, prior, stock_return, topix_return, stock_return - topix_return, None
    )


def label_disclosures(
    events: list[DisclosureEvent],
    stock_prices_by_symbol: dict[str, pd.DataFrame],
    topix_prices: pd.DataFrame,
) -> pd.DataFrame:
    """Attach an excess-return label to every disclosure event.

    Args:
        events: Disclosure events, e.g. from :func:`build_disclosure_events`
            across however many symbols.
        stock_prices_by_symbol: Daily OHLCV frames keyed by symbol.
        topix_prices: TOPIX daily bars, e.g. from
            :func:`stock_ai.data.jquants_index.fetch_topix`.

    Returns:
        :func:`disclosure_frame`'s columns, plus ``base_date``, ``prior_date``,
        ``stock_return``, ``topix_return``, ``excess_return`` and
        ``exclude_reason`` (``None`` where a label was computed).
    """
    frame = disclosure_frame(events)
    if frame.empty:
        for col in (
            "base_date",
            "prior_date",
            "stock_return",
            "topix_return",
            "excess_return",
            "exclude_reason",
        ):
            frame[col] = pd.Series(dtype="object")
        return frame

    labels: list[ExcessReturnLabel] = []
    for event in events:
        stock_prices = stock_prices_by_symbol.get(event.symbol)
        if stock_prices is None or stock_prices.empty:
            labels.append(ExcessReturnLabel(None, None, None, None, None, "no_stock_prices"))
            continue
        labels.append(
            label_excess_return(event.disc_date, event.is_after_hours, stock_prices, topix_prices)
        )

    frame["base_date"] = [label.base_date for label in labels]
    frame["prior_date"] = [label.prior_date for label in labels]
    frame["stock_return"] = [label.stock_return for label in labels]
    frame["topix_return"] = [label.topix_return for label in labels]
    frame["excess_return"] = [label.excess_return for label in labels]
    frame["exclude_reason"] = [label.exclude_reason for label in labels]

    excluded = frame["exclude_reason"].notna().sum()
    if excluded:
        logger.info(
            "%d of %d disclosure(s) excluded from excess-return labeling: %s",
            excluded,
            len(frame),
            frame.loc[frame["exclude_reason"].notna(), "exclude_reason"].value_counts().to_dict(),
        )
    return frame


def summarize_by_doc_type(labeled: pd.DataFrame) -> pd.DataFrame:
    """Count / median / std of excess return, grouped by disclosure type.

    Only rows with a computed ``excess_return`` count - excluded rows
    (missing prices, out-of-range calendar) are dropped rather than treated
    as a zero-impact observation, which would understate the spread.

    Returns:
        Columns ``doc_type, n, symbols, median_excess_return,
        std_excess_return``, sorted by ``n`` descending. Empty input yields an
        empty frame with these columns.
    """
    return _summarize_by(labeled, ["doc_type"])


def summarize_by_revision(labeled: pd.DataFrame) -> pd.DataFrame:
    """Count / median / std of excess return, by disclosure type *and* revision.

    The by-``DocType`` view alone cannot separate "a 2Q result that raised
    the full-year forecast" from "a 2Q result that left it alone", because
    Japanese large caps usually fold a revision into the quarterly
    announcement instead of disclosing it on its own. Those two events plausibly
    move the price in opposite directions, so averaging them inside one
    ``DocType`` cancels them against each other - which is the most likely
    reason every ``DocType`` row sits near zero.

    Splitting on the *direction* rather than a mere revised/not-revised flag
    matters for the same reason one level down: upward and downward revisions
    filed together would cancel just as thoroughly. See
    :attr:`DisclosureEvent.revision_direction` for how direction is decided.

    Returns:
        Columns ``doc_type, revision_direction, n, symbols,
        median_excess_return, std_excess_return``, sorted by ``n`` descending.
    """
    return _summarize_by(labeled, ["doc_type", "revision_direction"])


def _summarize_by(labeled: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Aggregate excess return over ``keys``, dropping unlabeled rows.

    ``symbols`` sits beside ``n`` because the two differ in a way that
    decides how much a row supports. Each company contributes one
    observation per disclosure type per year, so a three-year window turns
    12 companies into 36 observations - and those 36 are not 36 independent
    draws: a company that keeps raising guidance contributes the same story
    three times. The honest denominator for a standard error is nearer
    ``symbols`` than ``n``, so a row whose ``n`` looks comfortable while its
    ``symbols`` is single-digit is thinner than it appears.
    """
    columns = [*keys, "n", "symbols", "median_excess_return", "std_excess_return"]
    usable = labeled[labeled["excess_return"].notna()] if not labeled.empty else labeled
    if usable.empty:
        return pd.DataFrame(columns=columns)

    summary = usable.groupby(keys, dropna=False).agg(
        n=("excess_return", "count"),
        symbols=("symbol", "nunique"),
        median_excess_return=("excess_return", "median"),
        std_excess_return=("excess_return", "std"),
    )
    summary = summary.reset_index().sort_values("n", ascending=False, ignore_index=True)
    return summary[columns]
