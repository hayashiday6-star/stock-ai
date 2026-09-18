"""イベント窓の正本（`stock_ai.backtest.event_window`）。

**説をまたいで1つだけ置く。** #8（増担保）と #5（上方修正）が同じ形を使う。
`margin_census` に置いたまま #5 から呼べば、そのうち2つ目が書かれる——
`key_period` を、それを戒める文章を書いた同じ日に2つ目書いた前例がある。

**向きはここで決めない。** 超過リターンをそのまま返す。反転を2箇所に置くと、
どちらで反転したのか分からなくなる。
"""

from __future__ import annotations

from statistics import fmean

import numpy as np
import pandas as pd
import pytest

from stock_ai.backtest.event_window import EventSample, event_returns, event_sample
from stock_ai.data.schema import ADJ_CLOSE, CLOSE, HIGH, LOW, OPEN, VOLUME
from stock_ai.database.engine import Database
from stock_ai.database.repository import PriceRepository

_INDEX = pd.bdate_range("2020-01-06", periods=60, name="date")
_DAYS = [stamp.date() for stamp in _INDEX]


def _database(closes: dict[str, list[float]]) -> Database:
    database = Database("sqlite:///:memory:")
    database.create_all()
    with database.session() as session:
        repo = PriceRepository(session)
        for symbol, close in closes.items():
            repo.upsert_prices(
                symbol,
                pd.DataFrame(
                    {
                        OPEN: close,
                        HIGH: close,
                        LOW: close,
                        CLOSE: close,
                        ADJ_CLOSE: close,
                        VOLUME: [1_000_000.0] * len(close),
                    },
                    index=_INDEX,
                ),
                market="JP",
            )
    return database


_FLAT = [100.0] * 60


class TestTheOneCopyStillBehaves:
    """**移しても値が変わらないこと。** #8 の測定はこの関数の上に乗っている。"""

    def test_a_ten_percent_rise_against_a_flat_market(self) -> None:
        rising = [100.0 if position <= 1 else 110.0 for position in range(60)]
        database = _database({"1306": _FLAT, "1301": rising})

        values = event_returns(database, [("1301", _DAYS[0])], holding=5)

        assert values == [pytest.approx(0.10)]

    def test_the_market_is_taken_out(self) -> None:
        both = [100.0 if position <= 1 else 110.0 for position in range(60)]
        database = _database({"1306": both, "1301": both})

        values = event_returns(database, [("1301", _DAYS[0])], holding=5)

        assert values == [pytest.approx(0.0)]

    def test_entry_is_the_open_after_the_event_day(self) -> None:
        """**イベント日の引けには間に合わない。** 開示も公表も引け後が普通である。"""
        shaped = [80.0 if position == 3 else 100.0 for position in range(60)]
        database = _database({"1306": _FLAT, "1301": shaped})

        values = event_returns(database, [("1301", _DAYS[3])], holding=5)

        assert values == [pytest.approx(0.0)]

    def test_the_same_day_is_one_equal_weighted_observation(self) -> None:
        up = [100.0 if position <= 1 else 110.0 for position in range(60)]
        down = [100.0 if position <= 1 else 90.0 for position in range(60)]
        database = _database({"1306": _FLAT, "1301": up, "1302": down})

        values = event_returns(database, [("1301", _DAYS[0]), ("1302", _DAYS[0])], holding=5)

        assert values == [pytest.approx(0.0)]

    def test_events_after_the_cut_are_not_used(self) -> None:
        database = _database({"1306": _FLAT, "1301": _FLAT})

        values = event_returns(
            database, [("1301", _DAYS[0]), ("1301", _DAYS[10])], holding=5, until=_DAYS[0]
        )

        assert len(values) == 1

    def test_an_event_too_close_to_the_end_is_dropped(self) -> None:
        """**窓が足りないイベントを短い窓で測らない。** 混ぜると窓が2つになる。"""
        database = _database({"1306": _FLAT, "1301": _FLAT})

        assert event_returns(database, [("1301", _DAYS[57])], holding=5) == []

    def test_the_sign_is_not_flipped_here(self) -> None:
        """**向きは呼ぶ側が決める。** ここで反転すると、二重反転に気付けない。"""
        falling = [100.0 if position <= 1 else 90.0 for position in range(60)]
        database = _database({"1306": _FLAT, "1301": falling})

        values = event_returns(database, [("1301", _DAYS[0])], holding=5)

        assert values[0] < 0

    def test_a_window_of_zero_is_refused(self) -> None:
        with pytest.raises(ValueError):
            event_returns(_database({"1306": _FLAT}), [], holding=0)

    def test_a_missing_benchmark_is_an_error_not_an_empty_list(self) -> None:
        """**「ベンチマークが無い」を「イベントが無い」に化けさせない。**"""
        with pytest.raises(ValueError):
            event_returns(_database({"1301": _FLAT}), [("1301", _DAYS[0])], holding=5)


def test_margin_census_still_exposes_the_same_function() -> None:
    """**呼ぶ側で書き直さない。** 名前が残っていても、中身は1つであること。"""
    from stock_ai.backtest import margin_census

    assert margin_census.event_returns is event_returns


def test_the_returns_do_not_depend_on_the_order_events_are_given() -> None:
    """**並べ替えは内側でやる。** 呼ぶ側の順序で値が変わらないこと。"""
    rng = np.random.default_rng(0)
    close = list(1_000.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, 60))))
    database = _database({"1306": _FLAT, "1301": close, "1302": close})

    forward = event_returns(database, [("1301", _DAYS[0]), ("1302", _DAYS[5])], holding=5)
    backward = event_returns(database, [("1302", _DAYS[5]), ("1301", _DAYS[0])], holding=5)

    assert forward == pytest.approx(backward)


def _database_of_lengths(lengths: dict[str, int]) -> Database:
    """銘柄ごとに**足の長さを変えた**保存先。

    上場廃止で窓が切れる形を作る。`_database` は全銘柄が同じ長さなので、
    **その形が1件も入らない。**

    Args:
        lengths: 銘柄ごとの営業日数。

    Returns:
        価格の入った :class:`Database`。
    """
    database = Database("sqlite:///:memory:")
    database.create_all()
    with database.session() as session:
        repo = PriceRepository(session)
        for symbol, length in lengths.items():
            close = [100.0] * length
            repo.upsert_prices(
                symbol,
                pd.DataFrame(
                    {
                        OPEN: close,
                        HIGH: close,
                        LOW: close,
                        CLOSE: close,
                        ADJ_CLOSE: close,
                        VOLUME: [1_000_000.0] * length,
                    },
                    index=_INDEX[:length],
                ),
                market="JP",
            )
    return database


class TestEveryEventLandsInExactlyOneBucket:
    """**どこにも数えられずに消える経路を作らない。**

    2026-09-17 まで、この関数は5箇所で黙って `continue` していた。**引いた
    2,000 件のうち何件が残ったのかを、対照も #5・#8 も一度も出していなかった。**
    """

    def test_the_parts_add_up_to_what_was_drawn(self) -> None:
        database = _database_of_lengths({"1306": 60, "1301": 60, "1302": 30})
        found = event_sample(
            database,
            [("1301", _DAYS[0]), ("1302", _DAYS[25]), ("9999", _DAYS[0])],
            holding=5,
        )
        assert found.drawn == 3
        parts = (
            found.used,
            found.no_prices,
            found.not_trading,
            found.ended_early,
            found.too_recent,
            found.bad_leg,
            found.no_benchmark,
        )
        assert sum(parts) == found.drawn

    def test_a_bucket_that_does_not_add_up_is_refused(self) -> None:
        """**この検査が落ちる条件を、実際に1つ作る。**

        落ちないなら、この検査は何も守っていない。
        """
        with pytest.raises(ValueError, match="内訳"):
            EventSample(
                values=[],
                drawn=5,
                used=1,
                no_prices=0,
                not_trading=0,
                ended_early=0,
                too_recent=0,
                bad_leg=0,
                no_benchmark=0,
                stock_leg=0.0,
                bench_leg=0.0,
                truncated=[],
            )

    def test_a_delisting_is_not_counted_as_the_end_of_the_period(self) -> None:
        """**上場廃止と、期間の端は別に数える。**

        片方に混ぜると、偏る経路（廃止）が偏らない経路（端）に薄められる。
        """
        database = _database_of_lengths({"1306": 60, "1301": 60, "1302": 30})
        # 1302 は 30 本で切れている。1306 は 60 本まで在るので**廃止**。
        delisted = event_sample(database, [("1302", _DAYS[26])], holding=5)
        assert (delisted.ended_early, delisted.too_recent) == (1, 0)
        # 1301 は最後まで在るので、窓が足りないのは**期間の端**。
        at_the_edge = event_sample(database, [("1301", _DAYS[57])], holding=5)
        assert (at_the_edge.ended_early, at_the_edge.too_recent) == (0, 1)

    def test_a_hole_in_the_data_is_not_the_same_as_not_being_listed(self) -> None:
        """**名簿に在って価格が無い**のと、**その日に動いていなかった**のは別。

        混ぜると、直すべき穴が直しようのない構造に薄められる。400回の対照で
        34.4% が1つの行に潰れていた（2026-09-17）。
        """
        database = _database_of_lengths({"1306": 60, "1302": 30})
        # 価格が1本も無い銘柄——**取り込みの穴。**
        hole = event_sample(database, [("9999", _DAYS[0])], holding=5)
        assert (hole.no_prices, hole.not_trading, hole.used) == (1, 0, 0)
        # 足は在るが、その日には無い——**上場前・廃止後。穴ではない。**
        gone = event_sample(database, [("1302", _DAYS[45])], holding=5)
        assert (gone.no_prices, gone.not_trading, gone.used) == (0, 1, 0)

    def test_only_the_hole_raises_a_warning(self) -> None:
        """**当たり前に出るほうで鳴らさない。** 鳴りっぱなしの警告は読まれない。"""
        database = _database_of_lengths({"1306": 60, "1302": 30})
        hole = event_sample(database, [("9999", _DAYS[0])], holding=5)
        assert any("穴" in line for line in hole.warnings())
        gone = event_sample(database, [("1302", _DAYS[45])], holding=5)
        assert not any("穴" in line for line in gone.warnings())


class TestTheTwoLegsAddBackUpToTheExcess:
    """**銘柄側と指数側を別に出す。**

    超過だけを見ていると、「銘柄が上がった」のか「引く相手が上がらなかった」
    のかが分からない。陰性対照の `t` の平均 +0.49 は、その区別が要る。
    """

    def test_the_difference_of_the_legs_is_the_mean_excess(self) -> None:
        rising = [100.0 if position <= 1 else 110.0 for position in range(60)]
        market = [100.0 if position <= 1 else 105.0 for position in range(60)]
        database = _database({"1306": market, "1301": rising})

        found = event_sample(database, [("1301", _DAYS[0])], holding=5)
        assert found.values
        assert found.stock_leg - found.bench_leg == pytest.approx(fmean(found.values))

    def test_the_legs_are_the_raw_returns_not_the_gross_ratios(self) -> None:
        rising = [100.0 if position <= 1 else 110.0 for position in range(60)]
        database = _database({"1306": _FLAT, "1301": rising})

        found = event_sample(database, [("1301", _DAYS[0])], holding=5)
        assert found.stock_leg == pytest.approx(0.10)
        assert found.bench_leg == pytest.approx(0.0)


class TestTheDroppedSideIsMeasuredNotAssumed:
    """**落ちた側がどれだけ悪かったかを、推測ではなく測る。**

    「悪く終わった側に偏る**はず**」と書いて確かめないのが、`identity_check`
    で踏んだ形である。押し上げの大きさは、割合と差の積で出せる。
    """

    def test_the_lift_is_the_share_times_the_gap(self) -> None:
        # 1302 は 30 本で切れる。切れる前に **下げてから** 消える。
        database = _database_of_lengths({"1306": 60, "1301": 60})
        with database.session() as session:
            # **入った後に下げる。** 入る前に下げると、落ちた側の超過が 0 に
            # なって「落ちても悪くなかった」という別の話になる。
            falling = [100.0] * 28 + [80.0] * 2
            PriceRepository(session).upsert_prices(
                "1302",
                pd.DataFrame(
                    {
                        OPEN: falling,
                        HIGH: falling,
                        LOW: falling,
                        CLOSE: falling,
                        ADJ_CLOSE: falling,
                        VOLUME: [1_000_000.0] * 30,
                    },
                    index=_INDEX[:30],
                ),
                market="JP",
            )
        found = event_sample(database, [("1301", _DAYS[0]), ("1302", _DAYS[26])], holding=5)
        assert found.ended_early == 1
        assert found.truncated  # 足の在るところまでは測れている
        assert found.truncated[0] < 0  # 落ちた側は負で終わっていた
        lift = found.survivorship_bias()
        assert lift is not None
        share = found.ended_early / found.drawn
        assert lift == pytest.approx(share * (fmean(found.values) - fmean(found.truncated)))

    def test_the_lift_is_none_when_the_dropped_side_cannot_be_measured(self) -> None:
        """**測れていないことを 0 と読まない。**"""
        database = _database_of_lengths({"1306": 60, "1301": 60})
        found = event_sample(database, [("1301", _DAYS[0])], holding=5)
        assert found.ended_early == 0
        assert found.survivorship_bias() is None

    def test_the_warning_names_the_delistings(self) -> None:
        """**表は読む側が気付く必要がある。気付かなくても目に入るのが警告。**"""
        database = _database_of_lengths({"1306": 60, "1302": 30})
        found = event_sample(database, [("1302", _DAYS[26])], holding=5)
        assert any("上場廃止" in line for line in found.warnings())


class TestTheThinWrapperHasNoSecondCopy:
    """`event_returns` は `event_sample` の値だけを返す。**2つ目を書かない。**"""

    def test_the_wrapper_matches_the_sample(self) -> None:
        rising = [100.0 if position <= 1 else 110.0 for position in range(60)]
        database = _database({"1306": _FLAT, "1301": rising})
        events = [("1301", _DAYS[0]), ("1301", _DAYS[10])]
        assert (
            event_returns(database, events, holding=5)
            == event_sample(database, events, holding=5).values
        )
