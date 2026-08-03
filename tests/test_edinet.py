"""Tests for the EDINET disclosure source.

Exercised against recorded EDINET API v2 response *shapes*, not the live
service — this environment has no outbound access to it. What these lock down
is the mapping and filtering logic; a field name drifting upstream would not be
caught here, and would show up as zero disclosures on the first real run.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from pydantic import SecretStr

from stock_ai.ir.edinet import (
    EdinetDisclosureSource,
    _default_day_fetcher,
    normalize_sec_code,
    to_disclosure,
)

_TODAY = dt.date(2026, 8, 2)


def _record(**overrides: Any) -> dict[str, Any]:
    """One EDINET ``results`` entry, shaped like the real payload."""
    base: dict[str, Any] = {
        "seqNumber": 1,
        "docID": "S100EFGH",
        "edinetCode": "E31383",
        "secCode": "45930",
        "JCN": "1010001143536",
        "filerName": "株式会社ヘリオス",
        "docDescription": "臨時報告書",
        "docTypeCode": "180",
        "submitDateTime": "2026-08-02 15:30",
        "withdrawalStatus": "0",
        "disclosureStatus": "0",
        "xbrlFlag": "1",
        "pdfFlag": "1",
    }
    base.update(overrides)
    return base


def _source(
    days: dict[dt.date, list[dict[str, Any]]],
    lookback_days: int = 3,
    calls: list[dt.date] | None = None,
) -> EdinetDisclosureSource:
    def fetcher(day: dt.date) -> list[dict[str, Any]]:
        if calls is not None:
            calls.append(day)
        return days.get(day, [])

    return EdinetDisclosureSource(
        lookback_days=lookback_days, fetcher=fetcher, clock=lambda: _TODAY
    )


# --- securities code handling -----------------------------------------------


def test_symbols_normalize_to_a_four_digit_code() -> None:
    assert normalize_sec_code("7203") == "7203"
    assert normalize_sec_code("7203.T") == "7203"
    assert normalize_sec_code("4593.JP") == "4593"


def test_non_japanese_symbols_have_no_code() -> None:
    """A US ticker must not be matched against EDINET by accident."""
    assert normalize_sec_code("AAPL") is None
    assert normalize_sec_code("720") is None
    assert normalize_sec_code("7203A") is None


def test_the_five_digit_sec_code_matches_a_four_digit_symbol() -> None:
    """EDINET writes Toyota as 72030; matching the raw string finds nothing."""
    source = _source({_TODAY: [_record(secCode="72030", filerName="トヨタ自動車")]})
    assert len(source.fetch("7203")) == 1


def test_filings_without_a_sec_code_are_ignored() -> None:
    """Funds and unlisted filers have no securities code."""
    source = _source({_TODAY: [_record(secCode=None, filerName="某ファンド")]})
    assert source.fetch("4593") == []


def test_another_company_on_the_same_day_is_not_matched() -> None:
    source = _source({_TODAY: [_record(secCode="72030")]})
    assert source.fetch("4593") == []


# --- filtering --------------------------------------------------------------


def test_withdrawn_filings_are_excluded() -> None:
    source = _source({_TODAY: [_record(withdrawalStatus="1")]})
    assert source.fetch("4593") == []


def test_hidden_filings_are_excluded() -> None:
    source = _source({_TODAY: [_record(disclosureStatus="1")]})
    assert source.fetch("4593") == []


def test_a_missing_status_is_treated_as_visible() -> None:
    """Absent flags must not silently drop a real filing."""
    record = _record()
    del record["withdrawalStatus"]
    del record["disclosureStatus"]
    assert len(_source({_TODAY: [record]}).fetch("4593")) == 1


# --- mapping ----------------------------------------------------------------


def test_a_filing_maps_onto_a_disclosure() -> None:
    disclosure = to_disclosure("4593.T", _record())
    assert disclosure.symbol == "4593.T"
    assert disclosure.title == "臨時報告書"
    assert "株式会社ヘリオス" in disclosure.body
    assert disclosure.published_on == dt.date(2026, 8, 2)
    assert disclosure.url is not None
    assert "S100EFGH" in disclosure.url
    assert disclosure.source == "edinet"


def test_a_filing_without_a_description_falls_back_to_its_type() -> None:
    assert to_disclosure("4593", _record(docDescription="", docTypeCode="120")).title == (
        "有価証券報告書"
    )


def test_an_unknown_document_type_keeps_its_code() -> None:
    title = to_disclosure("4593", _record(docDescription="", docTypeCode="999")).title
    assert "999" in title


def test_a_malformed_submit_time_yields_no_date_rather_than_raising() -> None:
    assert to_disclosure("4593", _record(submitDateTime="not a date")).published_on is None
    assert to_disclosure("4593", _record(submitDateTime="")).published_on is None


# --- scanning ---------------------------------------------------------------


def test_results_span_the_lookback_window_newest_first() -> None:
    source = _source(
        {
            _TODAY: [_record(docID="A", submitDateTime="2026-08-02 15:30")],
            _TODAY - dt.timedelta(days=2): [_record(docID="B", submitDateTime="2026-07-31 09:00")],
        }
    )
    items = source.fetch("4593")
    assert [i.published_on for i in items] == [dt.date(2026, 8, 2), dt.date(2026, 7, 31)]


def test_older_filings_outside_the_window_are_not_seen() -> None:
    source = _source(
        {_TODAY - dt.timedelta(days=5): [_record()]},
        lookback_days=3,
    )
    assert source.fetch("4593") == []


def test_the_limit_is_respected() -> None:
    source = _source({_TODAY: [_record(docID=f"D{i}") for i in range(5)]})
    assert len(source.fetch("4593", limit=2)) == 2


def test_day_responses_are_shared_across_symbols() -> None:
    """The API is indexed by date, so a pass must cost days, not days x symbols."""
    calls: list[dt.date] = []
    source = _source({_TODAY: [_record(), _record(secCode="72030")]}, calls=calls)

    source.fetch("4593")
    source.fetch("7203")
    source.fetch("6857")

    assert len(calls) == 3  # three days, once each
    assert len(set(calls)) == 3


def test_a_non_japanese_symbol_costs_no_requests() -> None:
    calls: list[dt.date] = []
    source = _source({_TODAY: [_record()]}, calls=calls)
    assert source.fetch("AAPL") == []
    assert calls == []


def test_a_failing_day_is_survived_and_not_retried_per_symbol() -> None:
    """One bad day must not cost a request for every watched name."""
    calls: list[dt.date] = []

    def fetcher(day: dt.date) -> list[dict[str, Any]]:
        calls.append(day)
        if day == _TODAY:
            raise RuntimeError("HTTP 403")
        return [_record(submitDateTime="2026-08-01 10:00")]

    source = EdinetDisclosureSource(lookback_days=2, fetcher=fetcher, clock=lambda: _TODAY)

    first = source.fetch("4593")
    source.fetch("7203")

    assert len(first) == 1  # the good day still came through
    assert len(calls) == 2  # the failed day was cached as empty, not retried


def test_clearing_the_cache_forces_a_refetch() -> None:
    calls: list[dt.date] = []
    source = _source({_TODAY: [_record()]}, calls=calls)

    source.fetch("4593")
    source.clear_cache()
    source.fetch("4593")

    assert len(calls) == 6  # three days, twice


def test_lookback_is_at_least_one_day() -> None:
    source = _source({_TODAY: [_record()]}, lookback_days=0)
    assert len(source.fetch("4593")) == 1


@pytest.mark.parametrize("code", ["120", "140", "180", "350"])
def test_the_common_filing_types_are_labelled(code: str) -> None:
    title = to_disclosure("4593", _record(docDescription="", docTypeCode=code)).title
    assert title and not title.startswith("EDINET書類")


# --- the live request -------------------------------------------------------


class _FakeResponse:
    status_code = 200

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    """Records the one GET made, so the request itself can be asserted on."""

    last: dict[str, Any] = {}

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None

    def get(self, url: str, params: dict[str, str], headers: dict[str, str]) -> _FakeResponse:
        _FakeClient.last = {"url": url, "params": params, "headers": headers}
        return _FakeResponse({"results": [_record()]})


def test_the_api_key_is_sent_both_ways(monkeypatch: pytest.MonkeyPatch) -> None:
    """Query parameter and header both carry the key.

    Whichever one EDINET's gateway is actually checking, rejecting a request for
    the other reason costs a silent HTTP 200 with an empty body — the failure
    that reads as 'nothing was filed'.
    """
    import httpx

    monkeypatch.setattr(httpx, "Client", _FakeClient)

    records = _default_day_fetcher(SecretStr("k3y"))(dt.date(2026, 8, 1))

    assert len(records) == 1
    assert _FakeClient.last["params"]["Subscription-Key"] == "k3y"
    assert _FakeClient.last["headers"]["Ocp-Apim-Subscription-Key"] == "k3y"
    assert _FakeClient.last["params"]["date"] == "2026-08-01"


def test_no_api_key_sends_neither_carrier(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    monkeypatch.setattr(httpx, "Client", _FakeClient)

    _default_day_fetcher(None)(dt.date(2026, 8, 1))

    assert "Subscription-Key" not in _FakeClient.last["params"]
    assert _FakeClient.last["headers"] == {}


def test_a_keyless_empty_day_names_the_missing_key(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Zero documents and no key is the one empty-day case with a known cause."""
    import httpx

    class _EmptyClient(_FakeClient):
        def get(self, url: str, params: dict[str, str], headers: dict[str, str]) -> _FakeResponse:
            return _FakeResponse({"results": []})

    monkeypatch.setattr(httpx, "Client", _EmptyClient)

    with caplog.at_level("WARNING"):
        assert _default_day_fetcher(None)(dt.date(2026, 8, 1)) == []

    assert "EDINET_API_KEY" in caplog.text
