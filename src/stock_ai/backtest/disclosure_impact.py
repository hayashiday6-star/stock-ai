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
  group. The first disclosure of a fiscal year has no "before" and is never a
  revision by construction.

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

_FORECAST_FIELD = {metric: f"F{metric}" for metric in FORECAST_METRICS}


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
    """Current-as-of-this-disclosure value per :data:`FORECAST_METRICS`."""
    revised_from: dict[str, float | None]
    """Prior disclosure's value per metric, for metrics that changed. A
    metric absent here either did not change or there was no prior
    disclosure of the same target fiscal year to compare against."""

    @property
    def is_revision(self) -> bool:
        """Whether at least one tracked forecast metric changed here."""
        return bool(self.revised_from)


def _target_fiscal_year(record: dict[str, Any]) -> int | None:
    """The fiscal year ``F*`` forecasts apply to, from ``CurFYEn``."""
    end = _parse_date(_text(record, "CurFYEn"))
    return end.year if end else None


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
    last_forecast: dict[tuple[int, str], float | None] = {}

    events: list[DisclosureEvent] = []
    for disc_date, disc_time, record in dated:
        doc_type = _text(record, "DocType")
        fiscal_year = _target_fiscal_year(record)
        is_after_hours = disc_time is None or disc_time >= SESSION_CLOSE

        forecasts = {
            metric: _to_float(record.get(field)) for metric, field in _FORECAST_FIELD.items()
        }

        revised_from: dict[str, float | None] = {}
        if fiscal_year is not None:
            for metric, value in forecasts.items():
                if value is None:
                    continue
                key = (fiscal_year, metric)
                previous = last_forecast.get(key)
                if key in last_forecast and previous != value:
                    revised_from[metric] = previous
                last_forecast[key] = value

        events.append(
            DisclosureEvent(
                symbol=symbol,
                disc_date=disc_date,
                disc_time=disc_time,
                doc_type=doc_type,
                target_fiscal_year=fiscal_year,
                is_after_hours=is_after_hours,
                forecasts=forecasts,
                revised_from=revised_from,
            )
        )
    return events


def disclosure_frame(events: list[DisclosureEvent]) -> pd.DataFrame:
    """Flatten :class:`DisclosureEvent` rows into a DataFrame for analysis.

    One row per disclosure, with a ``{metric}_before`` / ``{metric}_after``
    column pair per :data:`FORECAST_METRICS` - populated only where that
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
        }
        for metric in FORECAST_METRICS:
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
        *(f"{m}_{suffix}" for m in FORECAST_METRICS for suffix in ("before", "after")),
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
        Columns ``doc_type, n, median_excess_return, std_excess_return``,
        sorted by ``n`` descending. Empty input yields an empty frame with
        these columns.
    """
    columns = ["doc_type", "n", "median_excess_return", "std_excess_return"]
    usable = labeled[labeled["excess_return"].notna()] if not labeled.empty else labeled
    if usable.empty:
        return pd.DataFrame(columns=columns)

    grouped = usable.groupby("doc_type", dropna=False)["excess_return"]
    summary = grouped.agg(n="count", median_excess_return="median", std_excess_return="std")
    summary = summary.reset_index().sort_values("n", ascending=False, ignore_index=True)
    return summary[columns]
