"""J-Quants fundamentals provider (Japanese equities).

Reads the V2 ``/fins/summary`` endpoint (available on the free plan; the richer
``/fins/details`` requires a paid plan) and maps the latest disclosure to the
canonical :class:`~stock_ai.data.types.Fundamentals`:

- ``Sales`` → revenue, ``NP`` → net income, ``NP / Eq`` → ROE
- ``EPS``, ``BPS``, ``DivAnn`` combine with an optional current price to give
  PER, PBR, and dividend yield; without a price those stay ``None``.

The HTTP call is injectable, so the provider is unit-testable without network.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Callable
from typing import Any

from pydantic import SecretStr

from stock_ai.core.exceptions import DataError
from stock_ai.core.logging import get_logger
from stock_ai.data.http import raise_for_status
from stock_ai.data.types import FinancialReport, FiscalPeriod, Fundamentals

logger = get_logger(__name__)

# A fetcher takes a symbol and returns raw statement records.
StatementFetcher = Callable[[str], list[dict[str, Any]]]
Clock = Callable[[], dt.date]
PriceLookup = Callable[[str], float | None]

_STATEMENTS_URL = "https://api.jquants.com/v2/fins/summary"


def _to_float(value: Any) -> float | None:
    """Parse a J-Quants numeric field (strings, blanks) to ``float`` or ``None``.

    Non-finite results map to ``None`` as well: ``float("nan")`` parses happily,
    and a ``NaN`` that escapes here poisons every ratio derived from it
    downstream (scoring treats non-finite input as missing, not as a score).
    """
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _latest(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the most recently disclosed record (V2 ``DiscDate``, V1 fallback)."""
    return max(
        records,
        key=lambda r: str(r.get("DiscDate") or r.get("DisclosedDate") or ""),
    )


def _first(record: dict[str, Any], *keys: str) -> float | None:
    """Return the first parseable numeric value among ``keys``."""
    for key in keys:
        value = _to_float(record.get(key))
        if value is not None:
            return value
    return None


def _latest_annual(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the newest record covering a full fiscal year, if there is one.

    When **no** record carries a period marker the payload cannot be split, so
    the newest is treated as annual - the documented fallback, and what the V1
    shape needs. Refusing to compute anything there would trade a wrong PER for
    no PER at all, on payloads that were never quarterly to begin with.

    Once *any* record is marked, the markers are trusted and only ``FY`` rows
    qualify: a mixed payload is exactly the case where a quarter would otherwise
    masquerade as a year.
    """
    if not any(_has_period_marker(record) for record in records):
        return _latest(records) if records else None
    annual = [record for record in records if _period_of(record) is FiscalPeriod.FY]
    return _latest(annual) if annual else None


def normalize_statement(
    symbol: str,
    records: list[dict[str, Any]],
    as_of: dt.date,
    price: float | None = None,
) -> Fundamentals:
    """Build a :class:`Fundamentals` snapshot from ``fins/summary`` records.

    Flow figures (sales, profit, EPS) are taken from the most recent **annual**
    disclosure; balance-sheet figures (equity, BPS, shares) from the most recent
    disclosure of any period.

    That split is the whole point. ``Sales``, ``NP`` and ``EPS`` are cumulative
    from the start of the fiscal year, so the newest disclosure is usually a
    quarter holding three or six months. Dividing a price by a half-year EPS
    doubles the PER, and every screen with a PER ceiling then rejects companies
    that are in fact cheap. Equity and BPS are point-in-time, so for those the
    freshest disclosure is simply the best one.

    Args:
        symbol: The security code.
        records: J-Quants summary records.
        as_of: Snapshot date.
        price: Current share price; enables PER, PBR, dividend yield, market cap.

    Returns:
        A fundamentals snapshot. Ratios needing a price stay ``None`` without one.

    Raises:
        DataError: If ``records`` is empty.
    """
    if not records:
        raise DataError(f"No J-Quants statements for {symbol!r}.")

    latest = _latest(records)
    # No annual disclosure yet (a recent listing) means no trustworthy earnings
    # figure. Reporting a quarter as if it were a year would be worse than
    # reporting nothing, so the earnings-based ratios stay None.
    annual = _latest_annual(records)

    revenue = _first(annual, "Sales", "NetSales") if annual else None
    net_income = _first(annual, "NP", "Profit") if annual else None
    eps = _first(annual, "EPS") if annual else None

    equity = _first(latest, "Eq", "Equity")
    bps = _first(latest, "BPS")
    dividend = _first(latest, "DivAnn")
    shares = _first(latest, "ShOutFY")

    roe = net_income / equity if (net_income is not None and equity) else None
    per = price / eps if (price is not None and eps) else None
    pbr = price / bps if (price is not None and bps) else None
    dividend_yield = dividend / price if (price and dividend is not None) else None
    market_cap = price * shares if (price is not None and shares) else None

    if annual is None:
        logger.info(
            "%s has no annual disclosure in the fetched window; PER, ROE, "
            "revenue and net income are left unset rather than filled from a "
            "part-year figure.",
            symbol,
        )

    return Fundamentals(
        symbol=symbol,
        as_of=as_of,
        roe=roe,
        per=per,
        pbr=pbr,
        dividend_yield=dividend_yield,
        market_cap=market_cap,
        revenue=revenue,
        net_income=net_income,
    )


# V2 abbreviates period markers; V1-style spellings are kept as fallbacks.
_PERIOD_ALIASES: dict[str, FiscalPeriod] = {
    "1Q": FiscalPeriod.Q1,
    "Q1": FiscalPeriod.Q1,
    "2Q": FiscalPeriod.Q2,
    "Q2": FiscalPeriod.Q2,
    "HY": FiscalPeriod.Q2,  # a half-year report closes the second quarter
    "3Q": FiscalPeriod.Q3,
    "Q3": FiscalPeriod.Q3,
    "FY": FiscalPeriod.FY,
    "4Q": FiscalPeriod.FY,
}


# Chronological order within a fiscal year; alphabetical would put FY first.
_PERIOD_ORDER: dict[FiscalPeriod, int] = {
    FiscalPeriod.Q1: 1,
    FiscalPeriod.Q2: 2,
    FiscalPeriod.Q3: 3,
    FiscalPeriod.FY: 4,
}


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


#: Field names that may carry the period marker, newest spelling first.
_PERIOD_FIELDS = ("CurPerType", "Period", "TypeOfCurrentPeriod", "PeriodType")


def _period_of(record: dict[str, Any]) -> FiscalPeriod:
    """Map a record's period marker onto :class:`FiscalPeriod`.

    V2 calls this ``CurPerType``. Reading only the older spellings made every
    record look annual, and that is not a cosmetic mistake: ``Sales`` and ``NP``
    are **cumulative from the start of the fiscal year**, so a 3Q row holds nine
    months. Filed under FY alongside a real twelve-month row, the next
    year-over-year comparison invents about a third of a year's growth out of
    nothing - which is exactly what a screen showing three quarters of the
    market growing 10%+ looks like.

    Anything unrecognised is still treated as a full year, because some payloads
    genuinely omit the marker on annual rows. :func:`normalize_statements`
    counts those and warns, so a future rename is loud rather than silent.
    """
    marker = _text(record, *_PERIOD_FIELDS)
    if marker is None:
        return FiscalPeriod.FY
    return _PERIOD_ALIASES.get(marker.strip().upper(), FiscalPeriod.FY)


def _has_period_marker(record: dict[str, Any]) -> bool:
    """Whether the record said which period it covers at all."""
    return _text(record, *_PERIOD_FIELDS) is not None


def _fiscal_year_of(record: dict[str, Any]) -> int | None:
    """Determine the fiscal year a record belongs to.

    Prefers an explicit fiscal-year field, then the fiscal-year-end date, and
    finally the disclosure date - a disclosure names the year it reports on far
    more reliably than the year it was published, so it is the last resort.
    """
    explicit = _to_float(_text(record, "FY", "FiscalYear"))
    if explicit is not None and 1900 <= explicit <= 2999:
        return int(explicit)

    for key in ("FYEnd", "CurrentFiscalYearEndDate", "FiscalYearEnd", "PeriodEnd"):
        parsed = _parse_date(_text(record, key))
        if parsed is not None:
            return parsed.year

    disclosed = _parse_date(_text(record, "DiscDate", "DisclosedDate"))
    return disclosed.year if disclosed else None


def normalize_statements(symbol: str, records: list[dict[str, Any]]) -> list[FinancialReport]:
    """Turn raw ``fins/summary`` records into a fiscal-period report series.

    Every record is kept, not just the newest: the payload already carries the
    company's disclosure history, and that history is exactly what growth rates
    and dividend streaks are computed from.

    Records whose fiscal year cannot be determined are dropped - placing them
    on the wrong year would corrupt a year-over-year comparison, which is worse
    than omitting them.

    Args:
        symbol: The security code.
        records: J-Quants summary records.

    Returns:
        Reports sorted oldest first. Restatements of the same period collapse
        to the latest disclosure.
    """
    by_period: dict[tuple[int, FiscalPeriod], tuple[str, FinancialReport]] = {}
    skipped = 0
    unmarked = 0

    for record in records:
        fiscal_year = _fiscal_year_of(record)
        if fiscal_year is None:
            skipped += 1
            continue

        period = _period_of(record)
        if not _has_period_marker(record):
            unmarked += 1
        disclosed_text = _text(record, "DiscDate", "DisclosedDate") or ""
        report = FinancialReport(
            symbol=symbol,
            fiscal_year=fiscal_year,
            period=period,
            disclosed_on=_parse_date(disclosed_text),
            revenue=_first(record, "Sales", "NetSales"),
            operating_income=_first(record, "OP", "OperatingProfit"),
            net_income=_first(record, "NP", "Profit"),
            equity=_first(record, "Eq", "Equity"),
            eps=_first(record, "EPS"),
            bps=_first(record, "BPS"),
            dividend_per_share=_first(record, "DivAnn"),
            shares_outstanding=_first(record, "ShOutFY"),
        )

        key = (fiscal_year, period)
        previous = by_period.get(key)
        # A restatement supersedes the earlier disclosure of the same period.
        if previous is None or disclosed_text >= previous[0]:
            by_period[key] = (disclosed_text, report)

    if skipped:
        logger.warning("Dropped %d %s statement(s) with no resolvable fiscal year", skipped, symbol)
    if unmarked and records:
        # Every record defaulting to annual is the signature of a renamed field,
        # and it corrupts quietly: quarterly figures are cumulative, so filing
        # nine months as a full year manufactures growth on the next comparison.
        level = logger.error if unmarked == len(records) else logger.warning
        level(
            "%d of %d %s statement(s) carried no period marker in any of %s "
            "and were filed as annual. Quarterly rows are cumulative, so this "
            "invents year-over-year growth. Check the payload's field names.",
            unmarked,
            len(records),
            symbol,
            ", ".join(_PERIOD_FIELDS),
        )

    ordered = sorted(by_period.items(), key=lambda kv: (kv[0][0], _PERIOD_ORDER[kv[0][1]]))
    return [report for _key, (_disclosed, report) in ordered]


def _default_fetcher(api_key: SecretStr | None) -> StatementFetcher:
    """Build a fetcher that calls the J-Quants V2 statements endpoint."""

    def fetch(symbol: str) -> list[dict[str, Any]]:
        import httpx

        headers = {"x-api-key": api_key.get_secret_value()} if api_key else {}
        records: list[dict[str, Any]] = []
        pagination_key: str | None = None
        with httpx.Client(timeout=30.0) as client:
            while True:
                params = {"code": symbol}
                if pagination_key:
                    params["pagination_key"] = pagination_key
                response = client.get(_STATEMENTS_URL, headers=headers, params=params)
                raise_for_status(response, f"statements for {symbol}")
                payload = response.json()
                # V2 returns {"data": [...]}; older shapes used "statements".
                records.extend(payload.get("data") or payload.get("statements") or [])
                pagination_key = payload.get("pagination_key")
                if not pagination_key:
                    break
        return records

    return fetch


class JQuantsFundamentalsProvider:
    """Fetch Japanese fundamentals via the J-Quants statements API."""

    name = "jquants"

    def __init__(
        self,
        api_key: SecretStr | None = None,
        fetcher: StatementFetcher | None = None,
        clock: Clock | None = None,
        price_source: PriceLookup | None = None,
    ) -> None:
        """Create the provider.

        Args:
            api_key: J-Quants V2 API key.
            fetcher: Callable performing the raw fetch; injected in tests.
            clock: Callable returning the snapshot date; defaults to today.
            price_source: Optional callable returning a symbol's current price,
                which enables PER, PBR, dividend yield, and market cap.
        """
        self._fetch = fetcher or _default_fetcher(api_key)
        self._today = clock or dt.date.today
        self._price_source = price_source

    def fetch_fundamentals(self, symbol: str) -> Fundamentals:
        """Fetch and normalize the latest fundamentals for ``symbol``."""
        records = self._fetch(symbol)
        price: float | None = None
        if self._price_source is not None:
            try:
                price = self._price_source(symbol)
            except Exception as exc:  # price is optional - never fail the fetch
                logger.warning("Price lookup failed for %s: %s", symbol, exc)
        snapshot = normalize_statement(symbol, records, self._today(), price)
        logger.info("Fetched J-Quants fundamentals for %s", symbol)
        return snapshot

    def fetch_snapshot_and_statements(
        self, symbol: str
    ) -> tuple[Fundamentals, list[FinancialReport]]:
        """Return both products of a single ``fins/summary`` request.

        The snapshot and the series come from the same records, so fetching them
        separately spends two requests on one answer. At TSE Prime scale that is
        1,600 avoidable calls, which is the difference between a bulk load
        finishing and a rate limit stopping it.

        This exists because storing only the series left every valuation screen
        silently empty on JP names: ``screen --max-per`` reads the snapshot
        table, and nothing was writing to it outside the US path.
        """
        records = self._fetch(symbol)
        price: float | None = None
        if self._price_source is not None:
            try:
                price = self._price_source(symbol)
            except Exception as exc:  # price is optional - never fail the fetch
                logger.warning("Price lookup failed for %s: %s", symbol, exc)
        snapshot = normalize_statement(symbol, records, self._today(), price)
        reports = normalize_statements(symbol, records)
        logger.info("Fetched J-Quants snapshot and %d statement(s) for %s", len(reports), symbol)
        return snapshot, reports

    def fetch_statements(self, symbol: str) -> list[FinancialReport]:
        """Fetch ``symbol``'s full disclosed statement history, oldest first.

        The same request behind :meth:`fetch_fundamentals` already returns every
        disclosure the plan covers; this keeps them all instead of collapsing to
        the latest one.
        """
        reports = normalize_statements(symbol, self._fetch(symbol))
        logger.info("Fetched %d J-Quants statement(s) for %s", len(reports), symbol)
        return reports
