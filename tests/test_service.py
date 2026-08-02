"""Tests for the ingestion service and the ``fetch`` CLI (no network)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator

import pandas as pd
import pytest
from typer.testing import CliRunner

from stock_ai import cli
from stock_ai.core.exceptions import DataError
from stock_ai.data.schema import DATE
from stock_ai.data.service import IngestionService
from stock_ai.database.engine import Database
from stock_ai.database.repository import PriceRepository

runner = CliRunner()


class FakeProvider:
    """Serves a fixed per-symbol frame, sliced to the requested date range."""

    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self.frames = frames
        self.calls: list[tuple[str, dt.date, dt.date]] = []

    def fetch_prices(self, symbol: str, start: dt.date, end: dt.date) -> pd.DataFrame:
        self.calls.append((symbol, start, end))
        frame = self.frames.get(symbol)
        if frame is None:
            raise DataError(f"unknown symbol {symbol}")
        mask = (frame.index >= pd.Timestamp(start)) & (frame.index <= pd.Timestamp(end))
        sub = frame.loc[mask]
        if sub.empty:
            raise DataError("no data in range")
        return sub


def _frame(dates: list[str]) -> pd.DataFrame:
    idx = pd.to_datetime(dates)
    idx.name = DATE
    n = len(dates)
    return pd.DataFrame(
        {
            "open": [100.0 + i for i in range(n)],
            "high": [101.0 + i for i in range(n)],
            "low": [99.0 + i for i in range(n)],
            "close": [100.5 + i for i in range(n)],
            "adj_close": [100.5 + i for i in range(n)],
            "volume": [1000 * (i + 1) for i in range(n)],
        },
        index=idx,
    )


@pytest.fixture
def db() -> Iterator[Database]:
    database = Database("sqlite:///:memory:")
    database.create_all()
    yield database
    database.dispose()


def test_new_symbol_backfills(db: Database) -> None:
    provider = FakeProvider({"AAPL": _frame(["2024-01-02", "2024-01-03", "2024-01-04"])})
    service = IngestionService(provider, db)

    result = service.ingest_symbol("AAPL", end=dt.date(2024, 1, 4))
    assert result.ok
    assert result.rows == 3


def test_second_run_fetches_only_the_delta(db: Database) -> None:
    frame = _frame(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"])
    provider = FakeProvider({"AAPL": frame})
    service = IngestionService(provider, db)

    service.ingest_symbol("AAPL", start=dt.date(2024, 1, 2), end=dt.date(2024, 1, 3))
    provider.calls.clear()

    result = service.ingest_symbol("AAPL", end=dt.date(2024, 1, 5))
    # Incremental start must be the day after the latest stored bar (01-03).
    assert provider.calls[0][1] == dt.date(2024, 1, 4)
    assert result.rows == 2

    with db.session() as s:
        stored = PriceRepository(s).get_prices("AAPL")
    assert len(stored) == 4  # full set, no duplicates


def test_up_to_date_skips_fetch(db: Database) -> None:
    provider = FakeProvider({"AAPL": _frame(["2024-01-02", "2024-01-03"])})
    service = IngestionService(provider, db)

    service.ingest_symbol("AAPL", start=dt.date(2024, 1, 2), end=dt.date(2024, 1, 3))
    provider.calls.clear()

    result = service.ingest_symbol("AAPL", end=dt.date(2024, 1, 3))
    assert result.ok
    assert result.rows == 0
    assert provider.calls == []  # provider never called when already current


def test_batch_continues_past_failure(db: Database) -> None:
    provider = FakeProvider({"AAPL": _frame(["2024-01-02", "2024-01-03"])})
    service = IngestionService(provider, db)

    results = service.ingest_many(["AAPL", "NOPE"], end=dt.date(2024, 1, 3))
    by_symbol = {r.symbol: r for r in results}
    assert by_symbol["AAPL"].ok
    assert not by_symbol["NOPE"].ok
    assert by_symbol["NOPE"].error


def test_fetch_cli_stores_and_reports(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = FakeProvider({"AAPL": _frame(["2024-01-02", "2024-01-03"])})
    monkeypatch.setattr(cli, "Database", lambda: db)
    monkeypatch.setattr(cli, "YFinancePriceProvider", lambda: provider)

    result = runner.invoke(
        cli.app, ["fetch", "AAPL", "--start", "2024-01-02", "--end", "2024-01-03"]
    )
    assert result.exit_code == 0
    assert "ok" in result.stdout

    with db.session() as s:
        assert len(PriceRepository(s).get_prices("AAPL")) == 2


def test_fetch_cli_exits_nonzero_on_failure(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    provider = FakeProvider({})  # every symbol fails
    monkeypatch.setattr(cli, "Database", lambda: db)
    monkeypatch.setattr(cli, "YFinancePriceProvider", lambda: provider)

    result = runner.invoke(
        cli.app, ["fetch", "NOPE", "--start", "2024-01-02", "--end", "2024-01-03"]
    )
    assert result.exit_code == 1
