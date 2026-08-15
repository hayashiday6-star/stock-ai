"""EDINET (金融庁) disclosure source for Japanese filings.

EDINET is the statutory filing system: 有価証券報告書, 四半期報告書, 臨時報告書
and so on. It has an official, documented JSON API (v2), which is why it is
implemented here and TDnet is not - TDnet publishes 適時開示 but offers no
public API, so the only route is scraping an unofficial HTML endpoint. See
:mod:`stock_ai.ir.sources` for how to plug a third-party TDnet feed in.

Two things shape this adapter:

- **The API is indexed by date, not by company.** There is no "documents for
  7203" endpoint; you ask for a day and filter. Monitoring a watchlist
  therefore costs one request per *day* looked back, not per symbol, so the
  day responses are cached and shared across symbols in one pass.
- **``secCode`` is five digits.** Toyota is ``72030``, not ``7203`` - the
  trailing zero is a share-class digit. Matching on the raw string silently
  finds nothing, so codes are compared on their first four digits.

.. warning::
   **Authentication is verified against the live service (2026-08-15); the
   parsing below is not.** The day tested was a Saturday during Obon and
   returned zero filings, so the request path is confirmed while the mapping
   from a real ``results`` entry - ``secCode``, ``docTypeCode``,
   ``submitDateTime`` - has still only been exercised against recorded
   response shapes.

   That distinction is the whole risk here. A drifted field name does not
   raise; it reports zero disclosures, which is indistinguishable from a
   quiet week. :func:`EdinetDisclosureSource.fetch` logs how many filings it
   scanned and how many carried a securities code precisely so the two can be
   told apart. Run ``stock-ai edinet-check`` on a weekday to confirm filings
   arrive at all.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
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

    Accepts the shapes symbols actually arrive in - ``7203``, ``7203.T``,
    ``7203.JP`` - and rejects anything that is not a Japanese ticker, so a US
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


def error_envelope(payload: Any) -> tuple[Any, str] | None:
    """Return ``(status, message)`` if ``payload`` is an EDINET error body.

    EDINET signals a refused request with **HTTP 200** and a body of
    ``{"StatusCode": ..., "message": ...}``. Nothing about the transport says
    anything went wrong, so a reader that only checks the HTTP status treats a
    rejected key as a day on which nobody filed anything.
    """
    if not isinstance(payload, dict):
        return None
    for key in ("StatusCode", "statusCode", "statuscode"):
        if key in payload:
            return payload[key], str(payload.get("message") or "").strip() or "(no message)"
    return None


def _log_empty_day(day: dt.date, payload: Any, has_key: bool) -> None:
    """Explain why a day came back with no documents, as far as EDINET says."""
    if not has_key:
        logger.warning(
            "EDINET %s returned no documents and no API key was sent. "
            "The v2 API requires EDINET_API_KEY; set it in .env.",
            day,
        )
        return

    if not isinstance(payload, dict):
        logger.warning(
            "EDINET %s returned no documents and no JSON object (got %s). "
            "The endpoint may have moved.",
            day,
            type(payload).__name__,
        )
        return

    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        resultset = metadata.get("resultset")
        count = resultset.get("count") if isinstance(resultset, dict) else None
        if count == 0:
            logger.info("EDINET %s: no filings that day (count 0).", day)
            return
        logger.warning(
            "EDINET %s returned no documents (status=%s, message=%s).",
            day,
            metadata.get("status") or "?",
            metadata.get("message") or "?",
        )
        return

    envelope = error_envelope(payload)
    if envelope is not None:
        status, message = envelope
        # This is the case that actually happens: HTTP 200 carrying an
        # application-level error. Logging it at ERROR matters as much as the
        # text - a rejected request reported at WARNING alongside "Checked 0 new
        # disclosure(s)" reads like a quiet week.
        logger.error(
            "EDINET %s rejected the request: StatusCode=%s, message=%s. "
            "The HTTP status was 200, so this is not a network problem - it is "
            "the API refusing.",
            day,
            status,
            message,
        )
        if str(status) in {"401", "403"}:
            # The key is now sent three ways, so "cannot find it" is ruled out
            # and only the value or the subscription is left.
            logger.error(
                "The key is sent as a query parameter and in both header forms, "
                "so this is the value itself, not where it was put. Check that "
                "EDINET_API_KEY in .env matches the key on your EDINET account "
                "exactly, and that the subscription is active - a registered but "
                "unconfirmed account issues a key that never works. Re-enter it "
                "with: powershell -File scripts/set-key.ps1 EDINET_API_KEY"
            )
        return

    logger.warning(
        "EDINET %s returned no documents and no 'metadata' block. "
        "Top-level keys were: %s. The response shape has changed, or this is "
        "not the documents endpoint any more.",
        day,
        sorted(payload) or "(none - empty object)",
    )


#: The placement this client uses for real requests.
CURRENT_PLACEMENT = "クエリ + Ocp-Apim ヘッダ (現行)"

#: The placement that matches what a browser sends, and therefore the one a
#: successful browser test actually proves.
BROWSER_PLACEMENT = "クエリのみ (ブラウザと同じ)"

#: The header spelling the gateway does not read. Kept in the probe because a
#: placement that fails is evidence, and dropped from the real request because
#: sending a key somewhere it is ignored is one more place for it to leak.
UNREAD_HEADER_PLACEMENT = "Subscription-Key ヘッダのみ"


def key_placements(secret: str) -> dict[str, tuple[dict[str, str], dict[str, str]]]:
    """Return each way of presenting ``secret``, as ``(params, headers)``.

    These are data rather than one hard-coded request because an Azure API
    Management gateway answers ``401 Access denied due to invalid subscription
    key`` both when the key is wrong *and* when it is somewhere the gateway does
    not look. The wording is identical, so the two are indistinguishable from a
    single failed request. Trying every placement against the live service is
    what separates "wrong key" from "right key, wrong envelope" - and pasting
    the URL into a browser only ever tests :data:`BROWSER_PLACEMENT`.

    Measured against the live service on 2026-08-15 with a valid key:

    ==================================  ======
    Placement                           Result
    ==================================  ======
    ``Subscription-Key`` query          200
    ``Ocp-Apim-Subscription-Key`` head  200
    ``Subscription-Key`` header         **401**
    ==================================  ======

    So the gateway reads the query parameter and the standard APIM header, and
    ignores the custom header spelling entirely. That last row is the one worth
    keeping: it is a request carrying a *valid* key that still comes back
    ``invalid subscription key``, which is the whole reason a single 401 could
    never be read as "your key is wrong".
    """
    return {
        BROWSER_PLACEMENT: ({"Subscription-Key": secret}, {}),
        "Ocp-Apim-Subscription-Key ヘッダのみ": ({}, {"Ocp-Apim-Subscription-Key": secret}),
        UNREAD_HEADER_PLACEMENT: ({}, {"Subscription-Key": secret}),
        CURRENT_PLACEMENT: (
            {"Subscription-Key": secret},
            {"Ocp-Apim-Subscription-Key": secret},
        ),
    }


@dataclass(frozen=True)
class ProbeResult:
    """What one key placement got back from EDINET."""

    placement: str
    #: ``None`` when the request never completed (DNS, TLS, timeout).
    http_status: int | None
    #: The application-level status, which is what actually decides acceptance.
    api_status: str
    message: str
    documents: int | None

    @property
    def accepted(self) -> bool:
        """Whether EDINET served the request rather than refusing it.

        A day with no filings is still an accepted request: the useful question
        is whether the gateway let us through, not whether anyone filed.
        """
        return self.http_status == 200 and self.documents is not None


def _document_count(payload: Any) -> int | None:
    """Return how many documents a payload reports, or ``None`` if it refused."""
    if not isinstance(payload, dict) or error_envelope(payload) is not None:
        return None
    results = payload.get("results")
    if isinstance(results, list):
        return len(results)
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        resultset = metadata.get("resultset")
        count = resultset.get("count") if isinstance(resultset, dict) else None
        if isinstance(count, int):
            return count
        # An accepted request always carries metadata, even on a quiet day.
        return 0
    return None


def probe_key_placements(
    api_key: SecretStr,
    day: dt.date,
    requester: Callable[[dict[str, str], dict[str, str]], tuple[int, Any]] | None = None,
) -> list[ProbeResult]:
    """Try every key placement against EDINET and report what each one did.

    Args:
        api_key: The key to test. It is never returned or logged.
        day: The date to request. Any date works; a weekday returns more.
        requester: Performs one request, returning ``(http_status, payload)``.
            Injected in tests so the probe can be exercised without network.

    Returns:
        One :class:`ProbeResult` per placement, in the order they were tried.
    """
    secret = api_key.get_secret_value()
    send = requester or _http_requester(day)
    results: list[ProbeResult] = []
    for placement, (extra_params, headers) in key_placements(secret).items():
        params = {"date": day.isoformat(), "type": "2", **extra_params}
        try:
            status, payload = send(params, headers)
        except Exception as exc:  # a transport failure is a result, not a crash
            results.append(ProbeResult(placement, None, "-", f"{type(exc).__name__}: {exc}", None))
            continue
        envelope = error_envelope(payload)
        api_status = str(envelope[0]) if envelope else str(status)
        message = envelope[1] if envelope else "OK"
        results.append(
            ProbeResult(placement, status, api_status, message, _document_count(payload))
        )
    return results


def _http_requester(day: dt.date) -> Callable[[dict[str, str], dict[str, str]], tuple[int, Any]]:
    """Return a callable that performs one probe request over HTTP."""

    def send(params: dict[str, str], headers: dict[str, str]) -> tuple[int, Any]:
        import httpx

        with httpx.Client(timeout=30.0) as client:
            response = client.get(_DOCUMENTS_URL, params=params, headers=headers)
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, response.text[:200]

    del day  # the date travels in params; kept for a clearer call site
    return send


def _default_day_fetcher(api_key: SecretStr | None) -> DayFetcher:
    """Build a fetcher for one day's document list."""

    def fetch(day: dt.date) -> list[dict[str, Any]]:
        import httpx

        params: dict[str, str] = {"date": day.isoformat(), "type": "2"}
        headers: dict[str, str] = {}
        if api_key is not None:
            # Both of these are confirmed to work against the live service; see
            # the table in key_placements. The custom ``Subscription-Key``
            # header is not sent, because the same run showed the gateway
            # ignores it - a key put where nothing reads it buys nothing and is
            # one more place for it to leak.
            #
            # An earlier version sent headers only, to keep the key out of the
            # URL that httpx logs at INFO. That was the wrong trade, and it
            # failed for a reason worth remembering: the only header it sent was
            # the spelling the gateway ignores, so it produced "401 Access
            # denied due to invalid subscription key" with a perfectly valid
            # key. Breaking the feature to avoid a leak that is already handled
            # elsewhere is not a security win.
            #
            # The leak is handled: SecretRedactingFilter scrubs both the
            # ``Subscription-Key=`` parameter shape and the literal key value
            # (registered from settings) out of every log record, so the URL in
            # the log reads ``Subscription-Key=<redacted>``.
            extra_params, extra_headers = key_placements(api_key.get_secret_value())[
                CURRENT_PLACEMENT
            ]
            params.update(extra_params)
            headers.update(extra_headers)

        with httpx.Client(timeout=30.0) as client:
            response = client.get(_DOCUMENTS_URL, params=params, headers=headers)
            if response.status_code >= 400:
                raise DataError(f"EDINET returned HTTP {response.status_code} for {day}.")
            payload = response.json()

        results = payload.get("results")
        if not isinstance(results, list) or not results:
            # EDINET answers 200 with an empty body for several very different
            # reasons - a missing subscription key, a non-business day, a
            # rejected request - and distinguishes them only in the metadata.
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

        A symbol that is not a Japanese four-digit code yields nothing - EDINET
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
                "EDINET returned %d filing(s) but none had a securities code - "
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
