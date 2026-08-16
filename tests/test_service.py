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


# --- backfilling history ----------------------------------------------------


def _stored_symbol(database: Database, symbol: str, dates: list[str]) -> None:
    with database.session() as session:
        PriceRepository(session).upsert_prices(symbol, _frame(dates), market="JP")


def test_a_lookback_reaching_past_the_oldest_bar_backfills_history() -> None:
    """A request for more history must not be answered incrementally.

    Observed live: a universe already holding four years was asked for 5,000
    days. Every symbol resolved to "the day after the latest bar", every symbol
    reported success, and not one extra year arrived. Nothing raised, so the
    run looked exactly like a run that had worked.
    """
    database = Database("sqlite:///:memory:")
    database.create_all()
    _stored_symbol(database, "7203", ["2024-01-01", "2024-01-02", "2024-01-03"])

    provider = FakeProvider({"7203": _frame(["2020-01-01", "2024-01-04"])})
    service = IngestionService(provider, database, default_lookback_days=3000, backfill=True)
    result = service.ingest_symbol("7203", end=dt.date(2024, 1, 10), market="JP")

    assert result.ok
    _symbol, start, _end = provider.calls[0]
    assert start < dt.date(2024, 1, 1)  # reaches behind the oldest stored bar
    database.dispose()


def test_backfill_is_opt_in_so_a_nightly_run_stays_incremental() -> None:
    """Inferring the backfill would re-fetch a year for every new symbol nightly."""
    database = Database("sqlite:///:memory:")
    database.create_all()
    _stored_symbol(database, "7203", ["2024-01-01", "2024-01-02", "2024-01-03"])

    provider = FakeProvider({"7203": _frame(["2024-01-04"])})
    service = IngestionService(provider, database, default_lookback_days=365)
    service.ingest_symbol("7203", end=dt.date(2024, 1, 10), market="JP")

    _symbol, start, _end = provider.calls[0]
    assert start == dt.date(2024, 1, 4)  # the day after the latest stored bar
    database.dispose()


def test_backfilling_keeps_the_bars_already_stored() -> None:
    """The overlap is deduplicated by the upsert, not dropped."""
    database = Database("sqlite:///:memory:")
    database.create_all()
    _stored_symbol(database, "7203", ["2024-01-02", "2024-01-03"])

    provider = FakeProvider({"7203": _frame(["2020-01-01", "2024-01-02", "2024-01-03"])})
    service = IngestionService(provider, database, default_lookback_days=3000, backfill=True)
    service.ingest_symbol("7203", end=dt.date(2024, 1, 10), market="JP")

    with database.session() as session:
        repo = PriceRepository(session)
        assert repo.earliest_date("7203") == dt.date(2020, 1, 1)
        assert repo.latest_date("7203") == dt.date(2024, 1, 3)
        assert len(repo.get_prices("7203")) == 3
    database.dispose()


# --- history reporting ------------------------------------------------------


def test_history_spans_reports_the_range_of_each_series() -> None:
    from stock_ai.database.repository import price_history_spans

    database = Database("sqlite:///:memory:")
    database.create_all()
    _stored_symbol(database, "7203", ["2020-01-01", "2022-06-01", "2024-01-03"])
    _stored_symbol(database, "6758", ["2023-01-01", "2023-01-02"])

    with database.session() as session:
        spans = {row[0]: row for row in price_history_spans(session)}

    assert spans["7203"][2] == dt.date(2020, 1, 1)
    assert spans["7203"][3] == dt.date(2024, 1, 3)
    assert spans["7203"][4] == 3
    assert spans["6758"][4] == 2
    database.dispose()


def test_a_symbol_without_bars_is_not_reported_as_zero_length() -> None:
    """A security row with no prices has no span, and must not fake one."""
    from stock_ai.database.repository import get_or_create_security, price_history_spans

    database = Database("sqlite:///:memory:")
    database.create_all()
    with database.session() as session:
        get_or_create_security(session, "EMPTY", market="JP")
    with database.session() as session:
        assert price_history_spans(session) == []
    database.dispose()


def test_the_history_command_does_not_blame_a_shared_floor_on_the_provider() -> None:
    """A shared floor is ambiguous, and saying otherwise closes the question wrongly.

    The first real run reported a floor of 2022-06-27 as "the provider's history
    limit". It was exactly 1,500 days before the day the universe was first
    loaded with ``--lookback 1500`` - our own boundary, not the provider's.
    """
    database = Database("sqlite:///:memory:")
    database.create_all()
    for symbol in ("1001", "1002", "1003", "1004"):
        _stored_symbol(database, symbol, ["2021-04-01", "2024-01-02"])

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(cli, "Database", lambda: database)
        patch.setenv("COLUMNS", "200")
        result = runner.invoke(cli.app, ["history"])

    assert result.exit_code == 0
    assert "2021-04-01" in result.stdout
    assert "either the provider" in result.stdout
    assert "--lookback first loaded them" in result.stdout
    database.dispose()
