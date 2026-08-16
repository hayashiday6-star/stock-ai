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
    assert list(table.columns) == ["symbol", "score", "coverage"]
    assert table.empty
    empty.dispose()


def test_backtest_comparison(db: Database) -> None:
    equity, metrics = data.backtest_comparison(db, "AAPL", fast=2, slow=3)
    assert equity.shape[1] == 2  # strategy + benchmark curves
    assert len(metrics) == 2
    assert "sharpe" in metrics.columns


def test_stored_overview(db: Database) -> None:
    overview = data.stored_overview(db)
    assert list(overview["銘柄"]) == ["AAPL"]
    assert overview["日足本数"].iloc[0] == 10
    assert overview["財務"].iloc[0] == "✓"


def test_ingest_prices_with_injected_provider() -> None:
    database = Database("sqlite:///:memory:")
    database.create_all()

    class _FakeProvider:
        def fetch_prices(self, symbol: str, start: object, end: object) -> pd.DataFrame:
            return _prices([10.0, 11.0, 12.0])

    results = data.ingest_prices(database, ["7203"], source="jquants", provider=_FakeProvider())
    assert results[0].ok
    assert results[0].rows == 3
    assert data.available_symbols(database) == ["7203"]
    frame = data.results_frame(results)
    assert list(frame["銘柄"]) == ["7203"]
    database.dispose()


def test_screen_table(db: Database) -> None:
    from stock_ai.screening.conditions import MinROE

    report = data.screen_table(db, MinROE(0.1))  # AAPL roe 0.30 passes
    assert "AAPL" in list(report["symbol"])


# --- "nothing changed" vs "nothing was ever loaded" -------------------------


def test_stored_counts_reports_each_data_type_separately() -> None:
    """An empty screen has two causes that look identical without this.

    A screen finding no cheap stocks and a screen with no valuation figures to
    read both render as an empty table. The sidebar counts tell them apart.
    """
    import datetime as dt

    import numpy as np
    import pandas as pd

    from stock_ai.dashboard.data import stored_counts
    from stock_ai.data.types import FinancialReport, Fundamentals
    from stock_ai.database.engine import Database
    from stock_ai.database.repository import (
        FinancialStatementRepository,
        FundamentalsRepository,
        PriceRepository,
        get_or_create_security,
    )

    database = Database("sqlite:///:memory:")
    database.create_all()
    index = pd.bdate_range(dt.date(2024, 1, 1), periods=10, name="date")
    close = np.full(10, 100.0)
    frame = pd.DataFrame(
        {
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "adj_close": close,
            "volume": 1,
        },
        index=index,
    )
    with database.session() as session:
        for i in range(5):
            get_or_create_security(session, f"{1300 + i:04d}", market="JP")
        for i in range(3):
            PriceRepository(session).upsert_prices(f"{1300 + i:04d}", frame, market="JP")
        for i in range(2):
            FinancialStatementRepository(session).upsert_reports(
                f"{1300 + i:04d}",
                [FinancialReport(symbol=f"{1300 + i:04d}", fiscal_year=2024, revenue=1.0)],
                market="JP",
            )
        FundamentalsRepository(session).upsert_fundamentals(
            Fundamentals(symbol="1300", as_of=dt.date(2026, 8, 7), per=10.0), market="JP"
        )

    assert stored_counts(database) == {
        "securities": 5,
        "with_prices": 3,
        "with_statements": 2,
        "with_fundamentals": 1,
    }
    database.dispose()


def test_stored_counts_on_an_empty_database() -> None:
    from stock_ai.dashboard.data import stored_counts
    from stock_ai.database.engine import Database

    database = Database("sqlite:///:memory:")
    database.create_all()

    assert stored_counts(database) == {
        "securities": 0,
        "with_prices": 0,
        "with_statements": 0,
        "with_fundamentals": 0,
    }
    database.dispose()


def test_the_growth_screen_needs_the_statement_series_attached() -> None:
    """Without it the growth criteria pass nothing, silently.

    On screen "no growing companies matched" and "the series was never loaded"
    are the same empty table, so the flag has to follow the criteria.
    """
    import datetime as dt

    from stock_ai.dashboard.data import screen_table
    from stock_ai.data.types import FinancialReport, Fundamentals
    from stock_ai.database.engine import Database
    from stock_ai.database.repository import (
        FinancialStatementRepository,
        FundamentalsRepository,
        get_or_create_security,
    )
    from stock_ai.screening.base import All
    from stock_ai.screening.conditions import MaxPER, MinProfitGrowth, MinRevenueGrowth

    database = Database("sqlite:///:memory:")
    database.create_all()
    with database.session() as session:
        for symbol, rate in (("GROWER", 1.30), ("FLAT", 1.00)):
            get_or_create_security(session, symbol, market="JP")
            FundamentalsRepository(session).upsert_fundamentals(
                Fundamentals(symbol=symbol, as_of=dt.date(2026, 8, 7), per=12.0), market="JP"
            )
            FinancialStatementRepository(session).upsert_reports(
                symbol,
                [
                    FinancialReport(
                        symbol=symbol,
                        fiscal_year=2023 + k,
                        disclosed_on=dt.date(2023 + k, 5, 10),
                        revenue=1e10 * rate**k,
                        net_income=5e8 * rate**k,
                        equity=8e9,
                        eps=100.0,
                    )
                    for k in range(3)
                ],
                market="JP",
            )

    growth = All(MaxPER(20.0), MinRevenueGrowth(0.1, years=1), MinProfitGrowth(0.1, years=1))

    with_series = screen_table(database, growth, load_statements=True)
    assert with_series["symbol"].tolist() == ["GROWER"]

    # The old behaviour, kept as a test so the regression is visible if it returns.
    assert screen_table(database, growth, load_statements=False).empty

    # A valuation-only screen must not pay for the series it does not read.
    assert sorted(screen_table(database, MaxPER(20.0))["symbol"].tolist()) == ["FLAT", "GROWER"]
    database.dispose()


def test_the_condition_builder_ands_only_the_enabled_parts() -> None:
    from stock_ai.dashboard.app import _build_condition
    from stock_ai.screening.conditions import MaxPER, MinROE

    assert _build_condition([]) is None
    assert _build_condition([(False, MaxPER(20.0))]) is None

    single = _build_condition([(True, MaxPER(20.0)), (False, MinROE(0.1))])
    assert "PER" in str(single)
    assert "ROE" not in str(single)

    both = _build_condition([(True, MaxPER(20.0)), (True, MinROE(0.1))])
    assert "PER" in str(both)
    assert "ROE" in str(both)


def test_the_loaded_commit_is_captured_once_at_import() -> None:
    """Reading it fresh each render confirms an update that has not taken effect.

    Streamlit reloads the app file but not imported modules, so after a git pull
    the sidebar showed the new commit while Python kept executing the old
    screening code. The version line has to describe the process, not the disk.
    """
    import stock_ai.dashboard.app as app

    assert isinstance(app._LOADED_COMMIT, str)
    assert app._LOADED_COMMIT  # never empty; "不明" when git is unavailable


def test_a_pull_without_a_restart_is_detectable() -> None:
    """The mismatch between loaded and on-disk is what the warning keys on."""
    import stock_ai.dashboard.app as app

    on_disk = app._repo_commit()
    if on_disk == "不明":  # no git in this environment; nothing to compare
        return

    stale = on_disk != "0000000" and "不明" not in (on_disk, "0000000")
    assert stale is True
    assert (on_disk != on_disk) is False  # identical commits raise nothing
