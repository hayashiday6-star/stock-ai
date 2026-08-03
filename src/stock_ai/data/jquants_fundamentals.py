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
from stock_ai.data.types import Fundamentals

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


def normalize_statement(
    symbol: str,
    records: list[dict[str, Any]],
    as_of: dt.date,
    price: float | None = None,
) -> Fundamentals:
    """Build a :class:`Fundamentals` snapshot from ``fins/summary`` records.

    Args:
        symbol: The security code.
        records: J-Quants summary records (newest disclosure wins).
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
    revenue = _first(latest, "Sales", "NetSales")
    net_income = _first(latest, "NP", "Profit")
    equity = _first(latest, "Eq", "Equity")
    eps = _first(latest, "EPS")
    bps = _first(latest, "BPS")
    dividend = _first(latest, "DivAnn")
    shares = _first(latest, "ShOutFY")

    roe = net_income / equity if (net_income is not None and equity) else None
    per = price / eps if (price is not None and eps) else None
    pbr = price / bps if (price is not None and bps) else None
    dividend_yield = dividend / price if (price and dividend is not None) else None
    market_cap = price * shares if (price is not None and shares) else None

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
                response.raise_for_status()
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
            except Exception as exc:  # price is optional — never fail the fetch
                logger.warning("Price lookup failed for %s: %s", symbol, exc)
        snapshot = normalize_statement(symbol, records, self._today(), price)
        logger.info("Fetched J-Quants fundamentals for %s", symbol)
        return snapshot
