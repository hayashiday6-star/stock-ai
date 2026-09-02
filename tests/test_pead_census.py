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
    Disclosure,
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


# --- 開示のタイミング ---------------------------------------------------------


def _with_time(disclosed_at: dt.time | None, on_date: dt.date) -> Disclosure:
    return Disclosure(
        symbol="7203",
        disclosed_on=on_date,
        fiscal_year=on_date.year,
        period="FY",
        turnover_20d=None,
        has_entry_bar=True,
        has_exit_bar=True,
        disclosed_at=disclosed_at,
    )


def test_a_disclosure_before_the_close_is_intraday() -> None:
    """実データで見た 14:00 の開示は場中。当日の値動きにニュースが入っている。"""
    assert _with_time(dt.time(14, 0), dt.date(2024, 5, 10)).timing() == "場中"


def test_a_disclosure_after_the_close_is_after_hours() -> None:
    assert _with_time(dt.time(15, 30), dt.date(2024, 5, 10)).timing() == "引け後"


def test_the_boundary_leans_to_intraday_before_the_session_was_extended() -> None:
    """15:00 ちょうどは引け後に倒す。

    誤って引け後にした場中開示は、当日に織り込まれた反応をドリフトとして
    数えてしまう。逆向きの誤りより高くつくので、境界は保守側に置く。
    """
    assert _with_time(dt.time(15, 0), dt.date(2024, 5, 10)).timing() == "引け後"


def test_the_extended_session_is_counted_separately() -> None:
    """延長後の 15:00-15:30 は当日中だが残り時間が短い。混ぜない。"""
    late = _with_time(dt.time(15, 10), dt.date(2025, 5, 10))
    assert late.timing() == "延長後の場中（15:00-15:30）"
    # 延長前の同じ時刻は引け後。
    assert _with_time(dt.time(15, 10), dt.date(2024, 5, 10)).timing() == "引け後"


def test_a_missing_time_is_unknown_not_after_hours() -> None:
    """時刻なしを引け後に倒すと、取り込み漏れが黙って結論に混ざる。"""
    assert _with_time(None, dt.date(2024, 5, 10)).timing() == "時刻なし"


def test_the_census_carries_the_stored_disclosure_time() -> None:
    frame = _frame()
    disclosed = frame.index[100].date()
    database = Database("sqlite:///:memory:")
    database.create_all()
    with database.session() as session:
        PriceRepository(session).upsert_prices("7203", frame, market="JP")
        FinancialStatementRepository(session).upsert_reports(
            "7203",
            [
                FinancialReport(
                    symbol="7203",
                    fiscal_year=2024,
                    disclosed_on=disclosed,
                    disclosed_at=dt.time(15, 30),
                )
            ],
            market="JP",
        )

    report = run_census(database)

    assert report.disclosures[0].disclosed_at == dt.time(15, 30)
    assert report.timing_counts()["引け後"] == 1


def test_slots_per_fiscal_year_shows_how_many_quarters_are_on_file() -> None:
    """「1銘柄あたり年3件」の理由は、この分布を見るまで分からない。

    四半期が落ちているのか、四半期開示をしない銘柄が混ざっているのか、
    年別の件数だけでは区別できない。
    """
    frame = _frame()
    database = Database("sqlite:///:memory:")
    database.create_all()
    with database.session() as session:
        PriceRepository(session).upsert_prices("7203", frame, market="JP")
        PriceRepository(session).upsert_prices("6758", frame, market="JP")
        # 7203 は4四半期そろい、6758 は2つだけ。
        FinancialStatementRepository(session).upsert_reports(
            "7203",
            [
                FinancialReport(
                    symbol="7203",
                    fiscal_year=2024,
                    period=period,
                    disclosed_on=frame.index[60 + offset].date(),
                )
                for offset, period in enumerate(("Q1", "Q2", "Q3", "FY"))
            ],
            market="JP",
        )
        FinancialStatementRepository(session).upsert_reports(
            "6758",
            [
                FinancialReport(
                    symbol="6758",
                    fiscal_year=2024,
                    period=period,
                    disclosed_on=frame.index[60 + offset].date(),
                )
                for offset, period in enumerate(("Q2", "FY"))
            ],
            market="JP",
        )

    slots = run_census(database).slots_per_fiscal_year()

    assert slots[4] == 1  # 7203
    assert slots[2] == 1  # 6758


def test_a_disclosure_before_the_price_history_is_not_measurable() -> None:
    """価格が始まる前の開示には D が無い。

    searchsorted は範囲外に 0 を返すので、弾かないと最初のバーを D と見なして
    測定可能に数えてしまう。2021年の開示を2025年の株価で測ることになり、
    件数も反応も別物になる。例外は出ないので、数字を検算するまで気付けない。
    """
    frame = _frame()
    before = (frame.index[0] - pd.Timedelta(days=365)).date()

    report = run_census(_database(frame, before))

    assert len(report.disclosures) == 1
    assert not report.disclosures[0].has_entry_bar
    assert not report.disclosures[0].has_exit_bar
    assert report.measurable() == []


def test_a_disclosure_on_the_first_bar_is_still_measurable() -> None:
    """境界の向きを固定する。始端そのものは弾かない。"""
    frame = _frame()
    first = frame.index[0].date()

    report = run_census(_database(frame, first))

    assert report.disclosures[0].measurable


def test_doc_type_counts_separate_the_kinds_of_disclosure() -> None:
    """決算短信と予想修正を混ぜて数えると、測っているものが別になる。

    PEAD のイベントは決算短信だけ。`fins/summary` が短信だけを返すのか
    他の種類も混ざるのかは、この分布を見るまで分からない。
    """
    frame = _frame()
    database = Database("sqlite:///:memory:")
    database.create_all()
    with database.session() as session:
        PriceRepository(session).upsert_prices("7203", frame, market="JP")
        FinancialStatementRepository(session).upsert_reports(
            "7203",
            [
                FinancialReport(
                    symbol="7203",
                    fiscal_year=2024,
                    period=period,
                    disclosed_on=frame.index[60 + offset].date(),
                    doc_type=kind,
                )
                for offset, (period, kind) in enumerate(
                    (("Q1", "1QFinancialStatements"), ("Q2", "ForecastRevision"))
                )
            ],
            market="JP",
        )

    counts = run_census(database).doc_type_counts()

    assert counts["1QFinancialStatements"] == 1
    assert counts["ForecastRevision"] == 1


def test_a_missing_doc_type_is_labelled_not_dropped() -> None:
    """種類が無い行を黙って落とすと、絞り込みの影響が見えなくなる。"""
    frame = _frame()
    report = run_census(_database(frame, frame.index[100].date()))

    assert report.doc_type_counts()["(種類なし)"] == 1
