"""EDINET (金融庁) disclosure source for Japanese filings.

EDINET is the statutory filing system: 有価証券報告書, 四半期報告書, 臨時報告書
and so on. It has an official, documented JSON API (v2), which is why it is
implemented here and TDnet is not — TDnet publishes 適時開示 but offers no
public API, so the only route is scraping an unofficial HTML endpoint. See
:mod:`stock_ai.ir.sources` for how to plug a third-party TDnet feed in.

Two things shape this adapter:

- **The API is indexed by date, not by company.** There is no "documents for
  7203" endpoint; you ask for a day and filter. Monitoring a watchlist
  therefore costs one request per *day* looked back, not per symbol, so the
  day responses are cached and shared across symbols in one pass.
- **``secCode`` is five digits.** Toyota is ``72030``, not ``7203`` — the
  trailing zero is a share-class digit. Matching on the raw string silently
  finds nothing, so codes are compared on their first four digits.

.. warning::
   Written against the published EDINET API v2 specification and exercised
   against recorded response shapes, but **not** verified against the live
   service (this environment has no outbound access to it). Check the first
   real run: a field name that has drifted would show up as zero disclosures
   rather than as an error.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any

from pydantic import SecretStr

from stock_ai.core.exceptions import DataError
from stock_ai.core.logging import get_logger
from stock_ai.data.types import Disclosure

logger = get_logger(__name__)

_DOCUMENTS_URL = "https://api.edinet-fsa.go.jp/api/v2/documents.json"
_DOCUMENT_URL = "https://api.edinet-fsa.go.jp/api/v2/documents/{doc_id}"

# A day fetcher takes a date and returns that day's raw ``results`` entries.
DayFetcher = Callable[[dt.date], list[dict[str, Any]]]

#: ``docTypeCode`` -> a short Japanese label, used when a filing has no
#: description of its own. Only the codes a watchlist cares about are named;
#: anything else keeps its numeric code.
DOC_TYPE_LABELS: dict[str, str] = {
    "030": "有価証券届出書",
    "120": "有価証券報告書",
    "130": "訂正有価証券報告書",
    "140": "四半期報告書",
    "150": "訂正四半期報告書",
    "160": "半期報告書",
    "170": "訂正半期報告書",
    "180": "臨時報告書",
    "190": "訂正臨時報告書",
    "200": "自己株券買付状況報告書",
    "220": "親会社等状況報告書",
    "230": "自己株券買付状況報告書",
    "350": "大量保有報告書",
    "360": "変更報告書",
}

#: Filings that never reach a reader: withdrawn, or hidden by EDINET.
_WITHDRAWN = "1"
_VISIBLE_DISCLOSURE_STATUS = {"", "0"}


def normalize_sec_code(symbol: str) -> str | None:
    """Return the four-digit securities code for ``symbol``, or ``None``.

    Accepts the shapes symbols actually arrive in — ``7203``, ``7203.T``,
    ``7203.JP`` — and rejects anything that is not a Japanese ticker, so a US
    symbol on the watchlist is skipped rather than matched by accident.
    """
    head = symbol.strip().upper().split(".")[0]
    if len(head) == 4 and head.isdigit():
        return head
    return None


def _sec_code_of(record: dict[str, Any]) -> str | None:
    """Return a record's four-digit securities code, if it has one.

    Funds and non-listed filers have no ``secCode``; EDINET's five-digit form
    carries a trailing share-class digit that must be dropped before matching.
    """
    raw = record.get("secCode")
    if not raw:
        return None
    text = str(raw).strip()
    return text[:4] if len(text) >= 4 and text[:4].isdigit() else None


def _is_visible(record: dict[str, Any]) -> bool:
    """Whether a filing is live rather than withdrawn or hidden."""
    if str(record.get("withdrawalStatus") or "0") == _WITHDRAWN:
        return False
    status = str(record.get("disclosureStatus") or "0")
    return status in _VISIBLE_DISCLOSURE_STATUS


def _title_of(record: dict[str, Any]) -> str:
    """Return the filing's description, falling back to its document type."""
    description = str(record.get("docDescription") or "").strip()
    if description:
        return description
    code = str(record.get("docTypeCode") or "").strip()
    return DOC_TYPE_LABELS.get(code, f"EDINET書類 ({code})" if code else "EDINET書類")


def _submitted_on(record: dict[str, Any]) -> dt.date | None:
    """Parse ``submitDateTime`` (``YYYY-MM-DD HH:MM``) into a date."""
    raw = str(record.get("submitDateTime") or "").strip()
    if not raw:
        return None
    try:
        return dt.date.fromisoformat(raw[:10])
    except ValueError:
        return None


def to_disclosure(symbol: str, record: dict[str, Any]) -> Disclosure:
    """Map one EDINET ``results`` entry onto a :class:`Disclosure`."""
    doc_id = str(record.get("docID") or "").strip()
    filer = str(record.get("filerName") or "").strip()
    doc_type = DOC_TYPE_LABELS.get(str(record.get("docTypeCode") or ""), "")
    body_parts = [part for part in (filer, doc_type) if part]
    return Disclosure(
        symbol=symbol,
        title=_title_of(record),
        body=" / ".join(body_parts),
        published_on=_submitted_on(record),
        url=_DOCUMENT_URL.format(doc_id=doc_id) if doc_id else None,
        source="edinet",
    )


def _default_day_fetcher(api_key: SecretStr | None) -> DayFetcher:
    """Build a fetcher for one day's document list."""

    def fetch(day: dt.date) -> list[dict[str, Any]]:
        import httpx

        params: dict[str, str] = {"date": day.isoformat(), "type": "2"}
        if api_key is not None:
            # EDINET v2 documents the key as a query parameter. It is kept out
            # of every log line and error message below for the same reason the
            # notification channels redact theirs.
            params["Subscription-Key"] = api_key.get_secret_value()

        with httpx.Client(timeout=30.0) as client:
            response = client.get(_DOCUMENTS_URL, params=params)
            if response.status_code >= 400:
                raise DataError(f"EDINET returned HTTP {response.status_code} for {day}.")
            payload = response.json()

        results = payload.get("results")
        return list(results) if isinstance(results, list) else []

    return fetch


class EdinetDisclosureSource:
    """Fetch Japanese statutory filings from EDINET.

    One instance is meant to serve a whole monitoring pass: the per-day
    responses it downloads are cached, so watching twenty names costs the same
    as watching one.
    """

    name = "edinet"

    def __init__(
        self,
        api_key: SecretStr | None = None,
        lookback_days: int = 7,
        fetcher: DayFetcher | None = None,
        clock: Callable[[], dt.date] | None = None,
    ) -> None:
        """Create the source.

        Args:
            api_key: EDINET API v2 subscription key.
            lookback_days: How many days back to scan. Each day is one request,
                so this is the knob that trades coverage against API calls.
            fetcher: Callable returning one day's raw records; injected in tests.
            clock: Callable returning today's date.
        """
        self.lookback_days = max(1, lookback_days)
        self._fetch_day = fetcher or _default_day_fetcher(api_key)
        self._today = clock or dt.date.today
        self._cache: dict[dt.date, list[dict[str, Any]]] = {}

    def fetch(self, symbol: str, limit: int = 10) -> list[Disclosure]:
        """Return up to ``limit`` recent filings for ``symbol``, newest first.

        A symbol that is not a Japanese four-digit code yields nothing — EDINET
        has no filings for it, and scanning anyway would waste the day budget.
        """
        code = normalize_sec_code(symbol)
        if code is None:
            return []

        matches: list[Disclosure] = []
        for day in self._recent_days():
            for record in self._day_records(day):
                if _sec_code_of(record) != code or not _is_visible(record):
                    continue
                matches.append(to_disclosure(symbol, record))

        matches.sort(key=lambda d: d.published_on or dt.date.min, reverse=True)
        logger.debug("EDINET: %d filing(s) for %s", len(matches), symbol)
        return matches[:limit]

    def clear_cache(self) -> None:
        """Drop the cached day responses so the next pass refetches them."""
        self._cache.clear()

    def _recent_days(self) -> list[dt.date]:
        """Return the days to scan, newest first."""
        today = self._today()
        return [today - dt.timedelta(days=offset) for offset in range(self.lookback_days)]

    def _day_records(self, day: dt.date) -> list[dict[str, Any]]:
        """Return one day's records, fetching and caching on first use.

        A failed day is cached as empty rather than retried per symbol: without
        that, one bad day would cost a request for every name on the watchlist.
        """
        if day not in self._cache:
            try:
                self._cache[day] = self._fetch_day(day)
            except Exception as exc:  # one bad day must not blind the rest
                logger.warning("EDINET fetch failed for %s: %s", day, exc)
                self._cache[day] = []
        return self._cache[day]
