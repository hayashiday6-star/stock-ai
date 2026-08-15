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


def test_the_api_key_is_sent_every_way_the_service_might_read_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Header-only produced 401 against the live service; the query form is the
    published one.

    Sending only headers looked safer - the key stays out of the URL httpx logs
    - but it broke authentication, and the error text ("invalid subscription
    key") reads like a wrong key rather than a missing one. The leak it avoided
    is handled by the log redactor instead.
    """
    import httpx

    monkeypatch.setattr(httpx, "Client", _FakeClient)

    _default_day_fetcher(SecretStr("edb_secret"))(dt.date(2026, 8, 1))

    assert _FakeClient.last["params"]["Subscription-Key"] == "edb_secret"
    assert _FakeClient.last["headers"]["Ocp-Apim-Subscription-Key"] == "edb_secret"
    assert _FakeClient.last["params"]["date"] == "2026-08-01"


def test_the_key_in_the_url_is_redacted_from_logs() -> None:
    """The query parameter is only acceptable because the log never shows it."""
    from stock_ai.core.logging import redact, register_secret

    key = "edb_live_key_value_1234567890"
    register_secret(key)
    line = (
        "HTTP Request: GET https://api.edinet-fsa.go.jp/api/v2/documents.json"
        f"?date=2026-08-07&type=2&Subscription-Key={key} 'HTTP/1.1 200 OK'"
    )
    cleaned = redact(line)
    assert key not in cleaned
    assert "Subscription-Key=<redacted>" in cleaned


def test_a_401_says_the_value_is_wrong_not_the_placement(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """With the key sent three ways, "cannot find it" is no longer a candidate."""
    import logging

    import httpx

    class _Denied(_FakeClient):
        def get(self, url: str, params: dict[str, str], headers: dict[str, str]) -> _FakeResponse:
            return _FakeResponse(
                {"StatusCode": 401, "message": "Access denied due to invalid subscription key."}
            )

    monkeypatch.setattr(httpx, "Client", _Denied)

    with caplog.at_level(logging.ERROR):
        assert _default_day_fetcher(SecretStr("k"))(dt.date(2026, 8, 7)) == []

    assert "not where it was put" in caplog.text
    assert "EDINET_API_KEY" in caplog.text


def test_an_empty_day_without_metadata_names_the_keys_it_did_get(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A v2 response always carries metadata; its absence means a changed shape."""
    import httpx

    class _NoMetadata(_FakeClient):
        def get(self, url: str, params: dict[str, str], headers: dict[str, str]) -> _FakeResponse:
            return _FakeResponse({"results": [], "unexpectedKey": 1, "another": 2})

    monkeypatch.setattr(httpx, "Client", _NoMetadata)

    with caplog.at_level("WARNING"):
        assert _default_day_fetcher(SecretStr("k"))(dt.date(2026, 8, 1)) == []

    assert "no 'metadata' block" in caplog.text
    assert "unexpectedKey" in caplog.text


def test_a_day_with_a_zero_count_is_not_an_alarm(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A holiday is not a bug; warning about one trains people to ignore warnings."""
    import httpx

    class _Holiday(_FakeClient):
        def get(self, url: str, params: dict[str, str], headers: dict[str, str]) -> _FakeResponse:
            return _FakeResponse({"results": [], "metadata": {"resultset": {"count": 0}}})

    monkeypatch.setattr(httpx, "Client", _Holiday)

    with caplog.at_level("WARNING"):
        assert _default_day_fetcher(SecretStr("k"))(dt.date(2026, 8, 1)) == []

    assert caplog.text == ""


def test_an_error_envelope_is_recognised() -> None:
    """EDINET refuses with HTTP 200 and {"StatusCode": ..., "message": ...}.

    Observed live: three days in a row returned exactly this, and because the
    HTTP status was 200 the monitor reported "Checked 0 new disclosure(s)" -
    a rejected request presented as a quiet week.
    """
    from stock_ai.ir.edinet import error_envelope

    assert error_envelope({"StatusCode": 401, "message": "Unauthorized"}) == (401, "Unauthorized")
    assert error_envelope({"statusCode": 404, "message": "Not Found"}) == (404, "Not Found")
    assert error_envelope({"StatusCode": 500}) == (500, "(no message)")


def test_a_normal_payload_is_not_an_error_envelope() -> None:
    from stock_ai.ir.edinet import error_envelope

    assert error_envelope({"metadata": {"status": "200"}, "results": []}) is None
    assert error_envelope([]) is None
    assert error_envelope(None) is None


def test_a_rejected_day_is_logged_at_error_with_the_reason(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """WARNING alongside "0 disclosures" reads as normal; this is not normal."""
    import logging

    import httpx

    class _Rejected(_FakeClient):
        def get(self, url: str, params: dict[str, str], headers: dict[str, str]) -> _FakeResponse:
            return _FakeResponse({"StatusCode": 401, "message": "Access denied"})

    monkeypatch.setattr(httpx, "Client", _Rejected)

    with caplog.at_level(logging.WARNING):
        assert _default_day_fetcher(SecretStr("k"))(dt.date(2026, 8, 7)) == []

    assert any(record.levelno >= logging.ERROR for record in caplog.records)
    assert "Access denied" in caplog.text
    assert "401" in caplog.text


def _accepting(*placements: str) -> Any:
    """Return a requester that accepts only requests carrying a named placement.

    The probe's whole job is telling placements apart, so the fake has to react
    to *where* the key is rather than to whether one was sent at all.
    """

    def send(params: dict[str, str], headers: dict[str, str]) -> tuple[int, Any]:
        from stock_ai.ir.edinet import BROWSER_PLACEMENT, CURRENT_PLACEMENT

        in_query = "Subscription-Key" in params
        in_ocp = "Ocp-Apim-Subscription-Key" in headers
        if in_query and in_ocp:
            name = CURRENT_PLACEMENT
        elif in_query:
            name = BROWSER_PLACEMENT
        elif in_ocp:
            name = "Ocp-Apim-Subscription-Key ヘッダのみ"
        else:
            name = "Subscription-Key ヘッダのみ"
        if name in placements:
            return 200, {"metadata": {"resultset": {"count": 3}}, "results": [{}, {}, {}]}
        return 200, {"StatusCode": 401, "message": "Access denied due to invalid subscription key."}

    return send


def test_the_probe_tries_every_placement() -> None:
    from stock_ai.ir.edinet import key_placements, probe_key_placements

    results = probe_key_placements(SecretStr("k"), dt.date(2026, 8, 7), _accepting())

    assert [r.placement for r in results] == list(key_placements("k"))
    assert not any(r.accepted for r in results)
    assert all(r.api_status == "401" for r in results)


def test_a_key_valid_only_in_the_browser_form_is_distinguished() -> None:
    """The case a browser test can confirm and a single failed run cannot."""
    from stock_ai.ir.edinet import BROWSER_PLACEMENT, CURRENT_PLACEMENT, probe_key_placements

    results = probe_key_placements(
        SecretStr("k"), dt.date(2026, 8, 7), _accepting(BROWSER_PLACEMENT)
    )
    accepted = {r.placement for r in results if r.accepted}

    assert accepted == {BROWSER_PLACEMENT}
    assert CURRENT_PLACEMENT not in accepted


def test_an_accepted_quiet_day_is_not_reported_as_a_failure() -> None:
    """Zero filings on a holiday is an accepted request, not a refused one."""
    from stock_ai.ir.edinet import probe_key_placements

    def quiet(params: dict[str, str], headers: dict[str, str]) -> tuple[int, Any]:
        return 200, {"metadata": {"resultset": {"count": 0}}, "results": []}

    results = probe_key_placements(SecretStr("k"), dt.date(2026, 8, 9), quiet)

    assert all(r.accepted for r in results)
    assert all(r.documents == 0 for r in results)


def test_a_transport_failure_is_a_result_rather_than_a_crash() -> None:
    """One dead placement must not stop the probe from testing the others."""
    from stock_ai.ir.edinet import BROWSER_PLACEMENT, probe_key_placements

    calls = {"n": 0}

    def flaky(params: dict[str, str], headers: dict[str, str]) -> tuple[int, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("connection timed out")
        return 200, {"metadata": {"resultset": {"count": 1}}, "results": [{}]}

    results = probe_key_placements(SecretStr("k"), dt.date(2026, 8, 7), flaky)

    assert len(results) == 4
    first = results[0]
    assert first.placement == BROWSER_PLACEMENT
    assert first.http_status is None
    assert not first.accepted
    assert "timed out" in first.message
    assert all(r.accepted for r in results[1:])


def test_the_probe_never_returns_the_key() -> None:
    from stock_ai.ir.edinet import probe_key_placements

    results = probe_key_placements(SecretStr("s3cret"), dt.date(2026, 8, 7), _accepting())

    assert not any("s3cret" in f"{r.placement}{r.message}{r.api_status}" for r in results)


def test_the_client_sends_the_placement_it_claims_to() -> None:
    """Guards the probe against drifting away from what the client really does."""
    import httpx

    seen: dict[str, Any] = {}

    class _Recording(_FakeClient):
        def get(self, url: str, params: dict[str, str], headers: dict[str, str]) -> _FakeResponse:
            seen["params"] = params
            seen["headers"] = headers
            return _FakeResponse({"metadata": {"resultset": {"count": 0}}, "results": []})

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(httpx, "Client", _Recording)
        _default_day_fetcher(SecretStr("k"))(dt.date(2026, 8, 7))

    from stock_ai.ir.edinet import CURRENT_PLACEMENT, key_placements

    expected_params, expected_headers = key_placements("k")[CURRENT_PLACEMENT]
    assert seen["headers"] == expected_headers
    assert expected_params.items() <= seen["params"].items()


def test_a_network_failure_is_not_reported_as_a_bad_key() -> None:
    """The misdiagnosis this command exists to prevent, applied to itself.

    Every placement failing in transport says nothing about the key. Telling
    someone to re-issue a working key because their wifi dropped sends them to
    the one place the answer is not.
    """
    from stock_ai.cli import _print_edinet_verdict
    from stock_ai.ir.edinet import probe_key_placements

    def unreachable(params: dict[str, str], headers: dict[str, str]) -> tuple[int, Any]:
        raise OSError("Network is unreachable")

    results = probe_key_placements(SecretStr("k"), dt.date(2026, 8, 7), unreachable)
    assert all(r.http_status is None for r in results)

    from stock_ai.cli import console

    with console.capture() as captured:
        _print_edinet_verdict(results)
    text = captured.get()

    assert "network" in text.lower()
    assert "set-key" not in text


def test_the_key_is_not_sent_in_the_header_the_gateway_ignores() -> None:
    """Measured 401 on a valid key: this spelling buys nothing but leaks more."""
    import httpx

    seen: dict[str, Any] = {}

    class _Recording(_FakeClient):
        def get(self, url: str, params: dict[str, str], headers: dict[str, str]) -> _FakeResponse:
            seen["headers"] = headers
            return _FakeResponse({"metadata": {"resultset": {"count": 0}}, "results": []})

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(httpx, "Client", _Recording)
        _default_day_fetcher(SecretStr("k"))(dt.date(2026, 8, 7))

    assert "Ocp-Apim-Subscription-Key" in seen["headers"]
    assert "Subscription-Key" not in seen["headers"]


def test_the_probe_still_tests_the_placement_the_client_dropped() -> None:
    """A placement that fails is evidence; removing it would lose the diagnosis."""
    from stock_ai.ir.edinet import UNREAD_HEADER_PLACEMENT, key_placements

    assert UNREAD_HEADER_PLACEMENT in key_placements("k")
