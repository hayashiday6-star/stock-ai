"""Tests for the J-Quants TOPIX index provider (injected fetcher, no network)."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from stock_ai.core.exceptions import DataError, NoDataError
from stock_ai.data.jquants_index import fetch_topix, normalize_topix
from stock_ai.data.schema import CLOSE, DATE, HIGH, LOW, OPEN

START = dt.date(2024, 1, 1)
END = dt.date(2024, 1, 31)


def _records() -> list[dict[str, Any]]:
    # Unsorted on purpose, to exercise the sort in normalize_topix.
    return [
        {"Date": "2024-01-11", "O": 2000, "H": 2010, "L": 1990, "C": 2005},
        {"Date": "2024-01-10", "O": 1980, "H": 1995, "L": 1975, "C": 1990},
    ]


def test_normalize_topix_maps_and_sorts() -> None:
    df = normalize_topix(_records())
    assert list(df.index) == sorted(df.index)
    assert df.index.name == DATE
    assert list(df.columns) == [OPEN, HIGH, LOW, CLOSE]
    assert df.loc[df.index[0], CLOSE] == 1990


def test_normalize_topix_empty_raises_no_data() -> None:
    with pytest.raises(NoDataError):
        normalize_topix([])


def test_normalize_topix_missing_fields_raises_data_error() -> None:
    with pytest.raises(DataError):
        normalize_topix([{"Date": "2024-01-10"}])


def test_normalize_topix_dedupes_keeping_last() -> None:
    records = [
        {"Date": "2024-01-10", "O": 1, "H": 1, "L": 1, "C": 100},
        {"Date": "2024-01-10", "O": 1, "H": 1, "L": 1, "C": 200},
    ]
    df = normalize_topix(records)
    assert len(df) == 1
    assert df.iloc[0][CLOSE] == 200


def test_fetch_topix_uses_injected_fetcher() -> None:
    calls: list[tuple[dt.date, dt.date]] = []

    def fetcher(start: dt.date, end: dt.date) -> list[dict[str, Any]]:
        calls.append((start, end))
        return _records()

    df = fetch_topix(START, END, fetcher=fetcher)
    assert calls == [(START, END)]
    assert len(df) == 2


def test_fetch_topix_rejects_inverted_range() -> None:
    with pytest.raises(DataError):
        fetch_topix(END, START, fetcher=lambda s, e: [])


class _FakeResponse:
    """Just enough of an httpx response for raise_for_status and .json()."""

    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload
        self.headers: dict[str, str] = {}
        self.text = str(payload)

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    """Answers the first over-wide ask with the provider's real refusal."""

    #: Verbatim from a run that asked for TOPIX back to 2001 on a plan that
    #: starts in 2021.
    REFUSAL = {
        "message": (
            "Your subscription covers the following dates: 2021-08-26 ~ . "
            "If you want more data, please check other plans:"
            "https://jpx-jquants.com/#dataset"
        )
    }

    def __init__(self, asked: list[tuple[str, str]], **_: Any) -> None:
        self.asked = asked

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *_: object) -> bool:
        return False

    def get(self, _url: str, headers: dict[str, str], params: dict[str, str]) -> _FakeResponse:
        self.asked.append((params["from"], params["to"]))
        if params["from"] < "2021-08-26":
            return _FakeResponse(400, self.REFUSAL)
        return _FakeResponse(
            200,
            {"data": [{"Date": "2021-08-26", "O": 2000, "H": 2010, "L": 1990, "C": 2005}]},
        )


def test_a_range_wider_than_the_plan_is_narrowed_and_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refusal names the covered range, so the second ask uses it.

    Without this the whole study fails on a benchmark that was available for
    every year that mattered - the plan simply starts later than the price
    store does.
    """
    import httpx

    from stock_ai.data.jquants_index import _default_fetcher

    asked: list[tuple[str, str]] = []
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: _FakeClient(asked, **kwargs), raising=True
    )

    records = _default_fetcher(None)(dt.date(2001, 1, 4), dt.date(2026, 8, 24))

    assert asked == [("2001-01-04", "2026-08-24"), ("2021-08-26", "2026-08-24")]
    assert len(records) == 1


def test_a_refusal_that_names_no_window_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a stated plan window justifies asking again."""
    import httpx

    from stock_ai.data.jquants_index import _default_fetcher

    asked: list[tuple[str, str]] = []

    class _AlwaysRefuses(_FakeClient):
        def get(self, _url: str, headers: dict[str, str], params: dict[str, str]):
            self.asked.append((params["from"], params["to"]))
            return _FakeResponse(400, {"message": "Bad Request"})

    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: _AlwaysRefuses(asked, **kwargs), raising=True
    )

    with pytest.raises(DataError):
        _default_fetcher(None)(dt.date(2001, 1, 4), dt.date(2026, 8, 24))
    assert len(asked) == 1


def test_the_plan_window_is_read_off_the_refusal_message() -> None:
    """The narrowing reuses the parser the price provider already ships."""
    from stock_ai.data.jquants_provider import subscription_window

    message = (
        'Provider said: {"message": "Your subscription covers the following '
        'dates: 2021-08-26 ~ . If you want more data, please check other plans"}'
    )
    window = subscription_window(message)
    assert window is not None
    start, end = window
    assert start == dt.date(2021, 8, 26)
    assert end is None
