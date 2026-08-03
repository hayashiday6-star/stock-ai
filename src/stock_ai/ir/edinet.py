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


def _log_empty_day(day: dt.date, payload: dict[str, Any], has_key: bool) -> None:
    """Explain why a day came back with no documents, as far as EDINET says."""
    metadata = payload.get("metadata")
    status = message = None
    if isinstance(metadata, dict):
        result = metadata.get("resultset") if isinstance(metadata.get("resultset"), dict) else {}
        status = metadata.get("status")
        message = metadata.get("message")
        count = result.get("count") if isinstance(result, dict) else None
        if count == 0:
            logger.info("EDINET %s: no filings that day (count 0).", day)
            return

    if not has_key:
        logger.warning(
            "EDINET %s returned no documents and no API key was sent. "
            "The v2 API requires EDINET_API_KEY; set it in .env.",
            day,
        )
        return

    logger.warning(
        "EDINET %s returned no documents (status=%s, message=%s).",
        day,
        status or "?",
        message or "?",
    )


def _default_day_fetcher(api_key: SecretStr | None) -> DayFetcher:
    """Build a fetcher for one day's document list."""

    def fetch(day: dt.date) -> list[dict[str, Any]]:
        import httpx

        params: dict[str, str] = {"date": day.isoformat(), "type": "2"}
        headers: dict[str, str] = {}
        if api_key is not None:
            # EDINET v2 documents the key as a query parameter, and that is the
            # authoritative form. The header is sent as well because the service
            # sits behind Azure API Management, whose own convention is the
            # header — and the failure mode if only one of the two is accepted
            # is HTTP 200 with an empty body, which reads as "nothing was filed"
            # rather than as an auth error. Sending both costs nothing and
            # removes a silent failure. The value is kept out of every log line
            # and error message for the same reason the notification channels
            # redact theirs.
            secret = api_key.get_secret_value()
            params["Subscription-Key"] = secret
            headers["Ocp-Apim-Subscription-Key"] = secret

        with httpx.Client(timeout=30.0) as client:
            response = client.get(_DOCUMENTS_URL, params=params, headers=headers)
            if response.status_code >= 400:
                raise DataError(f"EDINET returned HTTP {response.status_code} for {day}.")
            payload = response.json()

        results = payload.get("results")
        if not isinstance(results, list) or not results:
            # EDINET answers 200 with an empty body for several very different
            # reasons — a missing subscription key, a non-business day, a
            # rejected request — and distinguishes them only in the metadata.
            # Surfacing that is what turns "zero filings" from a dead end into
            # a diagnosable one.
            _log_empty_day(day, payload, has_key=api_key is not None)
            return []
        return list(results)

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
        scanned = 0
        with_codes = 0
        for day in self._recent_days():
            for record in self._day_records(day):
                scanned += 1
                record_code = _sec_code_of(record)
                if record_code is not None:
                    with_codes += 1
                if record_code != code or not _is_visible(record):
                    continue
                matches.append(to_disclosure(symbol, record))

        matches.sort(key=lambda d: d.published_on or dt.date.min, reverse=True)
        # Zero matches is ambiguous on its own: the company may simply not have
        # filed, or the payload may have stopped carrying the field we match on.
        # Logging what was scanned is what tells those two apart.
        logger.info(
            "EDINET %s: %d match(es) from %d filing(s) over %d day(s), "
            "%d of which carried a securities code",
            symbol,
            len(matches),
            scanned,
            self.lookback_days,
            with_codes,
        )
        if scanned and not with_codes:
            logger.warning(
                "EDINET returned %d filing(s) but none had a securities code — "
                "the 'secCode' field may have been renamed upstream.",
                scanned,
            )
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
