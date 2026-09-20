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
    Exclusions,
    audit_exclusions,
    audit_holding_window,
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

    def test_a_zero_span_is_refused(self) -> None:
        with pytest.raises(ValueError, match="span must be at least 1"):
            measure_alignment(_database({}), {}, _FIRST, _LAST, span=0)


class TestWhetherTheCrashesShouldHaveBeenDropped:
    """**配当を戻して測り直す。** 戻しても急落なら、外すべきでなかった。"""

    @staticmethod
    def _one(fall: float, yield_on_day: float, offset: int) -> tuple[Database, dict, list]:
        """``offset`` 日目に配当を置いた急落を1件だけ作る。"""
        crash = _at("2015-06-10")
        drops = {crash - KNIFE_DAYS + 1 + step: 0.0 for step in range(KNIFE_DAYS)}
        # **下げを均等に割る。** 端に寄せると、戻す位置で答えが変わる。
        each = 1.0 - (1.0 - fall) ** (1.0 / KNIFE_DAYS)
        drops = dict.fromkeys(drops, each)
        ex_index = crash - KNIFE_DAYS + offset
        drops[ex_index] = 1.0 - (1.0 - drops.get(ex_index, 0.0)) * (1.0 - yield_on_day)
        closes = _walk(0, drops)
        when = _INDEX[crash].date()
        rates = {
            "1401": {
                _INDEX[ex_index].date(): ExDividend(
                    rate=closes[ex_index - 1] * yield_on_day, special=0.0
                )
            }
        }
        return _database({"1401": closes}), rates, [("1401", when)]

    def test_a_real_crash_is_flagged_as_wrongly_excluded(self) -> None:
        """配当 2% を戻しても −20% を超えるなら、外すべきでなかった。"""
        database, rates, excluded = self._one(fall=0.30, yield_on_day=0.02, offset=3)

        found = audit_exclusions(database, excluded, rates)

        assert found.still_qualifies == 1
        assert found.rescued == 0
        assert found.wrongly_excluded == 1.0

    def test_a_crash_the_dividend_made_is_flagged_as_correct(self) -> None:
        """**この検査が落ちる条件を作る。** 大きい配当なら、外して正しい。"""
        database, rates, excluded = self._one(fall=0.21, yield_on_day=0.12, offset=3)

        found = audit_exclusions(database, excluded, rates)

        assert found.rescued == 1
        assert found.still_qualifies == 0
        assert found.wrongly_excluded == 0.0

    def test_a_dividend_on_the_base_day_cannot_have_caused_it(self) -> None:
        """基準日の配当は比を1つも動かさない。**除外の窓が1日広い。**"""
        database, rates, excluded = self._one(fall=0.30, yield_on_day=0.02, offset=0)

        found = audit_exclusions(database, excluded, rates)

        assert found.outside_window == 1
        assert found.still_qualifies == 0
        assert any("1日広い" in line for line in found.warnings())

    def test_a_special_dividend_is_counted(self) -> None:
        crash = _at("2015-06-10")
        ex_index = crash - 2
        closes = _walk(0, {crash - KNIFE_DAYS + 1 + step: 0.07 for step in range(KNIFE_DAYS)})
        rates = {
            "1401": {
                _INDEX[ex_index].date(): ExDividend(
                    rate=closes[ex_index - 1] * 0.02, special=closes[ex_index - 1] * 0.015
                )
            }
        }
        database = _database({"1401": closes})

        found = audit_exclusions(database, [("1401", _INDEX[crash].date())], rates)

        assert found.special == 1

    def test_an_unpriced_dividend_is_undecided(self) -> None:
        """**判定できなかった件を、分母に入れない。**"""
        crash = _at("2015-06-10")
        ex_index = crash - 2
        closes = _walk(0, {crash - KNIFE_DAYS + 1 + step: 0.07 for step in range(KNIFE_DAYS)})
        # ありえない利回り（株価の 9 割）は読み違いとして落ちる。
        rates = {"1401": {_INDEX[ex_index].date(): ExDividend(rate=closes[0] * 9, special=0.0)}}

        found = audit_exclusions(
            _database({"1401": closes}), [("1401", _INDEX[crash].date())], rates
        )

        assert found.undecided == 1
        assert found.decided == 0
        assert found.wrongly_excluded is None

    def test_a_symbol_with_no_prices_is_undecided(self) -> None:
        found = audit_exclusions(_database({}), [("9999", dt.date(2015, 6, 10))], {})

        assert found.undecided == 1
        assert found.excluded == 1

    def test_the_months_are_counted(self) -> None:
        found = audit_exclusions(
            _database({}),
            [
                ("9999", dt.date(2015, 3, 30)),
                ("9998", dt.date(2015, 3, 31)),
                ("9997", dt.date(2015, 9, 29)),
            ],
            {},
        )

        assert dict(found.by_month) == {3: 2, 9: 1}


class TestTheZeroDividendCase:
    """**額 0 は「読めない」ではなく「落ちるものが無い」。**

    無配の公表にも `ExDate` は入る。`ex_dates_known_by` は額を見ないので、
    **落ちるものが無い日で急落を外していた**（2026-09-20）。
    """

    @staticmethod
    def _one(rate: float) -> tuple[Database, dict, list, dict]:
        crash = _at("2015-06-10")
        ex_index = crash - 2
        closes = _walk(0, {crash - KNIFE_DAYS + 1 + step: 0.07 for step in range(KNIFE_DAYS)})
        when = _INDEX[ex_index].date()
        rates = {"1401": {when: ExDividend(rate=rate, special=0.0)}}
        announced = {"1401": [(dt.date(2015, 1, 5), when)]}
        return _database({"1401": closes}), rates, [("1401", _INDEX[crash].date())], announced

    def test_a_zero_dividend_is_its_own_bucket(self) -> None:
        database, rates, excluded, announced = self._one(rate=0.0)

        found = audit_exclusions(database, excluded, rates, announced=announced)

        assert found.zero_rate == 1
        assert found.undecided == 0, "**0 を「判定できない」に落とさない。**"
        assert found.kept_by_mistake == 1

    def test_a_real_dividend_is_not_in_that_bucket(self) -> None:
        """**両向きに置く。** 常にそのバケットに入るなら区別していない。"""
        database, rates, excluded, announced = self._one(rate=20.0)

        found = audit_exclusions(database, excluded, rates, announced=announced)

        assert found.zero_rate == 0

    def test_a_revised_date_is_its_own_bucket(self) -> None:
        """**外したときの日が最終データに無い。** 「基準日」に化けさせない。"""
        crash = _at("2015-06-10")
        closes = _walk(0, {crash - KNIFE_DAYS + 1 + step: 0.07 for step in range(KNIFE_DAYS)})
        # 公表時は crash-2、最終データには入っていない。
        announced = {"1401": [(dt.date(2015, 1, 5), _INDEX[crash - 2].date())]}

        found = audit_exclusions(
            _database({"1401": closes}),
            [("1401", _INDEX[crash].date())],
            {"1401": {}},
            announced=announced,
        )

        assert found.revised == 1
        assert found.outside_window == 0, "**訂正を「基準日の配当」に化けさせない。**"


class TestTheBreakdownAddsUp:
    """**内訳が合わなければ、作った時点で落ちる。** `ExDateCoverage` と同じ作り。"""

    def test_a_breakdown_that_does_not_add_up_is_refused(self) -> None:
        with pytest.raises(ValueError, match="合わない"):
            Exclusions(
                excluded=819,
                still_qualifies=400,
                rescued=200,
                outside_window=0,
                zero_rate=0,
                revised=0,
                undecided=0,
                special=0,
                median_yield=0.02,
                by_month=(),
            )

    def test_a_breakdown_that_adds_up_is_accepted(self) -> None:
        """**落ちようのない検査にしない。** 合う組み合わせは通る。"""
        found = Exclusions(
            excluded=819,
            still_qualifies=400,
            rescued=200,
            outside_window=119,
            zero_rate=40,
            revised=10,
            undecided=50,
            special=3,
            median_yield=0.02,
            by_month=(),
        )

        assert found.decided == 759
        assert found.kept_by_mistake == 559
        assert found.wrongly_excluded == pytest.approx(559 / 759)


class TestDividendsInsideTheHoldingWindow:
    """**そちらは外していない。** ショートでは配当は払う側である。"""

    @staticmethod
    def _one(offset: int, yield_on_day: float = 0.02) -> tuple[Database, dict, list]:
        entry = _at("2015-06-10")
        ex_index = entry + offset
        closes = _walk(0, {ex_index: yield_on_day})
        rates = {
            "1401": {
                _INDEX[ex_index].date(): ExDividend(
                    rate=closes[ex_index - 1] * yield_on_day, special=0.0
                )
            }
        }
        return _database({"1401": closes}), rates, [("1401", _INDEX[entry].date())]

    def test_a_dividend_inside_the_window_is_counted(self) -> None:
        database, rates, kept = self._one(offset=2)

        found = audit_holding_window(database, kept, rates)

        assert found.with_ex_date == 1
        assert found.share == 1.0
        assert found.drag == pytest.approx(0.02, abs=0.001)

    def test_a_dividend_before_the_entry_is_not(self) -> None:
        """**急落側の配当を二重に数えない。** そちらは除外の担当である。"""
        database, rates, kept = self._one(offset=-2)

        found = audit_holding_window(database, kept, rates)

        assert found.with_ex_date == 0
        assert found.drag == 0.0

    def test_a_dividend_just_past_the_window_is_not(self) -> None:
        """**窓の外は数えない。** 境界を1つ作って確かめる。"""
        from stock_ai.backtest.knife import HOLDING

        database, rates, kept = self._one(offset=HOLDING + 1)

        found = audit_holding_window(database, kept, rates)

        assert found.with_ex_date == 0

    def test_a_big_enough_drag_warns(self) -> None:
        database, rates, kept = self._one(offset=2, yield_on_day=0.02)

        found = audit_holding_window(database, kept, rates)

        assert any("無視できない" in line for line in found.warnings())

    def test_a_clean_window_says_nothing(self) -> None:
        """**両向きに置く。** 常に鳴る旗は何も区別しない。"""
        database, rates, kept = self._one(offset=-2)

        assert audit_holding_window(database, kept, rates).warnings() == []


def test_the_crash_definition_is_not_copied_here() -> None:
    """**急落の定数は `knife` から取る。** 2つ持つと黙ってずれる。"""
    import inspect

    from stock_ai.backtest import ex_date_audit

    source = inspect.getsource(ex_date_audit)
    assert "KNIFE_DROP = " not in source
    assert "KNIFE_DAYS = " not in source
    assert ex_date_audit.KNIFE_DROP == KNIFE_DROP
    assert ex_date_audit.KNIFE_DAYS == KNIFE_DAYS
