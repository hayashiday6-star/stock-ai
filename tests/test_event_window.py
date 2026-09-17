"""イベント窓の正本（`stock_ai.backtest.event_window`）。

**説をまたいで1つだけ置く。** #8（増担保）と #5（上方修正）が同じ形を使う。
`margin_census` に置いたまま #5 から呼べば、そのうち2つ目が書かれる——
`key_period` を、それを戒める文章を書いた同じ日に2つ目書いた前例がある。

**向きはここで決めない。** 超過リターンをそのまま返す。反転を2箇所に置くと、
どちらで反転したのか分からなくなる。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stock_ai.backtest.event_window import event_returns
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
