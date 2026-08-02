"""Tests for the SQLite persistence layer (in-memory, no network)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator

import pandas as pd
import pytest

from stock_ai.data.schema import CLOSE, DATE, OHLCV_COLUMNS
from stock_ai.database.engine import Database
from stock_ai.database.repository import PriceRepository


@pytest.fixture
def db() -> Iterator[Database]:
    database = Database("sqlite:///:memory:")
    database.create_all()
    yield database
    database.dispose()


def _frame(close_values: list[float]) -> pd.DataFrame:
    dates = pd.to_datetime([f"2024-01-0{i + 2}" for i in range(len(close_values))])
    dates.name = DATE
    return pd.DataFrame(
        {
            "open": close_values,
            "high": [c + 1 for c in close_values],
            "low": [c - 1 for c in close_values],
            "close": close_values,
            "adj_close": close_values,
            "volume": [1000 * (i + 1) for i in range(len(close_values))],
        },
        index=dates,
    )


def test_upsert_and_read_roundtrip(db: Database) -> None:
    with db.session() as s:
        written = PriceRepository(s).upsert_prices("AAPL", _frame([100.0, 101.0, 102.0]))
    assert written == 3

    with db.session() as s:
        out = PriceRepository(s).get_prices("AAPL")
    assert list(out.columns) == OHLCV_COLUMNS
    assert len(out) == 3
    assert out.index.is_monotonic_increasing


def test_upsert_is_idempotent_and_updates(db: Database) -> None:
    with db.session() as s:
        PriceRepository(s).upsert_prices("AAPL", _frame([100.0, 101.0]))
    # Re-ingest the same dates with different closes.
    with db.session() as s:
        PriceRepository(s).upsert_prices("AAPL", _frame([200.0, 201.0]))

    with db.session() as s:
        out = PriceRepository(s).get_prices("AAPL")
    assert len(out) == 2  # no duplicate rows
    assert out[CLOSE].tolist() == [200.0, 201.0]  # values updated


def test_get_prices_date_range_filter(db: Database) -> None:
    with db.session() as s:
        PriceRepository(s).upsert_prices("AAPL", _frame([100.0, 101.0, 102.0, 103.0]))

    with db.session() as s:
        out = PriceRepository(s).get_prices(
            "AAPL", start=dt.date(2024, 1, 3), end=dt.date(2024, 1, 4)
        )
    assert [ts.date() for ts in out.index] == [dt.date(2024, 1, 3), dt.date(2024, 1, 4)]


def test_latest_date(db: Database) -> None:
    with db.session() as s:
        repo = PriceRepository(s)
        assert repo.latest_date("AAPL") is None
        repo.upsert_prices("AAPL", _frame([100.0, 101.0, 102.0]))

    with db.session() as s:
        assert PriceRepository(s).latest_date("AAPL") == dt.date(2024, 1, 4)


def test_empty_frame_writes_nothing(db: Database) -> None:
    with db.session() as s:
        written = PriceRepository(s).upsert_prices("AAPL", pd.DataFrame())
    assert written == 0
