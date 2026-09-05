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
    count_52w_highs,
    count_halt_resumptions,
    count_limit_moves,
    high_event_returns,
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
    opens: list[float] | None = None,
) -> None:
    """1銘柄ぶんの足を入れる。``flat`` の位置は高値＝安値にする。"""
    flat = flat or set()
    frame = pd.DataFrame(
        {
            "open": opens if opens is not None else closes,
            "high": [
                c if index in flat else max(c, (opens or closes)[index]) * 1.02
                for index, c in enumerate(closes)
            ],
            "low": [
                c if index in flat else min(c, (opens or closes)[index]) * 0.98
                for index, c in enumerate(closes)
            ],
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


def test_the_move_histogram_bins_the_moves() -> None:
    """前日比を刻むだけ。

    **近似の当たり具合はこれでは判定できない**（2026-09-05 に判明）。制限幅は
    円建てなので、正しく拾えていてもパーセントでは連続的に散る。ここで確かめる
    のは、同じ値の件が同じ山に入ることだけである。
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


# --- 52週高値更新 ---------------------------------------------------------


def test_a_new_high_is_counted() -> None:
    """過去 lookback 営業日の最高値を超えた日。"""
    days = _sessions(60)
    closes = [200.0] * 59 + [210.0]
    database = _database()
    _store(database, "1234", days, closes)

    census = count_52w_highs(database, symbols=["1234"], lookback=50)

    assert census.events == 1
    assert census.per_day[days[59]] == 1
    assert census.moves == [pytest.approx(0.05)]


def test_the_trailing_high_excludes_the_day_itself() -> None:
    """**その日を含めると、自分自身を超えられず1件も出ない。** 例外は出ない。"""
    days = _sessions(60)
    closes = [float(200 + index) for index in range(60)]  # 毎日更新している
    database = _database()
    _store(database, "1234", days, closes)

    census = count_52w_highs(database, symbols=["1234"], lookback=50)

    assert census.events == 10  # 50日目以降の10日すべてが更新


def test_matching_the_old_high_is_not_an_update() -> None:
    """同値は更新ではない。境目を等号でずらすと件数が変わる。"""
    days = _sessions(60)
    closes = [200.0] * 59 + [200.0]
    database = _database()
    _store(database, "1234", days, closes)

    assert count_52w_highs(database, symbols=["1234"], lookback=50).events == 0


def test_a_symbol_without_a_full_year_is_skipped() -> None:
    """履歴が lookback に満たない銘柄は、更新のしようがない。"""
    days = _sessions(30)
    database = _database()
    _store(database, "1234", days, [float(200 + i) for i in range(30)])

    census = count_52w_highs(database, symbols=["1234"], lookback=50)

    assert census.events == 0
    assert census.excluded_no_history == 1


def test_concentration_shows_when_events_pile_onto_the_same_days() -> None:
    """**52週高値の要はここである。** 固まれば独立観測は件数より少ない。

    件数だけ数えて「1,000件ある」と読むと、検出できる差を小さく見積もる。
    """
    days = _sessions(60)
    database = _database()
    # 20銘柄が同じ日に一斉に更新する。
    for index in range(20):
        _store(database, f"1{index:03d}", days, [200.0] * 59 + [210.0])

    census = count_52w_highs(database, lookback=50)

    assert census.events == 20
    assert census.effective_days() == 1.0  # 20件あるが、日は1つしかない
    assert census.concentration() == pytest.approx(1.0)


def test_concentration_is_near_the_share_when_events_are_spread() -> None:
    """均等に散っていれば、上位1割の日には1割ぶんしか乗らない。"""
    days = _sessions(70)
    database = _database()
    # 20銘柄が1日ずつずれて更新する。
    for index in range(20):
        closes = [200.0] * 70
        closes[50 + index :] = [210.0] * (70 - 50 - index)
        _store(database, f"1{index:03d}", days, closes)

    census = count_52w_highs(database, lookback=50)

    assert census.effective_days() == 20.0
    assert census.concentration() == pytest.approx(0.1, abs=0.01)


# --- §0 に入れる分散の材料 ------------------------------------------------


def _benchmark(
    database: Database,
    days: list[dt.date],
    closes: list[float] | None = None,
    opens: list[float] | None = None,
) -> None:
    """ベンチマーク 1306 を入れる。控除の相手。"""
    _store(database, "1306", days, closes or [1000.0] * len(days), opens=opens)


def test_the_series_is_one_value_per_event_day() -> None:
    """同じ日の銘柄は等加重で1つにまとめる。**銘柄ごとに並べない。**

    銘柄日をそのまま並べると、同じ日の値が独立な観測に見える。
    """
    days = _sessions(60)
    database = _database()
    _benchmark(database, days)
    for index in range(3):
        _store(database, f"1{index:03d}", days, [200.0] * 55 + [210.0] * 5)

    values = high_event_returns(database, holding=1, lookback=50)

    assert len(values) == 1  # 3銘柄が同じ日に更新 → 1つの観測


def test_the_entry_is_the_next_open_not_the_event_close() -> None:
    """**更新は引けにしか分からない。** その日の終値では買えない。

    更新日の終値 210 で買えるなら +10% 取れるが、翌日の寄付き 231 で買えば 0。
    """
    days = _sessions(60)
    database = _database()
    _benchmark(database, days)
    closes = [200.0] * 55 + [210.0] + [205.0] * 4
    _store(database, "1234", days, closes)

    values = high_event_returns(database, holding=1, lookback=50)

    # 翌日の寄付き 205 で買って同日の終値 205 で降りる → 0。
    # 更新日の終値 210 で買っていれば 205/210 − 1 = −2.4% になる。
    assert values == [pytest.approx(0.0)]


def test_the_benchmark_is_matched_by_date_not_by_position() -> None:
    """**位置で取ると、足の無い日があったぶんずれる。** 例外は出ない。"""
    days = _sessions(61)
    database = _database()
    # ベンチマークは毎日 +1% ずつ上がる。ずれれば超過リターンに 1% 乗る。
    _benchmark(database, days, [1000.0 * (1.01**index) for index in range(len(days))])
    # 銘柄側の足を1日抜く。位置で取ればベンチマークが1日ずれる。
    kept = days[:10] + days[11:]
    closes = [200.0] * 55 + [210.0] * (len(kept) - 55)
    _store(database, "1234", kept, closes)

    values = high_event_returns(database, holding=1, lookback=50)

    # 銘柄は寄付き＝終値なので 0。ベンチも同じ日の寄付き→終値なので 0。
    # 日付で合っていれば 0、位置でずれていれば ±1% になる。
    assert values == [pytest.approx(0.0)]


def test_the_benchmark_is_actually_subtracted() -> None:
    """ベンチマークが動けば、超過リターンはその分だけ減る。

    **控除し忘れると、市場が上げた日の観測がすべて水増しされる。**
    """
    days = _sessions(60)
    database = _database()
    # ベンチマークは買う日（更新の翌日 = 56日目）に寄付き 1000 → 終値 1100。
    bench_closes = [1000.0] * 56 + [1100.0] * 4
    bench_opens = [1000.0] * 60
    _benchmark(database, days, bench_closes, opens=bench_opens)
    _store(database, "1234", days, [200.0] * 55 + [210.0] * 5)

    values = high_event_returns(database, holding=1, lookback=50)

    # 銘柄は 0、ベンチは 1100/1000 − 1 = +10%。超過は −10%。
    assert values == [pytest.approx(-0.10)]


def test_a_holding_of_zero_is_refused() -> None:
    """0日保有は意味を持たない。黙って空を返さない。"""
    days = _sessions(60)
    database = _database()
    _benchmark(database, days)
    _store(database, "1234", days, [200.0] * 59 + [210.0])

    with pytest.raises(ValueError, match="holding"):
        high_event_returns(database, holding=0, lookback=50)


def test_a_missing_benchmark_is_an_error_not_an_empty_series() -> None:
    """**ベンチマークが無いのに空で返すと、控除し忘れに気付けない。**"""
    from stock_ai.core.exceptions import DataError

    days = _sessions(60)
    database = _database()
    _store(database, "1234", days, [200.0] * 59 + [210.0])

    with pytest.raises(DataError, match="1306"):
        high_event_returns(database, holding=1, lookback=50)


def test_the_benchmark_is_not_itself_an_event() -> None:
    """**指数そのものを母集団に入れない。** 1306 は JP の銘柄として保存される。

    ETF は毎日のように52週高値を更新し、売買代金も十分にある。除かないと、
    「市場が上げた」ことをイベントとして数える。例外は出ない。
    """
    days = _sessions(60)
    database = _database()
    # ベンチマークが毎日 +1% で上がり続ける。除外していなければ更新の山になる。
    _benchmark(database, days, [1000.0 * (1.01**index) for index in range(len(days))])

    census = count_52w_highs(database, lookback=50)

    assert census.events == 0
    assert high_event_returns(database, holding=1, lookback=50) == []
