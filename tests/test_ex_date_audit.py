"""外した権利落ちの中身を見る道具が、本当に見分けられるか。

`#16` が急落 819 件を権利落ちで外し、警告が「中身を見ること」と言った。
**言うだけで、見る道具が無かった**（2026-09-20、ユーザーが指摘）。

ここで押さえるのは4つ。

1. **ずれを捕まえられること。** 段差を1日ずらした盤面で、そう出る
2. **配当を戻して測り直せること。** 戻しても急落なら、外すべきでなかった
3. **内訳が足して合うこと。** 合わなければ作った時点で落ちる
4. **保有窓の中の権利落ちを数えること。** そちらは外していない
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from stock_ai.backtest.ex_date_audit import (
    AdjustmentEffect,
    audit_holding_window,
    measure_adjustment,
    measure_alignment,
)
from stock_ai.backtest.knife import KNIFE_DAYS, KNIFE_DROP
from stock_ai.data.jquants_dividend import ExDividend
from stock_ai.data.schema import ADJ_CLOSE, CLOSE, HIGH, LOW, OPEN, VOLUME
from stock_ai.database.engine import Database
from stock_ai.database.repository import PriceRepository

_INDEX = pd.bdate_range("2013-01-01", "2016-12-30", name="date")
_BARS = len(_INDEX)
_FIRST = _INDEX[0].date()
_LAST = _INDEX[-1].date()


def _at(when: str) -> int:
    return int(_INDEX.get_loc(pd.Timestamp(when)))


def _frame(closes: np.ndarray) -> pd.DataFrame:
    opens = closes.copy()
    opens[1:] = closes[:-1]
    return pd.DataFrame(
        {
            OPEN: opens,
            HIGH: np.maximum(closes, opens),
            LOW: np.minimum(closes, opens),
            CLOSE: closes,
            ADJ_CLOSE: closes,
            VOLUME: [500_000.0] * len(closes),
        },
        index=_INDEX,
    )


def _walk(seed: int, drops: dict[int, float]) -> np.ndarray:
    """乱数歩行に、決め打ちの位置で段差を入れる。**定数の足を置かない。**"""
    rng = np.random.default_rng(seed)
    steps = rng.normal(0.0, 0.003, _BARS)
    for where, size in drops.items():
        steps[where] += np.log(1.0 - size)
    return 1_000.0 * np.exp(np.cumsum(steps))


def _database(series: dict[str, np.ndarray]) -> Database:
    database = Database("sqlite:///:memory:")
    database.create_all()
    with database.session() as session:
        repo = PriceRepository(session)
        for symbol, closes in series.items():
            repo.upsert_prices(symbol, _frame(closes), market="JP")
    return database


_YIELD = 0.03
_EX_DAYS = ("2013-03-27", "2013-09-26", "2014-03-27", "2014-09-26", "2015-03-27")


class TestFindingWhereTheStepIs:
    """**`ExDate` がずれていないか。** ずれていれば、外す日を間違えている。"""

    @staticmethod
    def _built(shift: int) -> tuple[Database, dict[str, dict[dt.date, ExDividend]]]:
        """段差を ``shift`` 日ずらした盤面。**0 なら合っている。**"""
        series: dict[str, np.ndarray] = {}
        rates: dict[str, dict[dt.date, ExDividend]] = {}
        for index in range(8):
            symbol = f"{1400 + index:04d}"
            where = {_at(day) + shift: _YIELD for day in _EX_DAYS}
            series[symbol] = _walk(index, where)
            rates[symbol] = {
                _INDEX[_at(day)].date(): ExDividend(rate=1_000.0 * _YIELD, special=0.0)
                for day in _EX_DAYS
            }
        return _database(series), rates

    def test_an_aligned_column_puts_the_step_on_the_day(self) -> None:
        database, rates = self._built(shift=0)

        lined = measure_alignment(database, rates, _FIRST, _LAST)

        assert lined.lowest == 0
        assert lined.aligned
        assert lined.warnings() == []

    def test_the_step_is_about_the_yield(self) -> None:
        """段差の大きさが、配当利回りと同じ桁に在る。"""
        database, rates = self._built(shift=0)

        lined = measure_alignment(database, rates, _FIRST, _LAST)
        on_the_day = dict(zip(lined.offsets, lined.medians, strict=True))[0]

        assert on_the_day == pytest.approx(-_YIELD, abs=0.005)
        assert lined.median_yield == pytest.approx(_YIELD, abs=0.005)

    def test_a_column_that_is_off_by_one_is_caught(self) -> None:
        """**この検査が落ちる条件を、実際に作る。**

        `ExDate` が1日ずれていれば、段差は +1 日目に出る。
        """
        database, rates = self._built(shift=1)

        lined = measure_alignment(database, rates, _FIRST, _LAST)

        assert lined.lowest == 1
        assert not lined.aligned
        assert any("ずれている" in line for line in lined.warnings())

    def test_a_column_that_is_early_is_caught_too(self) -> None:
        """**両向きに置く。** 片側だけだと、逆のずれを見逃す。"""
        database, rates = self._built(shift=-1)

        lined = measure_alignment(database, rates, _FIRST, _LAST)

        assert lined.lowest == -1
        assert not lined.aligned

    def test_no_step_at_all_is_caught(self) -> None:
        """既に配当を抜いた価格なら、権利落ち日に何も起きない。"""
        series = {f"{1400 + i:04d}": _walk(i, {}) for i in range(8)}
        rates = {
            symbol: {
                _INDEX[_at(day)].date(): ExDividend(rate=1_000.0 * _YIELD, special=0.0)
                for day in _EX_DAYS
            }
            for symbol in series
        }

        lined = measure_alignment(_database(series), rates, _FIRST, _LAST)

        assert any("合わない" in line for line in lined.warnings())

    @staticmethod
    def _mixed(zero_group_drops: bool):
        """半分は配当あり（段差あり）、半分は額 0。**実データと同じ混ざり方。**"""
        series: dict[str, np.ndarray] = {}
        rates: dict[str, dict[dt.date, ExDividend]] = {}
        for index in range(8):
            symbol = f"{1400 + index:04d}"
            pays = index < 4  # noqa: PLR2004 - 半分ずつ
            drops = {_at(day): _YIELD for day in _EX_DAYS} if pays or zero_group_drops else {}
            series[symbol] = _walk(index, drops)
            rates[symbol] = {
                _INDEX[_at(day)].date(): ExDividend(
                    rate=1_000.0 * _YIELD if pays else 0.0, special=0.0
                )
                for day in _EX_DAYS
            }
        return _database(series), rates

    def test_a_zero_amount_group_with_no_step_says_nothing(self) -> None:
        """**額 0 の群も価格で確かめる。** 無配なら段差は出ない。"""
        database, rates = self._mixed(zero_group_drops=False)

        lined = measure_alignment(database, rates, _FIRST, _LAST)

        assert lined.zero_events == 4 * len(_EX_DAYS)
        assert lined.events == 4 * len(_EX_DAYS)
        assert lined.warnings() == []

    def test_a_zero_amount_group_with_a_step_is_caught(self) -> None:
        """**この検査が落ちる条件を、実際に作る。**

        額を 0 と読んでいるのに権利落ち日が下がるなら、**額の読み方が違う。**
        正の額を 0.99倍で裏取りしたのと同じ形を、0 側にも当てる。
        """
        database, rates = self._mixed(zero_group_drops=True)

        lined = measure_alignment(database, rates, _FIRST, _LAST)

        assert any("額の読み方が違う" in line for line in lined.warnings())

    def test_a_group_with_no_priced_dividend_still_says_something(self) -> None:
        """**早期 return で新しい検査を黙らせない。**

        `warnings()` が「1件も値付けできなかった」で `return` していて、
        **その下に置いた額 0 の検査が動かなかった**（2026-09-20、自分で
        踏んだ）。
        """
        series = {f"{1400 + i:04d}": _walk(i, {}) for i in range(4)}
        rates = {
            symbol: {_INDEX[_at(day)].date(): ExDividend(rate=0.0, special=0.0) for day in _EX_DAYS}
            for symbol in series
        }

        lined = measure_alignment(_database(series), rates, _FIRST, _LAST)

        assert lined.events == 0
        assert lined.zero_events > 0
        told = " ".join(lined.warnings())
        assert "1件も値付けできなかった" not in told
        assert "すべて額 0" in told

    def test_a_zero_span_is_refused(self) -> None:
        with pytest.raises(ValueError, match="span must be at least 1"):
            measure_alignment(_database({}), {}, _FIRST, _LAST, span=0)


class TestDividendsInsideTheHoldingWindow:
    """**ショートは配当を払う側。** 落とさずに測ると取り高が高く出る。

    そして**「消えているはず」は主張であって確認ではない**（2026-09-20、
    ユーザーが出力の自己矛盾から指摘）。**抜けた分を測る。**
    """

    @staticmethod
    def _one(offset: int, yield_on_day: float = 0.02):
        """``(DB, 額の表, 使った件, 調整に渡す形)`` を作る。"""
        entry = _at("2015-06-10")
        ex_index = entry + offset
        closes = _walk(0, {ex_index: yield_on_day})
        when = _INDEX[ex_index].date()
        amount = closes[ex_index - 1] * yield_on_day
        rates = {"1401": {when: ExDividend(rate=amount, special=0.0)}}
        paid = {"1401": [(dt.date(2015, 1, 5), when, amount)]}
        return _database({"1401": closes}), rates, [("1401", _INDEX[entry].date())], paid

    def test_a_dividend_inside_the_window_is_counted(self) -> None:
        """**`drag` は、調整に渡したのと同じ行から作る。**

        前は監査専用の `rates`（最後に公表された値＝先読みあり）から作って
        いて、**2つの列が別々の額を見ていた**（2026-09-20、実データで 3.0倍）。
        """
        database, rates, kept, paid = self._one(offset=2)

        found = audit_holding_window(database, kept, rates, paid)

        assert found.with_ex_date == 1
        assert found.share == 1.0
        assert found.drag == pytest.approx(0.02, abs=0.001)

    def test_the_adjustment_removes_what_the_window_holds(self) -> None:
        """**抜けた分が、窓の中の配当と釣り合う。**

        これが「調整が効いている」の確認である。**釣り合えば、配当ぶん
        ちょうど抜けたことが測れた**ことになる。
        """
        database, rates, kept, paid = self._one(offset=2)

        found = audit_holding_window(database, kept, rates, paid)

        assert found.removed == pytest.approx(found.drag, rel=0.1)
        assert found.matched
        assert found.warnings() == []

    def test_not_passing_the_adjustment_is_caught(self) -> None:
        """**この検査が落ちる条件を、実際に作る。**

        渡さなければ抜けた分は 0。**調整を当てていないのと同じ形**なので、
        黙って通してはいけない。

        **`drag` と `removed` を同じ入力から作った拍子に、この検査が死んだ**
        ——両方 0 になって釣り合ってしまう（2026-09-20）。**独立な相手**
        （最終データ）で捕まえ直している。
        """
        database, rates, kept, _paid = self._one(offset=2)

        found = audit_holding_window(database, kept, rates)

        assert found.removed == 0.0
        assert found.drag == 0.0
        assert found.final_yield > 0.0, "**突き合わせる相手が空では、何も捕まらない。**"
        assert any("1件も渡っていない" in line for line in found.warnings())

    def test_a_double_adjustment_is_caught(self) -> None:
        """**2倍抜いても落ちる。** 片側だけの検査にしない。"""
        database, rates, kept, paid = self._one(offset=2)
        twice = {symbol: rows + rows for symbol, rows in paid.items()}

        found = audit_holding_window(database, kept, rates, twice)

        assert found.removed > found.drag * 1.5
        assert not found.matched

    def test_a_dividend_before_the_entry_is_not_counted(self) -> None:
        """**急落側の配当を二重に数えない。** そちらは除外の担当である。"""
        database, rates, kept, paid = self._one(offset=-2)

        found = audit_holding_window(database, kept, rates, paid)

        assert found.with_ex_date == 0
        assert found.drag == 0.0

    def test_a_dividend_just_past_the_window_is_not(self) -> None:
        """**窓の外は数えない。** 境界を1つ作って確かめる。"""
        from stock_ai.backtest.knife import HOLDING

        database, rates, kept, paid = self._one(offset=HOLDING + 1)

        found = audit_holding_window(database, kept, rates, paid)

        assert found.with_ex_date == 0

    def test_a_clean_window_says_nothing(self) -> None:
        """**両向きに置く。** 常に鳴る旗は何も区別しない。"""
        database, rates, kept, paid = self._one(offset=-2)

        assert audit_holding_window(database, kept, rates, paid).warnings() == []

    def test_a_dividend_on_the_entry_day_counts_in_neither_column(self) -> None:
        """**entry 当日の権利落ちは、どちらの列にも入らない。**

        株価はその日の**寄付きで**落ちるので、**その寄付きで入るこちらは
        落ちた後の値段で入っており、配当は乗っていない。** 倍率の側からも
        同じで、entry と exit の両方に同じだけ掛かって相殺する。

        揃える前は `drag` だけがこの日を数えていて、**2つの列が1日違う窓を
        見ていた**（2026-09-20）。
        """
        database, rates, kept, paid = self._one(offset=1)

        found = audit_holding_window(database, kept, rates, paid)

        assert found.with_ex_date == 0, "**drag が entry 当日を数えている。**"
        assert found.drag == 0.0
        assert found.removed == pytest.approx(0.0, abs=1e-12)
        assert found.matched

    def test_a_dividend_revised_to_zero_still_balances(self) -> None:
        """**最終データが 0 でも、渡した額で測る。**

        調整は**その日までに公表された額**で当たる。監査が最後に公表された
        値（0）で数えると、**同じ行の2つの列が別々の額を見る**——実データで
        3.0倍ずれた（2026-09-20、ユーザーが指摘）。
        """
        database, _rates, kept, paid = self._one(offset=2)
        when = paid["1401"][0][1]
        revised = {"1401": {when: ExDividend(rate=0.0, special=0.0)}}

        found = audit_holding_window(database, kept, revised, paid)

        assert found.drag > 0.0, "**渡した額を数えていない。**"
        assert found.matched
        assert found.final_yield == 0.0
        assert found.final_only == 0

    def test_a_large_yield_balances_too(self) -> None:
        """**式の形も揃える。** 抜けるのは利回りの単純和ではない。

        抜ける量は `窓の総リターン × (1/∏(1−利回り) − 1)` である。
        **利回り 0.6 なら 1.5**——和で数えると 0.6 のままで、**同じ量を
        測っていない**（2026-09-20 に盤面で確かめた）。

        **値動きが平らな足で見る。** 価格が配当ぶん下がる足だと、総リターン
        が `1−利回り` に近づいて**両方の式がたまたま一致する**——この
        テストを最初そう書いて、違いが出なかった。
        """
        crash = _at("2015-06-10")
        closes = np.full(_BARS, 1_000.0)  # **平ら。** 配当で下げない
        when = _INDEX[crash + 2].date()
        rates = {"1401": {when: ExDividend(rate=600.0, special=0.0)}}
        paid = {"1401": [(dt.date(2015, 1, 5), when, 600.0)]}
        kept = [("1401", _INDEX[crash].date())]

        found = audit_holding_window(_database({"1401": closes}), kept, rates, paid)

        assert found.drag == pytest.approx(0.6 / 0.4, rel=1e-6), "**単純和なら 0.6 で止まる。**"
        assert found.matched

    def test_the_final_data_is_kept_out_of_the_judgement(self) -> None:
        """**最終データにだけ在る額は、別の欄に置く。**

        「その日に知りようがなかったこと」で過去の判断を裁かない。
        """
        database, rates, kept, _paid = self._one(offset=2)

        found = audit_holding_window(database, kept, rates, {"1401": []})

        assert found.drag == 0.0
        assert found.final_yield > 0.0
        assert found.final_only == 1
        assert "判定には使わない" in found.aside()

    def test_a_dividend_published_after_the_ex_date_counts_in_neither(self) -> None:
        """**公表日も同じ規則で見る。**

        `dividend_adjusted` は公表が権利落ちより後の行を飛ばす（実データで
        4,406 件）。`drag` が公表日を見ていなかったので、**調整が当てて
        いない配当を片方だけが数えていた**——残りの 1.05倍がこれだった
        （2026-09-20、ユーザーが「1.05倍で鳴り続けている」と指摘）。
        """
        database, rates, kept, paid = self._one(offset=2)
        published, when, amount = paid["1401"][0]
        assert published < when, "**足場が前提を満たしていない。**"
        late = {"1401": [(when + dt.timedelta(days=30), when, amount)]}

        found = audit_holding_window(database, kept, rates, late)

        assert found.drag == 0.0, "**調整が当てていない配当を数えている。**"
        assert found.removed == 0.0
        assert found.matched

    def test_the_warning_says_how_many_times_off(self) -> None:
        """**割り算を読む側にさせない。** 2つ並べるだけでは倍率が出ない。"""
        database, rates, kept, paid = self._one(offset=2)
        twice = {symbol: rows + rows for symbol, rows in paid.items()}

        found = audit_holding_window(database, kept, rates, twice)

        assert found.times is not None
        assert found.times > 1.5
        assert any("倍）" in line for line in found.warnings())
        # **文面が、当てている条件と同じことを言っていること。**
        assert any("ぴったり一致するはず" in line for line in found.warnings())
        assert not any("2倍なら二重" in line for line in found.warnings())

    def test_both_columns_count_the_same_events(self) -> None:
        """**分母が揃っていること。** 片方だけ数えると2つの列がずれる。"""
        database, rates, kept, paid = self._one(offset=2)

        found = audit_holding_window(database, kept, rates, paid)

        assert found.measured == len(kept)
        assert found.share == found.with_ex_date / found.measured


def test_the_crash_definition_is_not_copied_here() -> None:
    """**急落の定数は `knife` から取る。** 2つ持つと黙ってずれる。"""
    import inspect

    from stock_ai.backtest import ex_date_audit

    source = inspect.getsource(ex_date_audit)
    assert "KNIFE_DROP = " not in source
    assert "KNIFE_DAYS = " not in source
    assert ex_date_audit.KNIFE_DROP == KNIFE_DROP
    assert ex_date_audit.KNIFE_DAYS == KNIFE_DAYS


class TestAdjustingMovesTheCrashSet:
    """**先に配当を落としてから線を当てる。**

    「権利落ちが窓に在れば外す」は事前登録 §3 の**代理**であって、
    「機械的な値下がりを外す」そのものではなかった——実データで**本物の
    急落を 189 件巻き込んでいた**（2026-09-20、ユーザーが指摘）。

    **順序を直すと、両向きに動く。** 配当が作っていた下げは消え、配当に
    隠れていた下げは出る。**片方しか動かないなら、直っていない。**
    """

    @staticmethod
    def _one(fall: float, yield_on_day: float):
        """1銘柄。下げを5日に均し、そのうち1日に配当を乗せる。"""
        crash = _at("2015-06-10")
        each = 1.0 - (1.0 - fall) ** (1.0 / KNIFE_DAYS)
        drops = {crash - KNIFE_DAYS + 1 + step: each for step in range(KNIFE_DAYS)}
        ex_index = crash - 2
        drops[ex_index] = 1.0 - (1.0 - each) * (1.0 - yield_on_day)
        closes = _walk(0, drops)
        rates = {
            "1401": [
                (dt.date(2015, 1, 5), _INDEX[ex_index].date(), closes[ex_index - 1] * yield_on_day)
            ]
        }
        return _database({"1401": closes}), rates

    def test_a_fall_the_dividend_made_disappears(self) -> None:
        """配当が線の向こうに押し出していた分は、落とすと事象でなくなる。"""
        database, rates = self._one(fall=0.14, yield_on_day=0.10)

        moved = measure_adjustment(database, rates, _FIRST, _LAST)

        assert moved.lost > 0
        assert moved.after < moved.before

    def test_adjusting_can_only_remove(self) -> None:
        """**増えることはありえない。**

        窓の中の権利落ちは分母（基準日）だけを下げるので、落とせば下げは
        必ず浅くなる。**増えたら調整の向きが逆**なので、そこで落とす。
        """
        database, rates = self._one(fall=0.30, yield_on_day=0.02)

        moved = measure_adjustment(database, rates, _FIRST, _LAST)

        assert moved.after <= moved.before

    def test_a_tie_on_the_line_is_not_counted_as_a_dividend_made_crash(self) -> None:
        """**線の上にちょうど乗った件は、丸めで転ぶ。** 配当のせいにしない。

        `680 → 544` はちょうど −20%。両辺に同じ倍率を掛けると、`(544f)`
        と `(680f)*0.8` の丸めがずれて**事象でなくなる**ことがある。
        **配当が作った下げではない**ので、`lost` に数えてはいけない。

        線の当て方を直す前は**増える側**に転んでいて、**ユーザーの PC で
        落ちた**（2026-09-20、2461）。直した後は**失う側にしか転ばない**
        （近傍 600万通りで 9,208 対 0）。
        """
        crash = _at("2015-06-10")
        closes = np.full(_BARS, 680.0)
        closes[crash : crash + 3] = 544.0  # ちょうど −20%、すぐ戻る
        # **権利落ちは窓より後**（両辺が同じ倍率で縮む）。**前日終値 680 で
        # 140 円**——この倍率でちょうど転ぶ。
        ex_index = crash + 20
        rates = {"1401": [(dt.date(2015, 1, 5), _INDEX[ex_index].date(), 140.0)]}

        moved = measure_adjustment(_database({"1401": closes}), rates, _FIRST, _LAST)

        assert moved.ties > 0, "**同点の経路を通っていない。** 検査になっていない。"
        assert moved.lost == 0, "**丸めで転んだ分を「配当が作った」に数えている。**"

    def test_a_genuinely_deeper_fall_still_raises(self) -> None:
        """**同点を許しても、本当に深くなったら落ちる。**

        許容が広すぎれば、`CLAUDE.md`「落ちようのない検査を『合格』と
        読まない」に戻る。
        """
        import numpy as np_

        from stock_ai.data import schema

        def _wrong(prices, announced, *, base, symbol=""):
            counted = schema.DividendAdjustment()
            if not announced:
                return prices, counted
            when_of = [stamp.date() for stamp in prices.index]
            index_of = {day: i for i, day in enumerate(when_of)}
            factor = np_.ones(len(prices), dtype=float)
            for _published, ex_date, rate in announced:
                position = index_of.get(ex_date)
                if position is None or position == 0:
                    continue
                before = float(np_.asarray(base, dtype=float)[position - 1])
                if before <= 0 or rate <= 0:
                    continue
                factor[position:] *= 1.0 - rate / before  # ← 向きが逆
            frame = prices.copy()
            frame[CLOSE] = prices[CLOSE].to_numpy(dtype=float) * factor
            return frame, counted

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(schema, "dividend_adjusted", _wrong)
        try:
            database, rates = self._one(fall=0.14, yield_on_day=0.10)
            with pytest.raises(ValueError, match="向きが逆"):
                measure_adjustment(database, rates, _FIRST, _LAST)
        finally:
            monkeypatch.undo()

    def test_no_dividend_means_no_change(self) -> None:
        """**落ちようのない検査にしない。** 配当が無ければ集合は動かない。"""
        database, _rates = self._one(fall=0.30, yield_on_day=0.02)

        moved = measure_adjustment(database, {}, _FIRST, _LAST)

        assert moved.before == moved.after
        assert moved.lost == 0
        assert any("1件も変わらない" in line for line in moved.warnings())

    def test_the_breakdown_adds_up(self) -> None:
        """**足して合わない内訳は、作った時点で落ちる。**"""
        with pytest.raises(ValueError, match="向きが逆"):
            AdjustmentEffect(before=10, after=20, lost=0, ties=0, symbols=1)

    def test_a_consistent_breakdown_is_accepted(self) -> None:
        """**落ちようのない検査にしない。** 合う組み合わせは通る。"""
        effect = AdjustmentEffect(before=10, after=9, lost=1, ties=0, symbols=1)

        assert effect.before == 10
        assert effect.after == 9
