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
