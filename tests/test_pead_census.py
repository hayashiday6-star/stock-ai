"""事前登録を書く前の下調べが、正しく数えているかどうか。

このセンサスが間違うと、事前登録が空振りするかどうかの判断そのものが
間違う。前回の失敗（現象とデータが交わっていなかったのに気付かず登録を
書き上げた）を繰り返さないための計測なので、計測自体を固定しておく。
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from stock_ai.backtest.pead_census import (
    DRIFT_WINDOW,
    ENTRY_OFFSET,
    TURNOVER_WINDOW,
    run_census,
)
from stock_ai.data.schema import ADJ_CLOSE, CLOSE, HIGH, LOW, OPEN, VOLUME
from stock_ai.data.types import FinancialReport
from stock_ai.database.engine import Database
from stock_ai.database.repository import FinancialStatementRepository, PriceRepository

_SESSIONS = 200


def _frame(bars: int = _SESSIONS, *, close: float = 1_000.0, volume: float = 10_000.0):
    index = pd.bdate_range("2024-01-01", periods=bars, name="date")
    return pd.DataFrame(
        {
            OPEN: close,
            HIGH: close,
            LOW: close,
            CLOSE: close,
            ADJ_CLOSE: close,
            VOLUME: volume,
        },
        index=index,
    )


def _database(frame: pd.DataFrame, disclosed_on: dt.date, symbol: str = "7203") -> Database:
    database = Database("sqlite:///:memory:")
    database.create_all()
    with database.session() as session:
        PriceRepository(session).upsert_prices(symbol, frame, market="JP")
        FinancialStatementRepository(session).upsert_reports(
            symbol,
            [
                FinancialReport(
                    symbol=symbol, fiscal_year=disclosed_on.year, disclosed_on=disclosed_on
                )
            ],
            market="JP",
        )
    return database


def test_a_disclosure_with_room_on_both_sides_is_measurable() -> None:
    frame = _frame()
    disclosed = frame.index[100].date()

    report = run_census(_database(frame, disclosed))

    assert len(report.disclosures) == 1
    assert report.disclosures[0].measurable
    assert len(report.measurable()) == 1


def test_a_disclosure_too_close_to_the_end_has_no_exit_bar() -> None:
    """窓が取れない開示を件数に数えると、検証できる件数を過大に見積もる。

    前回はこの過大評価を、事前登録を書き終えるまで発見できなかった。
    """
    frame = _frame()
    # 出口は D+1+60。最後から数えて60本しか残っていない日を開示日にする。
    disclosed = frame.index[-DRIFT_WINDOW].date()

    report = run_census(_database(frame, disclosed))

    assert len(report.disclosures) == 1
    assert report.disclosures[0].has_entry_bar
    assert not report.disclosures[0].has_exit_bar
    assert report.measurable() == []


def test_the_last_measurable_bar_is_exactly_entry_plus_drift() -> None:
    """境界そのものを固定する。off-by-one は件数を静かにずらす。"""
    frame = _frame()
    last_usable = frame.index[-(ENTRY_OFFSET + DRIFT_WINDOW) - 1].date()
    one_too_late = frame.index[-(ENTRY_OFFSET + DRIFT_WINDOW)].date()

    assert run_census(_database(frame, last_usable)).disclosures[0].measurable
    assert not run_census(_database(frame, one_too_late)).disclosures[0].measurable


def test_turnover_is_measured_before_the_disclosure_never_on_it() -> None:
    """開示当日の出来高は売買代金に入れない。

    前回、判定日当日で条件付けした流動性フィルタが1件も除外せず、
    「効いているつもりで何もしていない」状態だった。同じ形を持ち込まない。
    """
    frame = _frame(volume=10_000.0)
    position = 100
    # 開示当日だけ出来高を跳ねさせる。20日平均に混ざれば数字が動く。
    frame.loc[frame.index[position], VOLUME] = 10_000_000.0
    disclosed = frame.index[position].date()

    report = run_census(_database(frame, disclosed))

    # 1,000円 x 10,000株 = 1,000万円。当日が混ざればこの値にならない。
    assert report.disclosures[0].turnover_20d == 10_000_000.0


def test_turnover_needs_a_full_window_before_the_disclosure() -> None:
    frame = _frame()
    disclosed = frame.index[TURNOVER_WINDOW - 1].date()

    report = run_census(_database(frame, disclosed))

    assert report.disclosures[0].turnover_20d is None
    assert report.disclosures[0].band() is None


def test_a_row_without_a_disclosure_date_is_counted_but_not_used() -> None:
    """開示日が無い行は「開示が無かった」ではなく「いつか分からない」。"""
    database = Database("sqlite:///:memory:")
    database.create_all()
    with database.session() as session:
        PriceRepository(session).upsert_prices("7203", _frame(), market="JP")
        FinancialStatementRepository(session).upsert_reports(
            "7203",
            [FinancialReport(symbol="7203", fiscal_year=2024, disclosed_on=None)],
            market="JP",
        )

    report = run_census(database)

    assert report.rows_total == 1
    assert report.rows_without_disclosed_on == 1
    assert report.disclosures == []


def test_bands_are_exclusive_so_the_shares_sum_to_one() -> None:
    """帯が累積だと「1億円以上」を読み違える。"""
    frame = _frame()
    disclosed = frame.index[100].date()
    report = run_census(_database(frame, disclosed))

    assert sum(count for _edge, count in report.by_band()) == len(report.disclosures)


def test_same_day_counts_group_two_symbols_disclosing_together() -> None:
    frame = _frame()
    disclosed = frame.index[100].date()
    database = _database(frame, disclosed, symbol="7203")
    with database.session() as session:
        PriceRepository(session).upsert_prices("6758", frame, market="JP")
        FinancialStatementRepository(session).upsert_reports(
            "6758",
            [FinancialReport(symbol="6758", fiscal_year=2024, disclosed_on=disclosed)],
            market="JP",
        )

    report = run_census(database)

    assert report.same_day_counts()[disclosed] == 2


def test_us_securities_are_not_censused() -> None:
    frame = _frame()
    disclosed = frame.index[100].date()
    database = _database(frame, disclosed)
    with database.session() as session:
        PriceRepository(session).upsert_prices("AAPL", frame, market="US")

    report = run_census(database)

    assert report.symbols_scanned == 1
