"""リバーサルの日次系列が、意図した窓と意図した universe で組まれているか。

ここで固定したいのは3つ。

- **分位1が最も下げた側**であること（符号を取り違えると全部が裏返る）
- 窓が **D+1 寄付き → D+1+保有 寄付き** であること（寄り引けを混ぜない）
- universe の差し替えが、**日付ごとの名簿と最新の名簿とで実際に変わる**こと
  ——生存バイアスの実測は、この差し替えが効いていることが前提になる
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from stock_ai.backtest.pead import Period
from stock_ai.backtest.reversal import (
    build_series,
    dated_universe,
    survivors_universe,
    survivorship_gap,
)
from stock_ai.data.schema import ADJ_CLOSE, CLOSE, HIGH, LOW, OPEN, VOLUME
from stock_ai.database.engine import Database
from stock_ai.database.repository import PriceRepository

_BARS = 60
_JUDGE = 30
"""判定日の位置。前に20営業日の売買代金、後ろに21営業日の窓が要る。"""

_INDEX = pd.bdate_range("2024-01-01", periods=_BARS, name="date")
_JUDGE_DAY = _INDEX[_JUDGE].date()


def _frame(lookback_return: float = 0.0, forward_return: float = 0.0):
    """判定日の5日リターンと、窓のフォワードリターンだけを狙って埋める。

    それ以外の日は 1,000 円で平ら。分位の割り当てと窓の取り方だけを見たいので、
    他の日に動きを入れない。
    """
    close = [1_000.0] * _BARS
    close[_JUDGE] = 1_000.0 * (1.0 + lookback_return)
    opens = [1_000.0] * _BARS
    opens[_JUDGE + 21] = 1_000.0 * (1.0 + forward_return)
    return pd.DataFrame(
        {
            OPEN: opens,
            HIGH: close,
            LOW: close,
            CLOSE: close,
            # 調整済みと生値を同じにして、分割調整の比を1に固定する。
            ADJ_CLOSE: close,
            VOLUME: [500_000.0] * _BARS,
        },
        index=_INDEX,
    )


def _database(frames: dict[str, pd.DataFrame]) -> Database:
    database = Database("sqlite:///:memory:")
    database.create_all()
    with database.session() as session:
        for symbol, frame in frames.items():
            PriceRepository(session).upsert_prices(symbol, frame, market="JP")
    return database


def _ten_symbols() -> dict[str, pd.DataFrame]:
    """下落率が10段階、フォワードも10段階の10銘柄とベンチマーク。

    銘柄 ``100i`` は5日で ``-10% + 2i``、窓のリターンは ``0.1% * i``。
    5分位なら1分位あたり2銘柄になる。
    """
    frames = {"1306": _frame()}
    for index in range(10):
        frames[f"100{index}"] = _frame(
            lookback_return=-0.10 + 0.02 * index, forward_return=0.001 * index
        )
    return frames


def _one_day(database: Database, **kwargs):
    return build_series(
        database, Period.ALL, start=_JUDGE_DAY, end=_JUDGE_DAY, quantiles=5, **kwargs
    )


def test_quantile_one_is_the_biggest_fallers() -> None:
    """**符号の確認。** 分位1に入るのは最も下げた2銘柄（フォワード 0.0% と 0.1%）。"""
    series = _one_day(_database(_ten_symbols()))

    assert series.days == [_JUDGE_DAY]
    assert series.counts == [10]
    assert series.quantiles[0][0] == pytest.approx((0.000 + 0.001) / 2)
    assert series.quantiles[0][-1] == pytest.approx((0.008 + 0.009) / 2)


def test_the_window_runs_open_to_open_over_the_holding_period() -> None:
    """寄り引けを混ぜない。動かしたのは D+21 の**寄付き**だけである。"""
    frames = {"1306": _frame(), "7203": _frame(lookback_return=-0.10, forward_return=0.05)}
    # 1銘柄では分位を作れないので、対照を9本足す。
    for index in range(9):
        frames[f"900{index}"] = _frame(lookback_return=0.01 * index)

    series = _one_day(_database(frames))

    # 7203 がいちばん下げているので分位1。同じ分位のもう1銘柄はフォワード0。
    assert series.quantiles[0][0] == pytest.approx(0.05 / 2)


def test_long_only_nets_off_the_benchmark_over_the_same_window() -> None:
    frames = _ten_symbols()
    # ベンチマークだけ窓の間に 2% 上がる。分位1から引かれるはず。
    frames["1306"] = _frame(forward_return=0.02)

    series = _one_day(_database(frames))

    assert series.benchmark == [pytest.approx(0.02)]
    assert series.long_only()[0] == pytest.approx((0.000 + 0.001) / 2 - 0.02)


def test_long_short_is_quantile_one_minus_quantile_five() -> None:
    series = _one_day(_database(_ten_symbols()))
    assert series.long_short()[0] == pytest.approx(0.0005 - 0.0085)


def test_a_dated_roster_admits_only_what_was_listed_then() -> None:
    """名簿で絞ると、分位の中身が変わる。"""
    database = _database(_ten_symbols())
    listed = {f"100{index}" for index in range(2, 10)}  # 1000 と 1001 を外す
    snapshots = {dt.date(2023, 1, 1): listed}

    series = _one_day(database, snapshots=snapshots)

    assert series.counts == [8]
    assert series.universe_label == "snapshots"
    # いちばん下げた2銘柄が抜けたので、分位1は次の2銘柄になる。
    assert series.quantiles[0][0] == pytest.approx((0.002 + 0.003) / 2)


def test_a_symbol_listed_only_later_never_enters_the_earlier_day() -> None:
    """**先読みを入れないこと。** 判定日より後の名簿は使わない。"""
    database = _database(_ten_symbols())
    snapshots = {
        dt.date(2023, 1, 1): {f"100{index}" for index in range(8)},
        # 判定日より後に 1008/1009 が載る名簿。この日には効かないはず。
        _JUDGE_DAY + dt.timedelta(days=30): {f"100{index}" for index in range(10)},
    }

    series = _one_day(database, snapshots=snapshots)

    assert series.counts == [8]


def test_days_before_the_first_roster_have_no_universe_at_all() -> None:
    """名簿より前の日は空集合。直近の名簿を流用すると和集合と同じ罠になる。"""
    database = _database(_ten_symbols())
    snapshots = {_JUDGE_DAY + dt.timedelta(days=30): {f"100{index}" for index in range(10)}}

    with pytest.raises(ValueError, match="1つも無い"):
        _one_day(database, snapshots=snapshots)


def test_survivors_only_applies_the_latest_roster_to_every_day() -> None:
    """生存バイアスの対照。**意図的に先読みしている側。**"""
    database = _database(_ten_symbols())
    snapshots = {
        dt.date(2023, 1, 1): {f"100{index}" for index in range(10)},
        _JUDGE_DAY + dt.timedelta(days=30): {f"100{index}" for index in range(2, 10)},
    }

    clean = _one_day(database, snapshots=snapshots)
    survivors = _one_day(database, snapshots=snapshots, survivors_only=True)

    assert clean.counts == [10]
    assert survivors.counts == [8]
    assert survivors.universe_label == "survivors"
    # 名簿ありのほうが、消えた2銘柄（最も下げた側）を分位1に含む。
    assert clean.quantiles[0][0] != pytest.approx(survivors.quantiles[0][0])


def test_universe_helpers_pick_the_roster_the_series_would_pick() -> None:
    """``dated_universe`` / ``survivors_universe`` が同じ規則で選ぶこと。"""
    snapshots = {
        dt.date(2023, 1, 1): {"1000"},
        dt.date(2024, 1, 1): {"1000", "1001"},
    }
    dated = dated_universe(snapshots)
    survivors = survivors_universe(snapshots)

    assert dated(dt.date(2023, 6, 1)) == {"1000"}
    assert dated(dt.date(2024, 6, 1)) == {"1000", "1001"}
    assert dated(dt.date(2022, 6, 1)) == set()
    assert survivors(dt.date(2022, 6, 1)) == {"1000", "1001"}


def test_a_missing_entry_bar_is_counted_not_silently_dropped() -> None:
    """ベンチマークの暦にある日で値が欠けた銘柄日を数える。

    センサスは銘柄ごとの暦で数えていたので、件数はここでずれる。**推測せずに
    済むよう数え口を置く。**
    """
    frames = _ten_symbols()
    # 売買停止で1本まるごと無い形にする。ベンチマークの暦に載せ替えた時点で
    # 入場日が欠ける。
    frames["1005"] = frames["1005"].drop(index=_INDEX[_JUDGE + 1])

    series = _one_day(_database(frames))

    assert series.counts == [9]
    assert series.excluded_calendar == 1


def test_a_day_without_enough_symbols_is_dropped_and_counted() -> None:
    """5分位に割れない日は落とす。**落としたことを数える。**

    3銘柄を5分位に割ると、空の分位ができる。空を0%として扱うと「その日は
    リバーサルが効かなかった」という観測に化けるので、日ごと落とす。
    """
    frames = {"1306": _frame()}
    for index in range(3):  # 5分位に足りない
        frames[f"100{index}"] = _frame(lookback_return=-0.01 * index)

    series = _one_day(_database(frames))

    assert series.days == []
    assert series.excluded_thin_day == 1


def test_the_series_needs_a_benchmark_to_fix_the_calendar() -> None:
    frames = {f"100{index}": _frame() for index in range(10)}

    with pytest.raises(ValueError, match="ベンチマーク"):
        _one_day(_database(frames))


def test_the_gap_refuses_series_that_are_not_aligned() -> None:
    """揃っていない差は、バイアスではなく期間の違いを測る。"""
    with pytest.raises(ValueError, match="日数が違う"):
        survivorship_gap([0.01, 0.02], [0.01])


def test_the_gap_is_clean_minus_survivors() -> None:
    assert survivorship_gap([0.03, 0.01], [0.01, 0.02]) == pytest.approx([0.02, -0.01])
