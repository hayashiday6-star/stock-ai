"""J-Quants listing profiles: company name and sector for Japanese equities.

Sector lives on the *listing* endpoint, not on the statements one, so this is a
separate provider from :mod:`stock_ai.data.jquants_fundamentals`. Both the
TOPIX-17 and TSE-33 codes are accepted; the finer TSE-33 wins when present
because it maps onto the canonical buckets with less guesswork.

As elsewhere in the data layer, the HTTP call is injectable so the provider is
unit-testable without network.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import SecretStr

from stock_ai.core.exceptions import DataError
from stock_ai.core.logging import get_logger
from stock_ai.data.sectors import Sector, from_topix17, from_tse33
from stock_ai.data.types import SecurityProfile

logger = get_logger(__name__)

# A fetcher takes a symbol and returns raw listing records.
ListingFetcher = Callable[[str], list[dict[str, Any]]]

_MASTER_URL = "https://api.jquants.com/v2/equities/master"


def _text(record: dict[str, Any], *keys: str) -> str | None:
    """Return the first non-empty string among ``keys``."""
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return None


def _sector_of(record: dict[str, Any]) -> Sector:
    """Resolve a listing record's sector, preferring the finer TSE-33 code."""
    tse33 = _text(record, "S33", "Sec33Cd", "Sector33Code")
    if tse33 is not None:
        sector = from_tse33(tse33)
        if sector is not Sector.OTHER:
            return sector

    topix17 = _text(record, "S17", "Sec17Cd", "Sector17Code")
    if topix17 is not None:
        return from_topix17(topix17)
    return Sector.OTHER


def normalize_listing(symbol: str, records: list[dict[str, Any]]) -> SecurityProfile:
    """Build a :class:`SecurityProfile` from J-Quants listing records.

    Args:
        symbol: The security code.
        records: Listing records; the last one wins, which is the most recent
            state when the endpoint returns a history of listing changes.

    Returns:
        The normalized profile.

    Raises:
        DataError: If ``records`` is empty.
    """
    if not records:
        raise DataError(f"No J-Quants listing info for {symbol!r}.")

    record = records[-1]
    return SecurityProfile(
        symbol=symbol,
        market="JP",
        name=_text(record, "CoName", "Name", "CompanyName", "CoNameEn", "CompanyNameEnglish"),
        sector=str(_sector_of(record)),
        # The Japanese sector label is the provider's own finer classification.
        industry=_text(record, "S33Nm", "Sec33Name", "Sector33CodeName", "S17Nm", "Sec17Name"),
    )


def _default_fetcher(api_key: SecretStr | None) -> ListingFetcher:
    """Build a fetcher that calls the J-Quants V2 listed-info endpoint."""

    def fetch(symbol: str) -> list[dict[str, Any]]:
        import httpx

        headers = {"x-api-key": api_key.get_secret_value()} if api_key else {}
        with httpx.Client(timeout=30.0) as client:
            response = client.get(_MASTER_URL, headers=headers, params={"code": symbol})
            response.raise_for_status()
            payload = response.json()
        # V2 returns {"data": [...]}; older shapes used "info".
        return payload.get("data") or payload.get("info") or []

    return fetch


class JQuantsProfileProvider:
    """Fetch Japanese listing profiles (name, sector) via J-Quants."""

    name = "jquants"

    def __init__(
        self, api_key: SecretStr | None = None, fetcher: ListingFetcher | None = None
    ) -> None:
        """Create the provider.

        Args:
            api_key: J-Quants V2 API key.
            fetcher: Callable performing the raw fetch; injected in tests.
        """
        self._fetch = fetcher or _default_fetcher(api_key)

    def fetch_profile(self, symbol: str) -> SecurityProfile:
        """Fetch and normalize the listing profile for ``symbol``."""
        profile = normalize_listing(symbol, self._fetch(symbol))
        logger.info("Fetched J-Quants profile for %s (%s)", symbol, profile.sector)
        return profile
