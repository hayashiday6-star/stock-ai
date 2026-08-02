"""Tests for fundamentals: provider normalization, persistence, service, CLI."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator

import pytest
from typer.testing import CliRunner

from stock_ai import cli
from stock_ai.core.exceptions import DataError
from stock_ai.data.service import FundamentalsService
from stock_ai.data.yfinance_provider import YFinanceFundamentalsProvider
from stock_ai.database.engine import Database
from stock_ai.database.repository import FundamentalsRepository

runner = CliRunner()

_FIXED_DAY = dt.date(2024, 6, 30)

_RAW_INFO = {
    "trailingPE": 28.5,
    "priceToBook": 45.0,
    "returnOnEquity": 1.47,
    "totalRevenue": 383_000_000_000,
    "netIncomeToCommon": 97_000_000_000,
    "dividendYield": 0.0044,
    "marketCap": 3_000_000_000_000,
}


def _provider(info: dict[str, object]) -> YFinanceFundamentalsProvider:
    return YFinanceFundamentalsProvider(info_fetcher=lambda _s: info, clock=lambda: _FIXED_DAY)


@pytest.fixture
def db() -> Iterator[Database]:
    database = Database("sqlite:///:memory:")
    database.create_all()
    yield database
    database.dispose()


def test_provider_maps_all_fields() -> None:
    result = _provider(_RAW_INFO).fetch_fundamentals("AAPL")
    assert result.symbol == "AAPL"
    assert result.as_of == _FIXED_DAY
    assert result.per == 28.5
    assert result.market_cap == 3_000_000_000_000


def test_provider_tolerates_missing_and_junk_values() -> None:
    result = _provider({"trailingPE": None, "priceToBook": "n/a"}).fetch_fundamentals("X")
    assert result.per is None
    assert result.pbr is None
    assert result.roe is None  # entirely absent from the info dict


def test_provider_empty_info_raises() -> None:
    with pytest.raises(DataError):
        _provider({}).fetch_fundamentals("AAPL")


def test_repository_roundtrip(db: Database) -> None:
    snapshot = _provider(_RAW_INFO).fetch_fundamentals("AAPL")
    with db.session() as s:
        FundamentalsRepository(s).upsert_fundamentals(snapshot)

    with db.session() as s:
        loaded = FundamentalsRepository(s).get_latest("AAPL")
    assert loaded is not None
    assert loaded.per == 28.5
    assert loaded.as_of == _FIXED_DAY


def test_repository_upsert_is_idempotent(db: Database) -> None:
    first = _provider(_RAW_INFO).fetch_fundamentals("AAPL")
    updated = _provider({**_RAW_INFO, "trailingPE": 30.0}).fetch_fundamentals("AAPL")
    with db.session() as s:
        repo = FundamentalsRepository(s)
        repo.upsert_fundamentals(first)
        repo.upsert_fundamentals(updated)  # same (symbol, as_of)

    with db.session() as s:
        loaded = FundamentalsRepository(s).get_latest("AAPL")
    assert loaded is not None
    assert loaded.per == 30.0  # updated in place, not duplicated


def test_get_latest_absent_returns_none(db: Database) -> None:
    with db.session() as s:
        assert FundamentalsRepository(s).get_latest("AAPL") is None


def test_service_batch_continues_past_failure(db: Database) -> None:
    provider = YFinanceFundamentalsProvider(
        info_fetcher=lambda s: _RAW_INFO if s == "AAPL" else {},
        clock=lambda: _FIXED_DAY,
    )
    results = FundamentalsService(provider, db).ingest_many(["AAPL", "NOPE"])
    by_symbol = {r.symbol: r for r in results}
    assert by_symbol["AAPL"].ok
    assert not by_symbol["NOPE"].ok


def test_fundamentals_cli(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "Database", lambda: db)
    monkeypatch.setattr(cli, "YFinanceFundamentalsProvider", lambda: _provider(_RAW_INFO))

    result = runner.invoke(cli.app, ["fundamentals", "AAPL"])
    assert result.exit_code == 0

    with db.session() as s:
        assert FundamentalsRepository(s).get_latest("AAPL") is not None
