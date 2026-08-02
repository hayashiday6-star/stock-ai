"""J-Quants fundamentals provider (Japanese equities).

Maps J-Quants financial-statement records to the canonical
:class:`~stock_ai.data.types.Fundamentals`. Price-dependent ratios (PER, PBR,
dividend yield, market cap) are left ``None`` here — they require a current
price and can be filled by a later enrichment step — while revenue, net income,
and ROE (profit / equity) come straight from the statements.

The HTTP call is injectable so the provider is unit-testable without network.
The default fetcher targets the J-Quants V2 statements endpoint; verify its
exact shape against current J-Quants docs before live use.
"""

from __future__ import annotations

import datetime as dt
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

_STATEMENTS_URL = "https://api.jquants.com/v2/fins/statements"


def _to_float(value: Any) -> float | None:
    """Parse a J-Quants numeric field (strings, blanks) to ``float`` or ``None``."""
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _latest(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the most recently disclosed statement record."""
    return max(records, key=lambda r: str(r.get("DisclosedDate", "")))


def normalize_statement(symbol: str, records: list[dict[str, Any]], as_of: dt.date) -> Fundamentals:
    """Build a :class:`Fundamentals` snapshot from statement records.

    Args:
        symbol: The security code.
        records: J-Quants ``statements`` records.
        as_of: Snapshot date.

    Returns:
        A fundamentals snapshot (price-dependent ratios left ``None``).

    Raises:
        DataError: If ``records`` is empty.
    """
    if not records:
        raise DataError(f"No J-Quants statements for {symbol!r}.")

    latest = _latest(records)
    revenue = _to_float(latest.get("NetSales"))
    net_income = _to_float(latest.get("Profit"))
    equity = _to_float(latest.get("Equity"))
    roe = net_income / equity if (net_income is not None and equity) else None

    return Fundamentals(
        symbol=symbol,
        as_of=as_of,
        roe=roe,
        revenue=revenue,
        net_income=net_income,
        # PER / PBR / dividend yield / market cap need a current price → left None.
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
                records.extend(payload.get("statements", []))
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
    ) -> None:
        """Create the provider.

        Args:
            api_key: J-Quants V2 API key.
            fetcher: Callable performing the raw fetch; injected in tests.
            clock: Callable returning the snapshot date; defaults to today.
        """
        self._fetch = fetcher or _default_fetcher(api_key)
        self._today = clock or dt.date.today

    def fetch_fundamentals(self, symbol: str) -> Fundamentals:
        """Fetch and normalize the latest fundamentals for ``symbol``."""
        records = self._fetch(symbol)
        snapshot = normalize_statement(symbol, records, self._today())
        logger.info("Fetched J-Quants fundamentals for %s", symbol)
        return snapshot
