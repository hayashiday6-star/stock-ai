"""値幅制限と売買停止明けのセンサス。

**数えるだけのコードだが、数え違いは例外を出さない。** 件数が少なめに出れば
「母集団が足りない」と読んで説を閉じるし、多めに出れば足りると思って封印する。
どちらも黙って起きるので、境目を1つずつ押さえる。
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from stock_ai.backtest.event_census import (
    CANDIDATE_GAP_DAYS,
    MIN_MARKET_BREADTH,
    count_halt_resumptions,
    count_limit_moves,
)
from stock_ai.database.engine import Database
from stock_ai.database.repository import PriceRepository, get_or_create_security

#: 流動性フィルタを確実に通す出来高。終値200円 × これで 1億円を超える。
LIQUID_VOLUME = 1_000_000


def _database() -> Database:
    database = Database("sqlite:///:memory:")
    database.create_all()
    return database


def _sessions(count: int, start: dt.date = dt.date(2024, 1, 1)) -> list[dt.date]:
    """平日だけを ``count`` 日ぶん。"""
    days: list[dt.date] = []
    day = start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += dt.timedelta(days=1)
    return days


def _store(
    database: Database,
    symbol: str,
    days: list[dt.date],
    closes: list[float],
    *,
    flat: set[int] | None = None,
    volume: int = LIQUID_VOLUME,
) -> None:
    """1銘柄ぶんの足を入れる。``flat`` の位置は高値＝安値にする。"""
    flat = flat or set()
    frame = pd.DataFrame(
        {
            "open": closes,
            "high": [c if index in flat else c * 1.02 for index, c in enumerate(closes)],
            "low": [c if index in flat else c * 0.98 for index, c in enumerate(closes)],
            "close": closes,
            "adj_close": closes,
            "volume": [volume] * len(closes),
        },
        index=pd.DatetimeIndex(days, name="date"),
    )
    with database.session() as session:
        get_or_create_security(session, symbol, market="JP")
        PriceRepository(session).upsert_prices(symbol, frame, market="JP")
        session.commit()


def _market(database: Database, days: list[dt.date], count: int = MIN_MARKET_BREADTH) -> None:
    """暦を成立させるだけの脇役を入れる。

    暦は実データから作るので、**脇役がいないと営業日が1日も無いことになる。**
    """
    for index in range(count):
        _store(database, f"9{index:03d}", days, [200.0] * len(days))


# --- 値幅制限 -------------------------------------------------------------


def test_a_flat_bar_that_rose_is_counted() -> None:
    """高値＝安値・出来高あり・前日比プラス。近似の定義そのもの。"""
    days = _sessions(30)
    closes = [200.0] * 30
    closes[20] = 220.0  # +10% で張り付いた
    database = _database()
    _store(database, "1234", days, closes, flat={20})

    census = count_limit_moves(database, symbols=["1234"])

    assert census.events == 1
    assert census.moves == [pytest.approx(0.10)]
    assert census.per_day[days[20]] == 1


def test_a_flat_bar_that_fell_is_not_counted() -> None:
    """**上側だけを数える。** ストップ安は生存バイアス感応度が高い。"""
    days = _sessions(30)
    closes = [200.0] * 30
    closes[20] = 180.0
    database = _database()
    _store(database, "1234", days, closes, flat={20})

    assert count_limit_moves(database, symbols=["1234"]).events == 0


def test_a_flat_bar_with_no_volume_is_not_counted() -> None:
    """出来高が無い日は、張り付いたのではなく約定していない。"""
    days = _sessions(30)
    closes = [200.0] * 30
    closes[20] = 220.0
    database = _database()
    _store(database, "1234", days, closes, flat={20}, volume=0)

    assert count_limit_moves(database, symbols=["1234"]).events == 0


def test_a_moving_bar_is_not_counted() -> None:
    """上げても、高値と安値が離れていれば制限には達していない。"""
    days = _sessions(30)
    closes = [200.0] * 30
    closes[20] = 220.0
    database = _database()
    _store(database, "1234", days, closes)  # flat 指定なし

    assert count_limit_moves(database, symbols=["1234"]).events == 0


def test_a_discontinuity_is_not_mistaken_for_a_limit() -> None:
    """**1日で 50% を超える動きは値動きではない。** #6 と同じ規則で落とす。

    分割・併合の調整漏れがここに紛れ込むと、「制限に達した日」の件数が水増し
    される。例外は出ない。
    """
    days = _sessions(30)
    closes = [200.0] * 30
    closes[20] = 200_000.0  # 1:1000 の併合が調整されていない形
    database = _database()
    _store(database, "1234", days, closes, flat={20})

    assert count_limit_moves(database, symbols=["1234"]).events == 0


def test_a_thin_name_is_counted_before_the_filter_and_dropped_after() -> None:
    """**#1 で消えたのはここである。** 落ちた件数を数えないと、母集団を見誤る。"""
    days = _sessions(30)
    closes = [200.0] * 30
    closes[20] = 220.0
    database = _database()
    _store(database, "1234", days, closes, flat={20}, volume=100)

    census = count_limit_moves(database, symbols=["1234"])

    assert census.raw_events == 1
    assert census.events == 0
    assert census.excluded_thin == 1
    assert census.survival == 0.0


def test_the_move_histogram_shows_whether_the_approximation_landed() -> None:
    """制限幅が効いているなら、前日比は少数の位置に固まる。

    **これが近似の当たり具合を見る唯一の手段である。** 過去の制限幅の表を
    持っていないので、値そのものと突き合わせることはできない。
    """
    days = _sessions(60)
    closes = [200.0] * 60
    flat = set()
    for position in (20, 30, 40):
        closes[position] = closes[position - 1] * 1.10
        closes[position + 1 :] = [closes[position]] * (len(closes) - position - 1)
        flat.add(position)
    database = _database()
    _store(database, "1234", days, closes, flat=flat)

    census = count_limit_moves(database, symbols=["1234"])

    assert census.events == 3
    # 3件とも同じ +10% なので、山は1つしか立たない。
    assert len([bucket for bucket, count in census.move_histogram() if count]) == 1


# --- 売買停止明け ---------------------------------------------------------


def test_a_gap_over_market_days_is_a_halt() -> None:
    """市場が開いていた日に足が無ければ、停止である。"""
    days = _sessions(40)
    database = _database()
    _market(database, days)
    kept = days[:20] + days[25:]  # 5営業日ぶん抜く
    _store(database, "1234", kept, [200.0] * len(kept))

    census = count_halt_resumptions(database)

    assert census.per_day[days[25]] == 1
    assert census.lengths == [5]


def test_a_long_weekend_is_not_a_halt() -> None:
    """**暦の隙間そのものは停止ではない。** 市場も開いていない。

    足切り（``CANDIDATE_GAP_DAYS``）だけで判定すると、連休がすべて停止になる。
    """
    days = _sessions(40)
    database = _database()
    _market(database, days)
    _store(database, "1234", days, [200.0] * len(days))

    assert count_halt_resumptions(database).events == 0
    assert CANDIDATE_GAP_DAYS >= 4  # 3連休は暦日で4日空く


def test_a_thin_market_day_does_not_make_everyone_look_halted() -> None:
    """**数銘柄しか値の付かない日を営業日に数えると、全銘柄が停止に見える。**

    暦は「``MIN_MARKET_BREADTH`` 以上の銘柄が約定した日」で作る。ここを外すと
    件数が桁で増え、しかも例外は出ない。
    """
    days = _sessions(40)
    database = _database()
    _market(database, days)
    # 誰も約定していない日に、1銘柄だけ足がある。
    stray = [days[19] + dt.timedelta(days=1)]
    _store(database, "8888", stray, [200.0])
    kept = days[:20] + days[25:]
    _store(database, "1234", kept, [200.0] * len(kept))

    census = count_halt_resumptions(database)

    # 8888 の1日を営業日に数えていれば、停止の長さが 6 になる。
    assert census.lengths == [5]


def test_a_thin_name_is_dropped_but_counted() -> None:
    """停止に**入る前**の売買代金で測る。再開後の板は当てにならない。"""
    days = _sessions(40)
    database = _database()
    _market(database, days)
    kept = days[:20] + days[25:]
    _store(database, "1234", kept, [200.0] * len(kept), volume=100)

    census = count_halt_resumptions(database)

    assert census.raw_events == 1
    assert census.events == 0
    assert census.excluded_thin == 1


def test_a_halt_that_crossed_a_discontinuity_is_counted_separately() -> None:
    """併合をまたいだ停止は、他の停止と混ぜずに数える。

    除外はしない。**黙って落とすと、同じ欠陥が別の場所で効いているときに
    気付けなくなる**（#6 の `IMPLAUSIBLE_FORWARD` と同じ扱い）。
    """
    days = _sessions(40)
    database = _database()
    _market(database, days)
    kept = days[:20] + days[25:]
    closes = [200.0] * 20 + [200_000.0] * (len(kept) - 20)
    _store(database, "1234", kept, closes)

    census = count_halt_resumptions(database)

    assert census.events == 1
    assert census.crossed_discontinuity == 1


# --- 執行できるか ---------------------------------------------------------


def test_a_second_flat_day_is_counted_as_unfillable() -> None:
    """**連続ストップ高では買えない。** 費用の問題ではなく、取れないという問題。

    約定を仮定した検証は、この件をそのまま「買えた」ことにする。例外は出ない。
    """
    days = _sessions(30)
    closes = [200.0] * 30
    closes[20] = 220.0
    closes[21:] = [242.0] * (30 - 21)
    database = _database()
    _store(database, "1234", days, closes, flat={20, 21})

    census = count_limit_moves(database, symbols=["1234"])

    assert census.events == 2  # 20日目と21日目の両方が制限に達している
    assert census.unfillable == 1  # 20日目のぶんは翌日も張り付いていて買えない
    assert census.fillable == 1


def test_the_gap_is_measured_against_the_previous_close() -> None:
    """払う分は「翌日始値 ÷ 当日終値 − 1」。"""
    days = _sessions(30)
    closes = [200.0] * 30
    closes[20] = 220.0
    closes[21:] = [230.0] * (30 - 21)
    database = _database()
    _store(database, "1234", days, closes, flat={20})

    census = count_limit_moves(database, symbols=["1234"])

    # 翌日の始値は _store が終値と同じ値を入れるので 230。230/220 - 1。
    assert census.gaps == [pytest.approx(230.0 / 220.0 - 1.0)]


def test_the_open_position_says_where_in_the_day_you_bought() -> None:
    """始値が当日の高安のどこか。1 に寄れば、いちばん悪いところで買っている。"""
    days = _sessions(30)
    closes = [200.0] * 30
    closes[20] = 220.0
    closes[21:] = [230.0] * (30 - 21)
    database = _database()
    _store(database, "1234", days, closes, flat={20})

    census = count_limit_moves(database, symbols=["1234"])

    # _store は高値 close*1.02、安値 close*0.98、始値 close なので中央になる。
    assert census.open_positions == [pytest.approx(0.5)]


def test_an_event_on_the_last_bar_has_no_next_day() -> None:
    """系列の末尾は買う日が無い。**買えなかったのとは別に数える。**"""
    days = _sessions(30)
    closes = [200.0] * 30
    closes[29] = 220.0
    database = _database()
    _store(database, "1234", days, closes, flat={29})

    census = count_limit_moves(database, symbols=["1234"])

    assert census.events == 1
    assert census.no_next_bar == 1
    assert census.unfillable == 0
    assert census.fillable == 0


def test_the_gap_is_not_the_split_ratio() -> None:
    """**翌日が分割の初日なら、生値のギャップは分割比率になる。**

    調整後で測らないと、費用の仮定が桁で狂う。例外は出ない。
    """
    days = _sessions(30)
    # 20日目にストップ高（生 200 → 220）。21日目から 1:2 分割で生値が半分に
    # なるが、**経済的には同じ値**（220 の半分が 110）。
    closes = [200.0] * 20 + [220.0] + [110.0] * 9
    database = _database()
    frame = pd.DataFrame(
        {
            "open": closes,
            "high": [c if i == 20 else c * 1.02 for i, c in enumerate(closes)],
            "low": [c if i == 20 else c * 0.98 for i, c in enumerate(closes)],
            "close": closes,
            "adj_close": [c if i > 20 else c / 2 for i, c in enumerate(closes)],
            "volume": [LIQUID_VOLUME] * len(closes),
        },
        index=pd.DatetimeIndex(days, name="date"),
    )
    with database.session() as session:
        get_or_create_security(session, symbol := "1234", market="JP")
        PriceRepository(session).upsert_prices(symbol, frame, market="JP")
        session.commit()

    census = count_limit_moves(database, symbols=["1234"])

    assert census.moves == [pytest.approx(0.10)]  # 調整後で +10%
    # 生値で測れば 110/220-1 = -50%。調整後なら 0。
    assert census.gaps == [pytest.approx(0.0)]
