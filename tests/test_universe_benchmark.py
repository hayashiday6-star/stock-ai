"""引く相手を、持ち方と同じ加重で作る（`stock_ai.backtest.universe_benchmark`）。

**なぜ要るか。** イベント型の陰性対照で、乱数で選んだ銘柄と日を20営業日持つと
指数に勝った——情報が何も無いのに、である。分解したら銘柄側 +1.11% 対
指数側 +0.88%、差 +0.22% で、**生存フィルタの押し上げは −0.00%** だった
（2026-09-17、400回・800,000件）。

`1306` は時価総額加重、イベントのバスケットは等加重。**加重が違うものを
引き算していた。**

ここで留めるのは、**その差が実際に消えること**である。部品が正しいだけでは
足りない——`factor_panel` は部品を13個テストしていて、組み立てを一度も
呼んでいなかった。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stock_ai.backtest.event_window import event_sample
from stock_ai.backtest.universe_benchmark import (
    MIN_SYMBOLS_PER_DAY,
    equal_weighted_windows,
)
from stock_ai.data.schema import ADJ_CLOSE, CLOSE, HIGH, LOW, OPEN, VOLUME
from stock_ai.database.engine import Database
from stock_ai.database.repository import PriceRepository

_INDEX = pd.bdate_range("2020-01-06", periods=80, name="date")
_DAYS = [stamp.date() for stamp in _INDEX]


def _database(series: dict[str, list[float]]) -> Database:
    database = Database("sqlite:///:memory:")
    database.create_all()
    with database.session() as session:
        repo = PriceRepository(session)
        for symbol, close in series.items():
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
                    index=_INDEX[: len(close)],
                ),
                market="JP",
            )
    return database


def _drifting(rate: float, length: int = 80) -> list[float]:
    """毎営業日 ``rate`` ずつ増える足。"""
    return [100.0 * (1.0 + rate) ** step for step in range(length)]


def _crowd(rate: float, count: int) -> dict[str, list[float]]:
    """同じ伸び方をする銘柄を ``count`` 本。**薄い日の足切りを越えるため。**"""
    return {f"{2000 + offset}": _drifting(rate) for offset in range(count)}


class TestTheAverageIsTheAverage:
    def test_one_day_is_the_mean_of_the_symbols_that_day(self) -> None:
        series = _crowd(0.001, MIN_SYMBOLS_PER_DAY)
        series["9001"] = _drifting(0.005)
        built = equal_weighted_windows(_database(series), holding=5)

        # 31 本のうち 30 本が +0.1%/日、1本が +0.5%/日。
        # **窓は `D+1` の寄付き → `D+holding` の終値なので、伸びるのは4歩。**
        # 最初 5歩で書いて落ちた——`holding` が歩数だと思い込んでいた。
        slow = (1.001**4) - 1.0
        fast = (1.005**4) - 1.0
        expected = (MIN_SYMBOLS_PER_DAY * slow + fast) / (MIN_SYMBOLS_PER_DAY + 1)
        assert built.get(_DAYS[0]) == pytest.approx(expected)

    def test_a_thin_day_gets_no_value_at_all(self) -> None:
        """**3社の等加重は「宇宙」ではない。** 0 を返さず、値を出さない。"""
        built = equal_weighted_windows(_database(_crowd(0.001, 3)), holding=5)

        assert built.get(_DAYS[0]) is None
        assert built.thin_days > 0
        assert any("届かず" in line for line in built.warnings())

    def test_the_count_behind_each_average_is_kept(self) -> None:
        """**平均だけ見て、何社の平均かを見ない形を作らない。**"""
        built = equal_weighted_windows(_database(_crowd(0.001, 40)), holding=5)

        assert built.counted[_DAYS[0]] == 40

    def test_a_window_past_the_end_is_not_averaged_in(self) -> None:
        built = equal_weighted_windows(_database(_crowd(0.001, 40)), holding=5)

        assert built.get(_DAYS[-1]) is None
        assert built.get(_DAYS[-5]) is None
        assert built.get(_DAYS[-6]) is not None

    def test_a_window_of_zero_is_refused(self) -> None:
        with pytest.raises(ValueError, match="holding"):
            equal_weighted_windows(_database(_crowd(0.001, 40)), holding=0)

    def test_an_empty_universe_is_refused(self) -> None:
        with pytest.raises(ValueError):
            equal_weighted_windows(_database({"1306": _drifting(0.0)}), holding=5, symbols=[])


class TestTheTiltActuallyGoesAway:
    """**部品ではなく、組み立てで確かめる。**

    「加重が違うから差が出る」と書いただけでは、直ったことにならない。
    **同じ盤面で、指数を引いたときに差が出て、宇宙を引いたときに消える**のを
    1本通して見る。
    """

    @staticmethod
    def _board() -> tuple[Database, list[tuple[str, object]]]:
        # `1306` は**動かない**——時価総額加重の指数が大型だけを映している姿。
        # 一方、宇宙の銘柄は毎日 +0.2% 伸びる——小型が勝っている姿。
        series = {"1306": [100.0] * 80}
        series.update(_crowd(0.002, 40))
        database = _database(series)
        rng = np.random.default_rng(0)
        picked = [
            (f"{2000 + int(rng.integers(0, 40))}", _DAYS[int(rng.integers(0, 60))])
            for _draw in range(200)
        ]
        return database, picked

    def test_the_index_leaves_a_tilt_behind(self) -> None:
        database, picked = self._board()

        found = event_sample(database, picked, holding=5)  # 既定は `1306`

        assert found.values
        # 40銘柄すべて +0.2%/日 なので、5日で約 +1.0%。指数は動かない。
        assert found.stock_leg - found.bench_leg == pytest.approx((1.002**4) - 1.0, abs=1e-6)
        assert sum(found.values) / len(found.values) > 0.007

    def test_the_universe_takes_the_tilt_out(self) -> None:
        database, picked = self._board()
        picks = [f"{2000 + n}" for n in range(40)]
        built = equal_weighted_windows(database, holding=5, symbols=picks)

        found = event_sample(database, picked, holding=5, subtract=built)

        assert found.values
        # **情報が無いのだから、0 になるべきである。**
        assert sum(found.values) / len(found.values) == pytest.approx(0.0, abs=1e-9)

    def test_a_day_the_universe_cannot_price_is_counted_not_dropped(self) -> None:
        """**引く相手が無い日を、黙って捨てない。**"""
        database, _picked = self._board()
        picks = [f"{2000 + n}" for n in range(40)]
        built = equal_weighted_windows(database, holding=5, symbols=picks)

        # 窓が作れない末尾の日を1件だけ渡す。
        found = event_sample(database, [("2000", _DAYS[1])], holding=5, subtract=built)
        assert found.used == 1

        naked = equal_weighted_windows(database, holding=5, symbols=picks)
        naked.window.pop(_DAYS[1])
        missing = event_sample(database, [("2000", _DAYS[1])], holding=5, subtract=naked)
        assert (missing.used, missing.no_benchmark) == (0, 1)


class TestThinningChangesThePrecisionNotThePeriod:
    """**散らばりが動いた理由を、1つに絞れる形にする。**

    `MIN_SYMBOLS_PER_DAY` を上げると、落ちるのは期間の初めの薄い日である。
    **日の集合が変わるので、SD が動いても「精度」か「期間」か分からない。**

    銘柄を間引けば、**日は1日も減らずに引く相手の誤差だけが増える。**
    """

    def test_not_a_single_day_is_lost(self) -> None:
        database = _database(_crowd(0.001, 80))

        whole = equal_weighted_windows(database, holding=5)
        quarter = equal_weighted_windows(database, holding=5, fraction=0.25, seed=1)

        assert set(quarter.window) == set(whole.window)

    def test_fewer_symbols_stand_behind_each_average(self) -> None:
        database = _database(_crowd(0.001, 80))

        quarter = equal_weighted_windows(database, holding=5, fraction=0.25, seed=1)

        assert quarter.symbols == 20
        assert quarter.counted[_DAYS[0]] == 20

    def test_the_thinning_reproduces_from_its_seed(self) -> None:
        """**記録すれば手で再現できること。**

        **銘柄の中身を変えてある。** 全部同じ足にすると、どの部分集合を
        引いても平均が同じになり、**「種が効いていない」と「銘柄が同じ」を
        区別できない**——最初その形で書いて、緑になりかけた。
        """
        varied = {f"{2000 + offset}": _drifting(0.0005 * offset) for offset in range(80)}
        database = _database(varied)

        first = equal_weighted_windows(database, holding=5, fraction=0.5, seed=3)
        again = equal_weighted_windows(database, holding=5, fraction=0.5, seed=3)
        other = equal_weighted_windows(database, holding=5, fraction=0.5, seed=4)

        assert first.window == again.window
        assert first.window != other.window

    def test_a_thinned_benchmark_says_so(self) -> None:
        """**診断用だと、気付かなくても目に入ること。**"""
        database = _database(_crowd(0.001, 80))

        quarter = equal_weighted_windows(database, holding=5, fraction=0.25, seed=1)

        assert any("診断用" in line for line in quarter.warnings())
        assert not any(
            "診断用" in line for line in equal_weighted_windows(database, holding=5).warnings()
        )

    def test_a_fraction_outside_the_range_is_refused(self) -> None:
        """**この検査が落ちる条件を、実際に1つ作る。**"""
        database = _database(_crowd(0.001, 40))

        for bad in (0.0, -0.5, 1.5):
            with pytest.raises(ValueError, match="fraction"):
                equal_weighted_windows(database, holding=5, fraction=bad)

    def test_it_never_thins_down_to_nothing(self) -> None:
        database = _database(_crowd(0.001, 40))

        assert equal_weighted_windows(database, holding=5, fraction=0.001, seed=1).symbols == 1


class TestTheDailySeriesIsNotTheWindowOne:
    """**窓のほうから日次は取り出せない。**

    `holding=1` にしても「翌日の寄付き → 翌日の終値」であって、**終値どうしの
    変化ではない。** #13（月替わり）が要るのは後者である。

    そして 2026-09-19 に、この関数を**書いたつもりで書いていなかった。**
    `cli._universe_calendar` が存在しない名前を呼び、**ユーザーの PC で
    `--universe` を付けた実行が落ちるまで分からなかった。**
    """

    def test_it_is_the_close_to_close_change(self) -> None:
        from stock_ai.backtest.universe_benchmark import equal_weighted_daily

        database = _database(_crowd(0.002, 40))

        found = equal_weighted_daily(database)

        assert found[_DAYS[1]] == pytest.approx(0.002)
        assert found[_DAYS[5]] == pytest.approx(0.002)

    def test_the_first_day_has_no_return(self) -> None:
        from stock_ai.backtest.universe_benchmark import equal_weighted_daily

        found = equal_weighted_daily(_database(_crowd(0.002, 40)))

        assert _DAYS[0] not in found

    def test_it_averages_across_symbols(self) -> None:
        from stock_ai.backtest.universe_benchmark import equal_weighted_daily

        series = _crowd(0.001, 30)
        series.update({f"{3000 + n}": _drifting(0.003) for n in range(30)})

        found = equal_weighted_daily(_database(series))

        assert found[_DAYS[1]] == pytest.approx((0.001 + 0.003) / 2)

    def test_a_thin_day_is_left_out_not_zeroed(self) -> None:
        """**薄い日は 0 ではなく、入らない。**"""
        from stock_ai.backtest.universe_benchmark import equal_weighted_daily

        found = equal_weighted_daily(_database(_crowd(0.001, 3)))

        assert found == {}

    def test_the_floor_can_be_moved(self) -> None:
        from stock_ai.backtest.universe_benchmark import equal_weighted_daily

        found = equal_weighted_daily(_database(_crowd(0.001, 3)), min_symbols=2)

        assert found[_DAYS[1]] == pytest.approx(0.001)

    def test_a_floor_below_one_is_refused(self) -> None:
        from stock_ai.backtest.universe_benchmark import equal_weighted_daily

        with pytest.raises(ValueError, match="min_symbols"):
            equal_weighted_daily(_database(_crowd(0.001, 40)), min_symbols=0)

    def test_an_empty_universe_is_refused(self) -> None:
        from stock_ai.backtest.universe_benchmark import equal_weighted_daily

        with pytest.raises(ValueError, match="銘柄"):
            equal_weighted_daily(_database(_crowd(0.001, 40)), symbols=[])

    def test_the_window_version_gives_a_different_answer(self) -> None:
        """**別物であることを、同じ盤面で見せる。**

        `holding=1` の窓は「翌日の寄付き → 翌日の終値」なので、毎日 +0.2%
        伸びる足では **0** になる。日次のほうは +0.2% である。
        """
        from stock_ai.backtest.universe_benchmark import (
            equal_weighted_daily,
            equal_weighted_windows,
        )

        database = _database(_crowd(0.002, 40))

        daily = equal_weighted_daily(database)
        window = equal_weighted_windows(database, holding=1)

        assert daily[_DAYS[1]] == pytest.approx(0.002)
        assert window.get(_DAYS[0]) == pytest.approx(0.0)
