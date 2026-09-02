"""封印した事前登録どおりに計算しているか（`docs/PREREG_PEAD_JP.md`）。

ここで固定しているのは、間違えても例外が出ない種類の点である。特に反応日 R は
実測で8割が引け後開示なので、取り違えると反応そのものをドリフトとして数える。
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from stock_ai.backtest.pead import (
    HOLDING_DAYS,
    MIN_TURNOVER,
    OOS_FROM,
    Period,
    assign_quantiles,
    build_events,
    clustered_t,
    crowding_split,
    is_earnings,
    reaction_position,
    session_close_on,
    spread,
)
from stock_ai.data.schema import ADJ_CLOSE, CLOSE, HIGH, LOW, OPEN, VOLUME
from stock_ai.data.types import FinancialReport
from stock_ai.database.engine import Database
from stock_ai.database.repository import FinancialStatementRepository, PriceRepository

_BARS = 400


def _frame(bars: int = _BARS, *, close: float = 2_000.0, volume: float = 100_000.0):
    index = pd.bdate_range("2022-01-03", periods=bars, name="date")
    return pd.DataFrame(
        {OPEN: close, HIGH: close, LOW: close, CLOSE: close, ADJ_CLOSE: close, VOLUME: volume},
        index=index,
    )


# --- セクション3-2: イベントの種類 -------------------------------------------


def test_only_financial_statements_are_events() -> None:
    assert is_earnings("1QFinancialStatements_Consolidated_JP")
    assert is_earnings("FYFinancialStatements_NonConsolidated_JP")
    # 上位に出てこない変種も名前で拾う。件数で絞ると取りこぼす。
    assert is_earnings("FYFinancialStatements_Consolidated_REIT")


def test_forecast_revisions_are_not_events() -> None:
    assert not is_earnings("ForecastRevision")
    assert not is_earnings("DividendForecastRevision")


def test_an_unknown_kind_is_not_an_event() -> None:
    """「短信でなかった」ではなく「短信だと確認できていない」。"""
    assert not is_earnings(None)
    assert not is_earnings("")


# --- セクション3-1: 反応日 R --------------------------------------------------


def test_the_session_close_moved_later_in_november_2024() -> None:
    assert session_close_on(dt.date(2024, 11, 4)) == dt.time(15, 0)
    assert session_close_on(dt.date(2024, 11, 5)) == dt.time(15, 30)


def test_an_intraday_disclosure_reacts_the_same_day() -> None:
    index = _frame().index
    day = index[100].date()

    position = reaction_position(index, day, dt.time(14, 0))

    assert position is not None
    assert index[position].date() == day


def test_an_after_hours_disclosure_reacts_the_next_session() -> None:
    """8割を占めるこの経路を取り違えると、反応をドリフトとして数える。"""
    index = _frame().index
    day = index[100].date()

    position = reaction_position(index, day, dt.time(16, 0))

    assert position is not None
    assert index[position].date() == index[101].date()


def test_the_boundary_is_the_close_itself() -> None:
    """15:00 ちょうどは引け後。境界の向きを固定する。"""
    index = _frame().index
    day = index[100].date()

    assert reaction_position(index, day, dt.time(14, 59)) == 100
    assert reaction_position(index, day, dt.time(15, 0)) == 101


def test_the_extended_session_keeps_1510_intraday_only_after_the_change() -> None:
    index = pd.bdate_range("2024-10-01", periods=60, name="date")
    before = index[10].date()  # 2024-10 → 引けは 15:00
    after = index[40].date()  # 2024-11-25 頃 → 引けは 15:30

    assert reaction_position(index, before, dt.time(15, 10)) == 11
    assert reaction_position(index, after, dt.time(15, 10)) == 40


def test_a_disclosure_without_a_time_has_no_reaction_day() -> None:
    """既定値で埋めると、8割の反応日を1日取り違えたまま先に進む。"""
    index = _frame().index
    assert reaction_position(index, index[100].date(), None) is None


def test_a_disclosure_before_the_price_history_has_no_reaction_day() -> None:
    index = _frame().index
    before = (index[0] - pd.Timedelta(days=30)).date()
    assert reaction_position(index, before, dt.time(14, 0)) is None


# --- 組み立てと計算 ------------------------------------------------------------


def _database(
    frames: dict[str, pd.DataFrame], reports: dict[str, list[FinancialReport]]
) -> Database:
    database = Database("sqlite:///:memory:")
    database.create_all()
    with database.session() as session:
        prices = PriceRepository(session)
        statements = FinancialStatementRepository(session)
        for symbol, frame in frames.items():
            prices.upsert_prices(symbol, frame, market="JP")
        for symbol, rows in reports.items():
            statements.upsert_reports(symbol, rows, market="JP")
    return database


def _report(symbol: str, on: dt.date, at: dt.time = dt.time(16, 0)) -> FinancialReport:
    return FinancialReport(
        symbol=symbol,
        fiscal_year=on.year,
        disclosed_on=on,
        disclosed_at=at,
        doc_type="1QFinancialStatements_Consolidated_JP",
    )


def _with_drift(frame: pd.DataFrame, reaction: int, jump: float, drift: float) -> pd.DataFrame:
    """反応日に ``jump``、その後60営業日で ``drift`` だけ動く系列を作る。

    驚きの向きとその後のドリフトを**こちらで決めた値**にしておくと、
    出てきた数字が計算式どおりかを検算できる。動くことの確認では足りない。
    """
    out = frame.copy()
    close = out[CLOSE].to_numpy(dtype=float).copy()
    close[reaction:] *= 1.0 + jump
    entry = reaction + 1
    exit_at = entry + HOLDING_DAYS
    close[exit_at:] *= 1.0 + drift
    for column in (OPEN, HIGH, LOW, CLOSE, ADJ_CLOSE):
        out[column] = close
    return out


def test_a_known_drift_comes_back_as_the_spread() -> None:
    """上位分位 +8%、下位分位 −4% を仕込み、差が出るかを検算する。

    5分位に切るので、月内に5銘柄以上が要る。驚きの順に並ぶよう jump を
    振り、上位1銘柄と下位1銘柄だけにドリフトを与える。
    """
    base = _frame()
    reaction = 100
    day = base.index[reaction].date()

    jumps = [0.10, 0.05, 0.0, -0.05, -0.10]
    drifts = [0.08, 0.0, 0.0, 0.0, -0.04]
    symbols = [f"100{i}" for i in range(5)]
    frames = {
        symbol: _with_drift(base, reaction, jump, drift)
        for symbol, jump, drift in zip(symbols, jumps, drifts, strict=True)
    }
    # 引け後開示なので、開示日は反応日の1営業日前。
    reports = {s: [_report(s, base.index[reaction - 1].date())] for s in symbols}

    built = build_events(_database(frames, reports), Period.ALL)

    assert built.total == 5
    assert {e.reaction_on for e in built.events} == {day}
    result = spread(built.frame())
    # 上位 +8%、下位 −4% を仕込んだので差は 12%。コストは両建てで 0.6%。
    assert result.difference == pytest.approx(0.12 - 0.006, abs=1e-6)
    assert result.high == pytest.approx(0.08 - 0.003, abs=1e-6)
    assert result.low == pytest.approx(-0.04 + 0.003, abs=1e-6)


def test_the_surprise_is_measured_on_the_reaction_day_not_the_disclosure_day() -> None:
    """引け後開示の驚きは翌営業日の値動き。当日の値動きにニュースは無い。"""
    base = _frame()
    reaction = 100
    frame = _with_drift(base, reaction, 0.10, 0.0)
    symbol = "7203"
    reports = {symbol: [_report(symbol, base.index[reaction - 1].date(), dt.time(16, 0))]}

    built = build_events(_database({symbol: frame}, reports), Period.ALL)

    assert built.total == 1
    event = built.events[0]
    assert event.reaction_on == base.index[reaction].date()
    assert event.surprise == pytest.approx(0.10, abs=1e-9)
    assert not event.intraday


def test_a_thin_name_is_excluded_and_counted() -> None:
    base = _frame(volume=1.0)  # 2,000円 x 1株 = 売買代金2,000円
    reaction = 100
    symbol = "9999"
    reports = {symbol: [_report(symbol, base.index[reaction - 1].date())]}

    built = build_events(_database({symbol: base}, reports), Period.ALL)

    assert built.total == 0
    assert built.excluded_thin == 1


def test_the_turnover_floor_is_the_registered_one() -> None:
    assert MIN_TURNOVER == 100_000_000.0


# --- セクション6: IS / OOS の遮断 ---------------------------------------------


def test_the_period_boundary_is_the_registered_one() -> None:
    assert dt.date(2024, 1, 1) == OOS_FROM
    assert Period.IS.contains(dt.date(2023, 12, 29))
    assert not Period.IS.contains(dt.date(2024, 1, 4))
    assert Period.OOS.contains(dt.date(2024, 1, 4))
    assert not Period.OOS.contains(dt.date(2023, 12, 29))


def test_is_and_oos_do_not_overlap() -> None:
    """境界を跨ぐ2件を仕込み、それぞれの期間に1件ずつ入ることを見る。"""
    base = _frame(bars=900)  # 2022-01 から2年半ぶん
    symbol = "7203"
    positions = [i for i, ts in enumerate(base.index) if ts.date() in (dt.date(2023, 6, 1),)] or [
        300
    ]
    early = positions[0]
    late = next(i for i, ts in enumerate(base.index) if ts.date() >= dt.date(2024, 6, 3))
    frame = _with_drift(base, early, 0.05, 0.0)
    reports = {
        symbol: [
            _report(symbol, base.index[early - 1].date()),
            FinancialReport(
                symbol=symbol,
                fiscal_year=2025,
                disclosed_on=base.index[late - 1].date(),
                disclosed_at=dt.time(16, 0),
                doc_type="2QFinancialStatements_Consolidated_JP",
            ),
        ]
    }
    database = _database({symbol: frame}, reports)

    inside = build_events(database, Period.IS)
    outside = build_events(database, Period.OOS)
    everything = build_events(database, Period.ALL)

    assert inside.total == 1
    assert outside.total == 1
    assert everything.total == 2
    assert all(e.reaction_on < OOS_FROM for e in inside.events)
    assert all(e.reaction_on >= OOS_FROM for e in outside.events)


# --- セクション3-3: 分位 -------------------------------------------------------


def test_quantiles_are_cut_inside_each_month() -> None:
    """将来の月の分布を使っていない（セクション9の先読みチェック）。"""
    rows = []
    for month, base_surprise in (("2023-01", 0.0), ("2023-02", 1.0)):
        for i in range(5):
            rows.append(
                {
                    "month": month,
                    "surprise": base_surprise + i * 0.01,
                    "forward": 0.0,
                    "reaction_on": dt.date(2023, int(month[-2:]), i + 1),
                }
            )
    ranked = assign_quantiles(pd.DataFrame(rows))

    # 2月の驚きは1月より一律に大きいが、月内で切るので両方に上位も下位も出る。
    for month in ("2023-01", "2023-02"):
        same = ranked[ranked["month"] == month]
        assert set(same["quantile"]) == {0, 1, 2, 3, 4}


def test_a_month_with_too_few_events_is_dropped() -> None:
    """5分位に3件しかない月を無理に分けると、差が個別事情に支配される。"""
    rows = [
        {
            "month": "2023-01",
            "surprise": i * 0.01,
            "forward": 0.0,
            "reaction_on": dt.date(2023, 1, i + 1),
        }
        for i in range(3)
    ]
    assert assign_quantiles(pd.DataFrame(rows)).empty


# --- セクション7: 日付クラスタ -------------------------------------------------


def test_clustered_t_counts_days_not_events() -> None:
    """同じ日のイベントは同じ地合いを共有する。独立に数えると t が出すぎる。"""
    values = pd.Series([0.01] * 20)
    one_day = pd.Series([dt.date(2023, 1, 4)] * 20)
    many_days = pd.Series([dt.date(2023, 1, 4) + dt.timedelta(days=i) for i in range(20)])

    _, single = clustered_t(values, one_day)
    _, spread_out = clustered_t(values, many_days)

    assert single == 1
    assert spread_out == 20


def test_a_single_cluster_gives_no_t_value() -> None:
    values = pd.Series([0.01, 0.02])
    same_day = pd.Series([dt.date(2023, 1, 4)] * 2)

    t_value, clusters = clustered_t(values, same_day)

    assert clusters == 1
    assert pd.isna(t_value)


def test_a_spread_with_too_few_clusters_is_flagged_unreliable() -> None:
    """数字が出ることと、その数字を信じてよいことは別。"""
    rows = [
        {
            "month": "2023-01",
            "surprise": i * 0.01,
            "forward": 0.01 * i,
            "reaction_on": dt.date(2023, 1, (i % 20) + 1),
        }
        for i in range(10)
    ]
    result = spread(pd.DataFrame(rows))

    assert result.clusters < 30
    assert not result.reliable


# --- セクション3-4: 混雑度 -----------------------------------------------------


def test_crowding_is_split_on_each_year_median() -> None:
    """全期間の固定値だと、古い年が一律に閑散日になる。"""
    rows = []
    for year, counts in ((2022, [2, 4, 6]), (2025, [20, 40, 60])):
        for i, count in enumerate(counts):
            rows.append(
                {
                    "reaction_on": dt.date(year, 5, i + 1),
                    "same_day_count": count,
                    "surprise": 0.0,
                    "forward": 0.0,
                    "month": f"{year}-05",
                }
            )
    busy, quiet = crowding_split(pd.DataFrame(rows))

    # 各年の中央値（4 と 40）を超える日だけが混雑日。2025年が一律に混雑日に
    # ならないことを見ている。
    assert sorted(busy["same_day_count"]) == [6, 60]
    assert sorted(quiet["same_day_count"]) == [2, 4, 20, 40]


# --- ベンチマーク --------------------------------------------------------------


def test_the_benchmark_symbol_is_not_itself_an_event() -> None:
    """ETF自身をイベントに数えると、ベンチマークが被説明変数に混ざる。"""
    base = _frame()
    reaction = 100
    symbol, bench = "7203", "1306"
    frames = {symbol: _with_drift(base, reaction, 0.10, 0.0), bench: base}
    reports = {s: [_report(s, base.index[reaction - 1].date())] for s in (symbol, bench)}

    built = build_events(_database(frames, reports), Period.ALL, benchmark=bench)

    assert built.total == 1
    assert built.events[0].symbol == symbol


def test_the_benchmark_is_subtracted_from_both_the_surprise_and_the_return() -> None:
    """市場が動いた日の反応を、そのまま驚きにしない。"""
    base = _frame()
    reaction = 100
    symbol, bench = "7203", "1306"
    # 銘柄は反応日に +10%、市場は +4%。驚きは差の +6% になるはず。
    frames = {
        symbol: _with_drift(base, reaction, 0.10, 0.0),
        bench: _with_drift(base, reaction, 0.04, 0.0),
    }
    reports = {symbol: [_report(symbol, base.index[reaction - 1].date())]}

    plain = build_events(_database(frames, reports), Period.ALL)
    against = build_events(_database(frames, reports), Period.ALL, benchmark=bench)

    assert plain.events[0].surprise == pytest.approx(0.10, abs=1e-9)
    assert against.events[0].surprise == pytest.approx(0.10 - 0.04, abs=1e-9)
    assert against.benchmark == bench
    assert plain.benchmark is None


def test_the_spread_is_unchanged_by_the_benchmark() -> None:
    """主要指標は差なので、両分位から同じものを引いても変わらない。

    TOPIX が取れず 1306 で代替しても合否判定が動かない、という主張の裏付け。
    """
    base = _frame()
    reaction = 100
    jumps = [0.10, 0.05, 0.0, -0.05, -0.10]
    drifts = [0.08, 0.0, 0.0, 0.0, -0.04]
    symbols = [f"100{i}" for i in range(5)]
    frames = {
        s: _with_drift(base, reaction, j, d) for s, j, d in zip(symbols, jumps, drifts, strict=True)
    }
    frames["1306"] = _with_drift(base, reaction, 0.03, 0.02)
    reports = {s: [_report(s, base.index[reaction - 1].date())] for s in symbols}
    database = _database(frames, reports)

    plain = spread(build_events(database, Period.ALL).frame())
    against = spread(build_events(database, Period.ALL, benchmark="1306").frame())

    assert against.difference == pytest.approx(plain.difference, abs=1e-9)
    # 分位ごとの数字のほうは、ベンチマークのぶんだけ下がる。
    assert against.high < plain.high
