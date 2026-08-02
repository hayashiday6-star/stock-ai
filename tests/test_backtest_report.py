"""Tests for the backtest comparison table and the ``backtest`` CLI."""

from __future__ import annotations

from collections.abc import Iterator

import pandas as pd
import pytest
from typer.testing import CliRunner

from stock_ai import cli
from stock_ai.backtest.engine import BacktestEngine
from stock_ai.backtest.report import metrics_frame
from stock_ai.backtest.strategy import BuyAndHold
from stock_ai.data.schema import CLOSE, HIGH, LOW, OPEN, VOLUME
from stock_ai.database.engine import Database
from stock_ai.database.repository import PriceRepository

runner = CliRunner()


def _prices(n: int = 30) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="D", name="date")
    closes = [100.0 + i for i in range(n)]
    return pd.DataFrame(
        {
            OPEN: closes,
            HIGH: [c + 1 for c in closes],
            LOW: [c - 1 for c in closes],
            CLOSE: closes,
            "adj_close": closes,
            VOLUME: [1000] * n,
        },
        index=idx,
    )


def test_metrics_frame_has_row_per_strategy() -> None:
    prices = _prices()
    engine = BacktestEngine()
    result = engine.run(prices, BuyAndHold().generate_signals(prices))
    frame = metrics_frame({"a": result, "b": result})
    assert list(frame["strategy"]) == ["a", "b"]
    assert "sharpe" in frame.columns
    assert len(frame) == 2


@pytest.fixture
def db() -> Iterator[Database]:
    database = Database("sqlite:///:memory:")
    database.create_all()
    with database.session() as s:
        PriceRepository(s).upsert_prices("AAPL", _prices(40))
    yield database
    database.dispose()


def test_backtest_cli_runs(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "Database", lambda: db)
    monkeypatch.setenv("COLUMNS", "200")  # avoid Rich truncating the strategy column
    result = runner.invoke(
        cli.app, ["backtest", "AAPL", "--strategy", "sma", "--fast", "3", "--slow", "5"]
    )
    assert result.exit_code == 0
    assert "buy&hold" in result.stdout
    assert "sma_crossover_3_5" in result.stdout


def test_backtest_cli_missing_symbol_errors(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "Database", lambda: db)
    result = runner.invoke(cli.app, ["backtest", "MSFT"])
    assert result.exit_code != 0


def test_backtest_cli_unknown_strategy_errors(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "Database", lambda: db)
    result = runner.invoke(cli.app, ["backtest", "AAPL", "--strategy", "bogus"])
    assert result.exit_code != 0
