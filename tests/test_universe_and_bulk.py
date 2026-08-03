"""Tests for the listed universe and bulk ingestion.

The universe parsing is checked against recorded J-Quants ``listed/info``
response shapes; the live API is not reachable from the build environment, so a
renamed upstream field would show up as an empty universe rather than fail here.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from typing import Any

import numpy as np
import pandas as pd
import pytest

from stock_ai.core.exceptions import DataError
from stock_ai.data.bulk import BulkIngester, Dataset, store_universe
from stock_ai.data.sectors import Sector
from stock_ai.data.types import FinancialReport
from stock_ai.data.universe import JQuantsUniverse, Segment, normalize_listings
from stock_ai.database.engine import Database
from stock_ai.database.repository import (
    FinancialStatementRepository,
    PriceRepository,
    get_profile,
    list_securities,
)


@pytest.fixture
def database() -> Iterator[Database]:
    db = Database("sqlite:///:memory:")
    db.create_all()
    yield db
    db.dispose()


def _listing(**overrides: Any) -> dict[str, Any]:
    """One ``listed/info`` entry, shaped like the real payload."""
    base: dict[str, Any] = {
        "Date": "2026-08-03",
        "Code": "72030",
        "Name": "トヨタ自動車",
        "MktCd": "0111",
        "MktCdName": "プライム",
        "Sec33Cd": "3700",
        "Sec33Name": "輸送用機器",
        "Sec17Cd": "6",
    }
    base.update(overrides)
    return base


# --- code handling ----------------------------------------------------------


def test_the_five_digit_code_is_stored_as_four() -> None:
    """Keeping both formats would split one company across two symbols."""
    (profile,) = normalize_listings([_listing(Code="72030")])
    assert profile.symbol == "7203"


def test_a_four_digit_code_passes_through() -> None:
    (profile,) = normalize_listings([_listing(Code="7203")])
    assert profile.symbol == "7203"


def test_alphanumeric_and_malformed_codes_are_dropped() -> None:
    assert normalize_listings([_listing(Code="253A0")]) == []
    assert normalize_listings([_listing(Code="72031")]) == []  # not a share-class 0
    assert normalize_listings([_listing(Code="")]) == []


# --- segments ---------------------------------------------------------------


def test_segments_split_by_market_code() -> None:
    records = [
        _listing(Code="72030", MktCd="0111", MktCdName="プライム"),
        _listing(Code="45930", MktCd="0113", MktCdName="グロース", Sec33Cd="3250"),
        _listing(Code="12340", MktCd="0112", MktCdName="スタンダード", Sec33Cd="5250"),
    ]
    assert [p.symbol for p in normalize_listings(records, Segment.PRIME)] == ["7203"]
    assert [p.symbol for p in normalize_listings(records, Segment.GROWTH)] == ["4593"]
    assert [p.symbol for p in normalize_listings(records, Segment.STANDARD)] == ["1234"]
    assert len(normalize_listings(records, Segment.ALL)) == 3


def test_pre_2022_market_codes_still_count() -> None:
    """A listing never re-tagged after the 2022 restructure is still Prime."""
    records = [_listing(Code="99840", MktCd="0101", Sec33Cd="5250")]
    assert [p.symbol for p in normalize_listings(records, Segment.PRIME)] == ["9984"]


def test_the_segment_label_is_used_when_the_code_is_absent() -> None:
    record = _listing(Code="72030", MktCdName="プライム")
    del record["MktCd"]
    assert len(normalize_listings([record], Segment.PRIME)) == 1


def test_a_listing_on_another_segment_is_excluded() -> None:
    assert normalize_listings([_listing(MktCd="0113", MktCdName="グロース")], Segment.PRIME) == []


def test_a_known_code_beats_a_contradicting_label() -> None:
    """Otherwise a mislabelled payload lands Growth names in a Prime screen."""
    record = _listing(Code="45930", MktCd="0113", MktCdName="プライム", Sec33Cd="3250")
    assert normalize_listings([record], Segment.PRIME) == []
    assert [p.symbol for p in normalize_listings([record], Segment.GROWTH)] == ["4593"]


def test_an_unrecognised_code_falls_back_to_the_label() -> None:
    record = _listing(Code="72030", MktCd="0299", MktCdName="プライム")
    assert [p.symbol for p in normalize_listings([record], Segment.PRIME)] == ["7203"]


# --- funds ------------------------------------------------------------------


def test_etfs_and_reits_are_excluded() -> None:
    """A universe full of funds would poison every screen built on it."""
    records = [
        _listing(Code="72030", Sec33Cd="3700"),
        _listing(Code="13060", Name="TOPIX連動ETF", Sec33Cd="9999"),
        _listing(Code="89510", Name="日本ビルファンド", Sec33Cd="9999"),
    ]
    assert [p.symbol for p in normalize_listings(records)] == ["7203"]


def test_a_listing_with_no_sector_field_is_kept() -> None:
    """A renamed field must not silently empty the universe."""
    record = _listing(Code="12340")
    del record["Sec33Cd"]
    assert [p.symbol for p in normalize_listings([record])] == ["1234"]


# --- profiles ---------------------------------------------------------------


def test_the_profile_carries_name_sector_and_market() -> None:
    (profile,) = normalize_listings([_listing()])
    assert profile.name == "トヨタ自動車"
    assert profile.sector == str(Sector.CONSUMER_CYCLICAL)
    assert profile.industry == "輸送用機器"
    assert profile.market == "JP"


def test_duplicate_codes_collapse_to_the_last_record() -> None:
    records = [_listing(Code="72030", Name="旧名"), _listing(Code="72030", Name="新名")]
    (profile,) = normalize_listings(records)
    assert profile.name == "新名"


def test_the_universe_is_sorted_by_code() -> None:
    records = [
        _listing(Code="99840", Sec33Cd="5250"),
        _listing(Code="13010", Sec33Cd="0050"),
        _listing(Code="72030"),
    ]
    assert [p.symbol for p in normalize_listings(records)] == ["1301", "7203", "9984"]


# --- the source -------------------------------------------------------------


def test_the_source_filters_and_limits() -> None:
    records = [_listing(Code=f"{1000 + i}0", Sec33Cd="3700") for i in range(10)]
    universe = JQuantsUniverse(fetcher=lambda: records)
    assert len(universe.profiles(Segment.PRIME)) == 10
    assert len(universe.profiles(Segment.PRIME, limit=3)) == 3


def test_an_empty_response_is_an_error_not_an_empty_universe() -> None:
    """Silently returning nothing would look like "no listings today"."""
    with pytest.raises(DataError):
        JQuantsUniverse(fetcher=lambda: []).profiles()


# --- storing ----------------------------------------------------------------


def test_storing_the_universe_creates_securities(database: Database) -> None:
    profiles = normalize_listings([_listing(Code="72030"), _listing(Code="83060", Sec33Cd="7050")])
    assert store_universe(database, profiles) == 2

    with database.session() as session:
        assert [s for s, _m in list_securities(session)] == ["7203", "8306"]
        stored = get_profile(session, "7203")
    assert stored is not None
    assert stored.market == "JP"
    assert stored.sector == str(Sector.CONSUMER_CYCLICAL)


# --- bulk ingestion ---------------------------------------------------------


class _Statements:
    """A statements provider that fails for one nominated symbol."""

    def __init__(self, failing: str | None = None) -> None:
        self.failing = failing
        self.calls: list[str] = []

    def fetch_statements(self, symbol: str) -> list[FinancialReport]:
        self.calls.append(symbol)
        if symbol == self.failing:
            raise RuntimeError("HTTP 500")
        return [
            FinancialReport(
                symbol=symbol,
                fiscal_year=2024,
                disclosed_on=dt.date(2024, 5, 10),
                revenue=100.0,
            )
        ]


class _Prices:
    """A price provider returning a short flat series."""

    def __init__(self, failing: str | None = None) -> None:
        self.failing = failing
        self.calls: list[str] = []

    def fetch_prices(self, symbol: str, start: dt.date, end: dt.date) -> pd.DataFrame:
        self.calls.append(symbol)
        if symbol == self.failing:
            raise RuntimeError("no data")
        index = pd.date_range(end - dt.timedelta(days=4), periods=5, freq="D", name="date")
        close = np.full(5, 100.0)
        return pd.DataFrame(
            {
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "adj_close": close,
                "volume": 1_000,
            },
            index=index,
        )


def _ingester(
    database: Database,
    statements: _Statements | None = None,
    prices: _Prices | None = None,
    throttle: float = 0.0,
    sleeps: list[float] | None = None,
) -> BulkIngester:
    return BulkIngester(
        database,
        throttle_seconds=throttle,
        statement_provider=statements or _Statements(),
        price_provider=prices or _Prices(),
        sleeper=(sleeps.append if sleeps is not None else lambda _s: None),
    )


def test_statements_are_ingested_for_every_symbol(database: Database) -> None:
    report = _ingester(database).run(["7203", "8306"], Dataset.STATEMENTS)

    assert report.succeeded == ["7203", "8306"]
    assert report.rows == 2
    with database.session() as session:
        assert FinancialStatementRepository(session).latest_fiscal_year("7203") == 2024


def test_one_symbol_failing_does_not_end_the_run(database: Database) -> None:
    """A delisted code or a momentary 500 must not cost the other 1,599."""
    provider = _Statements(failing="4593")
    report = _ingester(database, statements=provider).run(
        ["7203", "4593", "8306"], Dataset.STATEMENTS
    )

    assert report.succeeded == ["7203", "8306"]
    assert "4593" in report.failed
    assert provider.calls == ["7203", "4593", "8306"]  # kept going


def test_a_rerun_skips_what_is_already_current(database: Database) -> None:
    """Resume is what makes an interrupted backfill cheap to finish."""
    provider = _Statements()
    ingester = _ingester(database, statements=provider)

    ingester.run(["7203", "8306"], Dataset.STATEMENTS)
    provider.calls.clear()
    second = ingester.run(["7203", "8306"], Dataset.STATEMENTS)

    assert second.skipped == ["7203", "8306"]
    assert provider.calls == []  # no requests at all


def test_a_rerun_retries_only_the_failures(database: Database) -> None:
    provider = _Statements(failing="4593")
    ingester = _ingester(database, statements=provider)

    ingester.run(["7203", "4593", "8306"], Dataset.STATEMENTS)
    provider.calls.clear()
    ingester.run(["7203", "4593", "8306"], Dataset.STATEMENTS)

    assert provider.calls == ["4593"]


def test_resume_can_be_turned_off(database: Database) -> None:
    provider = _Statements()
    ingester = _ingester(database, statements=provider)

    ingester.run(["7203"], Dataset.STATEMENTS)
    provider.calls.clear()
    ingester.run(["7203"], Dataset.STATEMENTS, resume=False)

    assert provider.calls == ["7203"]


def test_prices_are_ingested_and_then_skipped(database: Database) -> None:
    provider = _Prices()
    ingester = _ingester(database, prices=provider)

    first = ingester.run(["7203"], Dataset.PRICES)
    assert first.succeeded == ["7203"]
    with database.session() as session:
        assert not PriceRepository(session).get_prices("7203").empty

    provider.calls.clear()
    second = ingester.run(["7203"], Dataset.PRICES)
    assert second.skipped == ["7203"]
    assert provider.calls == []


def test_throttling_pauses_once_per_request(database: Database) -> None:
    """The pause is per request, so skipped symbols cost nothing."""
    sleeps: list[float] = []
    ingester = _ingester(database, throttle=0.25, sleeps=sleeps)

    ingester.run(["7203", "8306"], Dataset.STATEMENTS)
    assert sleeps == [0.25, 0.25]

    sleeps.clear()
    ingester.run(["7203", "8306"], Dataset.STATEMENTS)  # both skipped now
    assert sleeps == []


def test_progress_is_reported_for_every_symbol(database: Database) -> None:
    seen: list[tuple[int, int, str]] = []
    _ingester(database).run(
        ["7203", "8306", "4593"],
        Dataset.STATEMENTS,
        progress=lambda i, t, s: seen.append((i, t, s)),
    )
    assert seen == [(1, 3, "7203"), (2, 3, "8306"), (3, 3, "4593")]


def test_the_report_summarises_the_run(database: Database) -> None:
    report = _ingester(database, statements=_Statements(failing="4593")).run(
        ["7203", "4593"], Dataset.STATEMENTS
    )
    assert report.attempted == 2
    assert "1 ok" in report.summary()
    assert "1 failed" in report.summary()


def test_an_empty_symbol_list_does_nothing(database: Database) -> None:
    report = _ingester(database).run([], Dataset.STATEMENTS)
    assert report.attempted == 0
    assert report.rows == 0


#: Settings are never consulted on the explicit-symbols path.
_NO_SETTINGS = None  # type: ignore[assignment]


# --- a refused listing request ----------------------------------------------


class _StubResponse:
    def __init__(self, status: int, payload: Any) -> None:
        self.status_code = status
        self._payload = payload
        self.text = str(payload)

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _RecordingClient:
    """An httpx.Client stand-in that replays scripted responses."""

    def __init__(self, script: list[_StubResponse]) -> None:
        self._script = script
        self.calls: list[dict[str, str]] = []

    def __call__(self, *_args: Any, **_kwargs: Any) -> _RecordingClient:
        return self

    def __enter__(self) -> _RecordingClient:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None

    def get(self, _url: str, headers: dict[str, str], params: dict[str, str]) -> _StubResponse:
        self.calls.append(dict(params))
        return self._script.pop(0)


def _fetch_with(script: list[_StubResponse], monkeypatch: pytest.MonkeyPatch, **kwargs: Any):
    import httpx

    client = _RecordingClient(script)
    monkeypatch.setattr(httpx, "Client", client)
    from stock_ai.data.universe import _default_fetcher

    return client, _default_fetcher(None, clock=lambda: dt.date(2026, 8, 3), **kwargs)


def test_an_undated_403_is_retried_against_a_delayed_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plan that serves data late refuses today, not the endpoint.

    Giving up on the first 403 reports "not in your plan" for a plan that in
    fact has the endpoint, which is the difference between paying for an
    upgrade and passing a date.
    """
    listing = {"Code": "72030", "MktCd": "0111", "Name": "T", "Sec33Cd": "3700"}
    client, fetch = _fetch_with(
        [
            _StubResponse(403, {"message": "This API is not available in your subscription."}),
            _StubResponse(200, {"data": [listing]}),
        ],
        monkeypatch,
    )

    assert fetch() == [listing]
    # First call undated, second dated 90 days back, in the endpoint's format.
    assert "date" not in client.calls[0]
    assert client.calls[1]["date"] == "20260505"


def test_a_second_403_surfaces_the_services_own_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, fetch = _fetch_with(
        [
            _StubResponse(403, {"message": "first"}),
            _StubResponse(403, {"message": "endpoint not in plan"}),
        ],
        monkeypatch,
    )

    with pytest.raises(DataError) as excinfo:
        fetch()
    # The date is named too: it is what the user would change next.
    assert "endpoint not in plan" in str(excinfo.value)
    assert "2026-05-05" in str(excinfo.value)


def test_an_explicit_date_is_not_second_guessed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A user who passed --as-of gets one request and the real answer."""
    client, fetch = _fetch_with(
        [_StubResponse(403, {"message": "too recent"})],
        monkeypatch,
        as_of=dt.date(2025, 1, 31),
    )

    with pytest.raises(DataError) as excinfo:
        fetch()
    assert client.calls == [{"date": "20250131"}]
    assert "too recent" in str(excinfo.value)


def test_a_non_403_failure_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """500 is the service being unwell; asking for an older date cannot help."""
    client, fetch = _fetch_with([_StubResponse(500, {"message": "boom"})], monkeypatch)

    with pytest.raises(DataError) as excinfo:
        fetch()
    assert len(client.calls) == 1
    assert "500" in str(excinfo.value)


def test_a_body_that_is_not_json_still_produces_a_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, fetch = _fetch_with(
        [
            _StubResponse(403, ValueError("not json")),
            _StubResponse(403, ValueError("not json")),
        ],
        monkeypatch,
    )

    with pytest.raises(DataError) as excinfo:
        fetch()
    assert "403" in str(excinfo.value)


def test_pagination_carries_the_date_on_every_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dated retry must stay dated, or page two is a different snapshot."""
    first = {"Code": "72030", "MktCd": "0111", "Sec33Cd": "3700"}
    second = {"Code": "67580", "MktCd": "0111", "Sec33Cd": "3650"}
    client, fetch = _fetch_with(
        [
            _StubResponse(403, {"message": "delayed"}),
            _StubResponse(200, {"data": [first], "pagination_key": "p2"}),
            _StubResponse(200, {"data": [second]}),
        ],
        monkeypatch,
    )

    assert fetch() == [first, second]
    assert client.calls[2] == {"date": "20260505", "pagination_key": "p2"}


# --- naming symbols when the universe endpoint is unavailable ---------------


def test_explicit_symbols_bypass_the_universe(database: Database) -> None:
    """Being unable to *enumerate* a market must not stop you loading one.

    Listings, prices, and statements are separate J-Quants endpoints on separate
    plan tiers. A 403 on the first says nothing about the other two, so an
    explicit list has to reach the ingester without a universe request.
    """
    from stock_ai.cli import _bulk_symbols

    resolved = _bulk_symbols("prime", "7203, 6758,9984", _NO_SETTINGS, database, None)
    assert resolved == ["7203", "6758", "9984"]


def test_explicit_symbols_accept_spaces_as_separators(database: Database) -> None:
    from stock_ai.cli import _bulk_symbols

    assert _bulk_symbols("prime", "7203 6758", _NO_SETTINGS, database, None) == ["7203", "6758"]


def test_explicit_symbols_honour_the_limit(database: Database) -> None:
    from stock_ai.cli import _bulk_symbols

    assert _bulk_symbols("prime", "7203,6758,9984", _NO_SETTINGS, database, 2) == ["7203", "6758"]


def test_a_symbols_option_with_no_codes_is_rejected(database: Database) -> None:
    """Silently falling back to the segment would fetch 1,600 names by surprise."""
    import typer

    from stock_ai.cli import _bulk_symbols

    with pytest.raises(typer.BadParameter):
        _bulk_symbols("prime", " , , ", _NO_SETTINGS, database, None)


# --- the endpoint and its v2 field names ------------------------------------


def test_the_universe_targets_the_v2_master_endpoint() -> None:
    """v2 renamed the listings endpoint; the v1 path answers 403, not 404.

    J-Quants replies "The requested endpoint does not exist" with a 403 status,
    which reads as a permissions problem and sent this project chasing plan
    tiers for two rounds. Pinning the URL is what stops that recurring.
    """
    from stock_ai.data.universe import _MASTER_URL

    assert _MASTER_URL == "https://api.jquants.com/v2/equities/master"


def test_the_v2_abbreviated_field_names_are_understood() -> None:
    """A v2 record uses Mkt/S33/CoName, not MarketCode/Sector33Code/Name."""
    record = {
        "Code": "72030",
        "Mkt": "0111",
        "MktNm": "プライム",
        "S33": "3700",
        "S33Nm": "輸送用機器",
        "S17": "6",
        "CoName": "トヨタ自動車",
        "CoNameEn": "TOYOTA MOTOR CORPORATION",
    }
    profiles = normalize_listings([record], Segment.PRIME)

    assert len(profiles) == 1
    assert profiles[0].symbol == "7203"
    assert profiles[0].name == "トヨタ自動車"
    assert profiles[0].industry == "輸送用機器"
    assert profiles[0].sector != str(Sector.OTHER)


def test_the_v1_field_names_still_work() -> None:
    """The older spellings stay understood; nothing forces a lockstep upgrade."""
    record = {"Code": "67580", "MktCd": "0111", "Sec33Cd": "3650", "Name": "ソニーG"}
    profiles = normalize_listings([record], Segment.PRIME)

    assert len(profiles) == 1
    assert profiles[0].name == "ソニーG"


def test_records_that_all_fail_to_parse_are_reported(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An empty universe from a full payload is a renamed field, not an empty market.

    Without this the failure is silent: zero listings, exit 0, and every
    downstream step quietly working on nothing.
    """
    with caplog.at_level("WARNING"):
        assert normalize_listings([{"SomethingElse": "x", "Ticker": "7203"}], Segment.ALL) == []

    assert "none produced a usable profile" in caplog.text
    # The keys are the actionable part - they say what to rename.
    assert "SomethingElse" in caplog.text
    assert "Ticker" in caplog.text


def test_a_genuinely_empty_payload_is_not_reported_as_a_field_problem(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING"):
        assert normalize_listings([], Segment.ALL) == []
    assert "none produced a usable profile" not in caplog.text


def test_the_snapshot_date_is_sent_as_yyyymmdd(monkeypatch: pytest.MonkeyPatch) -> None:
    """equities/master takes YYYYMMDD; the ISO form is silently wrong."""
    client, fetch = _fetch_with(
        [_StubResponse(200, {"data": []})],
        monkeypatch,
        as_of=dt.date(2025, 1, 31),
    )

    fetch()
    assert client.calls == [{"date": "20250131"}]
