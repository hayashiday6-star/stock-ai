"""Tests for the dashboard data layer (no Streamlit, no network)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator

import pandas as pd
import pytest

from stock_ai.dashboard import data
from stock_ai.data.schema import ADJ_CLOSE, CLOSE, HIGH, LOW, OPEN, VOLUME
from stock_ai.data.types import Fundamentals
from stock_ai.database.engine import Database
from stock_ai.database.repository import FundamentalsRepository, PriceRepository

_TODAY = dt.date(2024, 6, 30)


def _prices(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="D", name="date")
    return pd.DataFrame(
        {
            OPEN: closes,
            HIGH: [c + 1 for c in closes],
            LOW: [c - 1 for c in closes],
            CLOSE: closes,
            ADJ_CLOSE: closes,
            VOLUME: [1000] * len(closes),
        },
        index=idx,
    )


@pytest.fixture
def db() -> Iterator[Database]:
    database = Database("sqlite:///:memory:")
    database.create_all()
    closes = [100.0 + i for i in range(10)]
    with database.session() as s:
        FundamentalsRepository(s).upsert_fundamentals(
            Fundamentals(symbol="AAPL", as_of=_TODAY, roe=0.30, per=10.0)
        )
        PriceRepository(s).upsert_prices("AAPL", _prices(closes))
    yield database
    database.dispose()


def test_available_symbols(db: Database) -> None:
    assert data.available_symbols(db) == ["AAPL"]


def test_load_prices(db: Database) -> None:
    prices = data.load_prices(db, "AAPL")
    assert len(prices) == 10
    assert CLOSE in prices.columns


def test_score_table(db: Database) -> None:
    table = data.score_table(db, ["AAPL"])
    assert list(table["symbol"]) == ["AAPL"]
    assert "score" in table.columns
    assert table["score"].iloc[0] > 0


def test_score_table_empty() -> None:
    empty = Database("sqlite:///:memory:")
    empty.create_all()
    table = data.score_table(empty, [])
    assert list(table.columns) == ["symbol", "score"]
    assert table.empty
    empty.dispose()


def test_backtest_comparison(db: Database) -> None:
    equity, metrics = data.backtest_comparison(db, "AAPL", fast=2, slow=3)
    assert equity.shape[1] == 2  # strategy + benchmark curves
    assert len(metrics) == 2
    assert "sharpe" in metrics.columns
