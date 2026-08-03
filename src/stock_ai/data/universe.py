"""The listed-company universe: which symbols exist, and on which segment.

Everything else in this project takes symbols as input. This is where that list
comes from — J-Quants' listed-info endpoint returns every listing when asked
without a code, which is the only practical way to get "all of TSE Prime"
without maintaining a hand-curated file that goes stale on every IPO.

Segment matters more than it looks. Prime is ~1,600 names, the whole exchange
is ~4,000, and a bulk price backfill costs one request per symbol — so picking
the segment is the difference between a ten-minute job and an hour-long one.

.. warning::
   Written against the published J-Quants V2 specification. The field names are
   guarded with fallbacks, but this has not been run against the live API from
   the environment it was built in. If a fetch returns zero listings, suspect a
   renamed field before suspecting your key.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from enum import StrEnum
from typing import Any

from pydantic import SecretStr

from stock_ai.core.exceptions import DataError
from stock_ai.core.logging import get_logger
from stock_ai.data.jquants_profile import _sector_of
from stock_ai.data.sectors import Sector, from_tse33
from stock_ai.data.types import SecurityProfile

logger = get_logger(__name__)

_LISTED_INFO_URL = "https://api.jquants.com/v2/listed/info"

#: How far back to retry a listing request that was refused outright.
#:
#: Undated, the endpoint answers with *today's* snapshot, and the entry-level
#: J-Quants plans serve data on a delay rather than serving it late — a request
#: inside the embargo is refused, not queued. Asking for a date safely outside
#: the delay is therefore the difference between "your plan cannot do this" and
#: "your plan cannot do this *today*", and only one of those is true.
_DELAYED_PLAN_DAYS = 90

# A fetcher returns every listing record the plan exposes.
ListingsFetcher = Callable[[], list[dict[str, Any]]]


class Segment(StrEnum):
    """A TSE market segment, as the universe is usually sliced."""

    PRIME = "prime"
    STANDARD = "standard"
    GROWTH = "growth"
    ALL = "all"


#: TSE market codes per segment. The pre-2022 codes are kept because listings
#: that have not been re-tagged still carry them, and dropping those names
#: would quietly shrink the universe.
_SEGMENT_CODES: dict[Segment, frozenset[str]] = {
    Segment.PRIME: frozenset({"0111", "0101"}),  # プライム (+ 旧東証一部)
    Segment.STANDARD: frozenset({"0112", "0102", "0106"}),  # スタンダード (+ 旧二部/JQS)
    Segment.GROWTH: frozenset({"0113", "0104", "0107"}),  # グロース (+ 旧マザーズ/JQG)
}

#: Substrings matched against the segment *name*, for payloads that carry the
#: label but not the code.
_SEGMENT_NAMES: dict[Segment, tuple[str, ...]] = {
    Segment.PRIME: ("プライム", "PRIME", "第一部"),
    Segment.STANDARD: ("スタンダード", "STANDARD", "第二部"),
    Segment.GROWTH: ("グロース", "GROWTH", "マザーズ"),
}


def _text(record: dict[str, Any], *keys: str) -> str | None:
    """Return the first non-empty string among ``keys``."""
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return None


#: Every market code the segment table knows, for telling "unrecognised code"
#: apart from "code for a different segment".
_KNOWN_CODES: frozenset[str] = frozenset().union(*_SEGMENT_CODES.values())


def _matches_segment(record: dict[str, Any], segment: Segment) -> bool:
    """Whether a listing belongs to ``segment``.

    The market **code** decides whenever it is one this table knows. The label
    is only consulted when the code is missing or unrecognised — otherwise a
    payload whose code and label disagree would be rescued by the label and
    land in the wrong segment, which is how an "all of Prime" screen quietly
    picks up Growth names.
    """
    if segment is Segment.ALL:
        return True

    code = _text(record, "MktCd", "MarketCode")
    if code and code in _KNOWN_CODES:
        return code in _SEGMENT_CODES[segment]

    label = _text(record, "MktCdName", "MarketCodeName", "MarketName")
    if label:
        upper = label.upper()
        return any(token.upper() in upper for token in _SEGMENT_NAMES[segment])
    return False


def _code_of(record: dict[str, Any]) -> str | None:
    """Return the four-digit securities code for a listing.

    J-Quants writes codes as five digits with a share-class suffix; the rest of
    this project keys on four, so the extra digit is dropped here rather than
    leaking a second symbol format into the database.
    """
    raw = _text(record, "Code", "LocalCode", "SecCode")
    if raw is None:
        return None
    if len(raw) == 5 and raw.isdigit() and raw.endswith("0"):
        raw = raw[:4]
    return raw if len(raw) == 4 and raw.isdigit() else None


def _is_operating_company(record: dict[str, Any]) -> bool:
    """Whether a listing is an ordinary company rather than a fund.

    ETFs, REITs, and index products share the exchange and the code format with
    equities, and letting them into the universe poisons everything downstream:
    they have no revenue to grow, no dividend policy to score, and no sector to
    group by. The tell is the TSE-33 code — funds carry ``9999`` (その他) or a
    code outside the industry table.

    A record with **no** sector field at all is kept. Rejecting those would turn
    a renamed upstream field into an empty universe, which is the silent failure
    this project keeps trying to avoid; a stray ETF is the cheaper mistake.
    """
    code = _text(record, "Sec33Cd", "Sector33Code")
    if code is None:
        return True
    return from_tse33(code) is not Sector.OTHER


def normalize_listings(
    records: list[dict[str, Any]], segment: Segment = Segment.ALL
) -> list[SecurityProfile]:
    """Turn raw listing records into profiles for one segment, sorted by code.

    Two kinds of record are dropped: those without a usable four-digit
    securities code, and funds (see :func:`_is_operating_company`).
    """
    profiles: dict[str, SecurityProfile] = {}
    funds = 0
    unclassified = 0

    for record in records:
        if not _matches_segment(record, segment):
            continue
        code = _code_of(record)
        if code is None:
            continue
        if not _is_operating_company(record):
            funds += 1
            continue
        if _text(record, "Sec33Cd", "Sector33Code") is None:
            unclassified += 1

        profiles[code] = SecurityProfile(
            symbol=code,
            market="JP",
            name=_text(record, "Name", "CompanyName", "CompanyNameEnglish"),
            sector=str(_sector_of(record)),
            industry=_text(record, "Sec33Name", "Sector33CodeName", "Sec17Name"),
        )

    if funds:
        logger.info("Excluded %d fund/index listing(s) from the universe", funds)
    if unclassified:
        logger.warning(
            "%d listing(s) had no sector code and were kept unfiltered — "
            "check the payload if this is most of them.",
            unclassified,
        )
    return [profiles[code] for code in sorted(profiles)]


def _service_message(response: Any) -> str:
    """Return J-Quants' own explanation for a failed response, if it gave one.

    The status code alone is not enough to act on: 403 covers both "this
    endpoint is not in your plan" and "this *date* is not in your plan", and
    only the body distinguishes them. Discarding it turns a one-line answer into
    a guessing game.
    """
    try:
        payload = response.json()
    except Exception:
        payload = None
    if isinstance(payload, dict):
        for key in ("message", "Message", "error", "detail"):
            value = payload.get(key)
            if value:
                return str(value).strip()
    text = (getattr(response, "text", "") or "").strip()
    return text[:200] if text else "no message returned"


def _delayed_snapshot_date(today: dt.date | None = None) -> dt.date:
    """A date old enough to sit outside a delayed plan's embargo."""
    return (today or dt.date.today()) - dt.timedelta(days=_DELAYED_PLAN_DAYS)


def _default_fetcher(
    api_key: SecretStr | None,
    as_of: dt.date | None = None,
    clock: Callable[[], dt.date] | None = None,
) -> ListingsFetcher:
    """Build a fetcher for the full listing list.

    Args:
        api_key: J-Quants V2 API key.
        as_of: Snapshot date to request. ``None`` asks for the current snapshot
            and falls back to a delayed one if that is refused.
        clock: Callable returning today's date; injected in tests.
    """
    today = clock or dt.date.today

    def fetch() -> list[dict[str, Any]]:
        import httpx

        headers = {"x-api-key": api_key.get_secret_value()} if api_key else {}

        def collect(client: Any, date: dt.date | None) -> list[dict[str, Any]]:
            """Page through the endpoint for one snapshot date."""
            records: list[dict[str, Any]] = []
            pagination_key: str | None = None
            while True:
                params: dict[str, str] = {}
                if date is not None:
                    params["date"] = date.isoformat()
                if pagination_key:
                    params["pagination_key"] = pagination_key
                response = client.get(_LISTED_INFO_URL, headers=headers, params=params)
                if response.status_code >= 400:
                    raise _ListingsRefusedError(
                        response.status_code, _service_message(response), date
                    )
                payload = response.json()
                records.extend(payload.get("data") or payload.get("info") or [])
                pagination_key = payload.get("pagination_key")
                if not pagination_key:
                    return records

        with httpx.Client(timeout=60.0) as client:
            try:
                return collect(client, as_of)
            except _ListingsRefusedError as refusal:
                # Retrying is worth one request: an undated 403 is the shape a
                # delayed plan produces, and the difference between the two
                # causes decides whether the user needs a new plan or a flag.
                if refusal.status != 403 or as_of is not None:
                    raise refusal.as_data_error() from None
                retry_date = _delayed_snapshot_date(today())
                logger.warning(
                    "J-Quants refused the current listing snapshot (403: %s). "
                    "Retrying as of %s, in case the plan serves delayed data.",
                    refusal.message,
                    retry_date,
                )
                try:
                    records = collect(client, retry_date)
                except _ListingsRefusedError as second:
                    raise second.as_data_error() from None
                logger.info(
                    "J-Quants served the %s snapshot. The plan is delayed rather "
                    "than missing this endpoint; pass --as-of to pick the date.",
                    retry_date,
                )
                return records

    return fetch


class _ListingsRefusedError(Exception):
    """A listing request the service turned down, with its own explanation."""

    def __init__(self, status: int, message: str, date: dt.date | None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.date = date

    def as_data_error(self) -> DataError:
        """Render the refusal as the error the CLI shows the user."""
        when = f" for {self.date}" if self.date else ""
        return DataError(f"J-Quants listed/info returned {self.status}{when}: {self.message}")


class JQuantsUniverse:
    """Fetch the listed-company universe from J-Quants."""

    name = "jquants"

    def __init__(
        self,
        api_key: SecretStr | None = None,
        fetcher: ListingsFetcher | None = None,
        as_of: dt.date | None = None,
    ) -> None:
        """Create the universe source.

        Args:
            api_key: J-Quants V2 API key.
            fetcher: Callable returning raw listing records; injected in tests.
            as_of: Snapshot date to request. Leave unset to ask for the current
                one and fall back to a delayed snapshot if the plan refuses it.
        """
        self._fetch = fetcher or _default_fetcher(api_key, as_of=as_of)

    def profiles(
        self, segment: Segment = Segment.PRIME, limit: int | None = None
    ) -> list[SecurityProfile]:
        """Return the segment's listings, sorted by code.

        Args:
            segment: Which market segment to keep.
            limit: Cap the result — useful for a trial run before committing to
                a backfill that will take an hour.

        Raises:
            DataError: If the endpoint returns nothing at all.
        """
        records = self._fetch()
        if not records:
            raise DataError("J-Quants returned no listings.")

        profiles = normalize_listings(records, segment)
        logger.info("Universe: %d listing(s) on %s", len(profiles), segment.value)
        return profiles[:limit] if limit else profiles
