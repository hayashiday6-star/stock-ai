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


# --- 取得元をまたいで同じ決算期を書くとき ---------------------------------


def test_a_blank_does_not_erase_a_stored_value() -> None:
    """取得元によって埋まる列が違う。空で上書きすると、無言で消える。

    J-Quants は EPS・BPS・1株配当・営業利益を持ち、EDINET の「主要な経営指標等」は
    どれも持たない。JP_STATEMENT_SOURCE を切り替えただけで配当履歴が消えると、
    配当の画面は例外を出さずに何も返さなくなる。実測値は日立の2026年3月期。
    """
    import datetime as dt

    from stock_ai.data.types import FinancialReport
    from stock_ai.database.engine import Database
    from stock_ai.database.repository import FinancialStatementRepository, get_or_create_security

    database = Database("sqlite:///:memory:")
    database.create_all()
    with database.session() as session:
        get_or_create_security(session, "6501", market="JP")
        repo = FinancialStatementRepository(session)
        repo.upsert_reports(
            "6501",
            [
                FinancialReport(
                    symbol="6501",
                    fiscal_year=2026,
                    disclosed_on=dt.date(2026, 4, 27),
                    revenue=10_586_781_000_000.0,
                    operating_income=1_199_275_000_000.0,
                    net_income=802_368_000_000.0,
                    equity=6_772_607_000_000.0,
                    eps=176.76,
                    bps=1459.71,
                    dividend_per_share=50.0,
                )
            ],
            market="JP",
        )
        # EDINET から同じ期を入れ直す。埋まるのは4項目だけ。
        repo.upsert_reports(
            "6501",
            [
                FinancialReport(
                    symbol="6501",
                    fiscal_year=2026,
                    revenue=10_586_781_000_000.0,
                    net_income=802_368_000_000.0,
                    equity=6_568_369_000_000.0,
                    shares_outstanding=4_535_560_000.0,
                )
            ],
            market="JP",
        )
        (stored,) = repo.get_reports("6501")

    # EDINET が持っている項目は更新される。自己資本の定義差 3.11% がここに出る。
    assert stored.equity == 6_568_369_000_000.0
    assert stored.shares_outstanding == 4_535_560_000.0
    # 持っていない項目は残る。
    assert stored.eps == 176.76
    assert stored.bps == 1459.71
    assert stored.dividend_per_share == 50.0
    assert stored.operating_income == 1_199_275_000_000.0
    assert stored.disclosed_on == dt.date(2026, 4, 27)
    database.dispose()


def test_a_restatement_still_overwrites() -> None:
    """値が入ってくるなら上書きする。訂正が反映されないほうが困る。"""
    from stock_ai.data.types import FinancialReport
    from stock_ai.database.engine import Database
    from stock_ai.database.repository import FinancialStatementRepository, get_or_create_security

    database = Database("sqlite:///:memory:")
    database.create_all()
    with database.session() as session:
        get_or_create_security(session, "6501", market="JP")
        repo = FinancialStatementRepository(session)
        repo.upsert_reports(
            "6501", [FinancialReport(symbol="6501", fiscal_year=2026, revenue=1.0)], market="JP"
        )
        repo.upsert_reports(
            "6501", [FinancialReport(symbol="6501", fiscal_year=2026, revenue=2.0)], market="JP"
        )
        (stored,) = repo.get_reports("6501")

    assert stored.revenue == 2.0
    database.dispose()
