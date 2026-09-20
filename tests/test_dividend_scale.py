"""配当を落とす倍率が、**分割の尺度に引きずられない**こと。

円建ての配当額を**分割調整後**の終値で割ると、**分割より前の権利落ちが
分割比のぶん余計に落ちる**——1:10 なら利回り 1.0% が 10.0% になる。

**例外は出ない。** 見つかったのは監査が鳴ったからで、実データで
「窓の中の配当 +0.007%/件 に対し、抜けたのは +0.036%/件」と出た
（2026-09-20、ユーザーが「2倍なら二重と書いてあるのに 5倍だ」と指摘）。

**そのとき最初に立てた筋は外れだった**（`drag` が中央値で `removed` が
平均ではないか）。再現させたら鳴らなかったので捨てている。
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from stock_ai.data.schema import (
    ADJ_CLOSE,
    CLOSE,
    HIGH,
    LOW,
    OPEN,
    VOLUME,
    DividendAdjustment,
    dividend_adjusted,
    split_adjusted,
)

_BARS = 40
_INDEX = pd.bdate_range("2020-01-01", periods=_BARS, name="date")
_SPLIT_AT = 30
_EX_AT = 23


def _raw(split: bool) -> pd.DataFrame:
    """1:10 分割を挟む足／挟まない足。**生の終値と調整後終値の両方を持つ。**"""
    if split:
        closes = np.where(np.arange(_BARS) < _SPLIT_AT, 1_000.0, 100.0)
        adjusted = np.full(_BARS, 100.0)
    else:
        closes = np.full(_BARS, 1_000.0)
        adjusted = closes.copy()
    return pd.DataFrame(
        {
            OPEN: closes,
            HIGH: closes,
            LOW: closes,
            CLOSE: closes,
            ADJ_CLOSE: adjusted,
            VOLUME: np.full(_BARS, 1e6),
        },
        index=_INDEX,
    )


def _applied(split: bool, rate: float = 10.0) -> tuple[float, DividendAdjustment]:
    """先頭の足に当たった倍率から、抜いた利回りを逆算する。"""
    raw = _raw(split)
    plain = split_adjusted(raw)
    netted, counted = dividend_adjusted(
        plain,
        [(_INDEX[0].date(), _INDEX[_EX_AT].date(), rate)],
        base=raw[CLOSE].to_numpy(dtype=float),
    )
    before = plain[CLOSE].to_numpy(dtype=float)[0]
    after = netted[CLOSE].to_numpy(dtype=float)[0]
    return 1.0 - after / before, counted


class TestTheRatioDoesNotFollowTheSplitScale:
    """**額は円建てで、分割では変わらない。** 割る相手も調整前でなければ揃わない。"""

    def test_a_split_after_the_ex_date_does_not_change_what_is_removed(self) -> None:
        """**これが直る前は 10倍だった。** 分割の有無で答えが変わってはいけない。"""
        without, _ = _applied(split=False)
        with_split, _ = _applied(split=True)

        assert without == pytest.approx(with_split, rel=1e-12)

    def test_the_removed_amount_is_the_yield_on_the_unadjusted_close(self) -> None:
        """抜くのは ``額 ÷ 調整前の前日終値``。**1株10円 ÷ 1000円 = 1.0%。**"""
        with_split, _ = _applied(split=True)

        assert with_split == pytest.approx(10.0 / 1_000.0, rel=1e-12)

    def test_dividing_by_the_adjusted_close_is_what_went_wrong(self) -> None:
        """**直す前の式を、この盤面に当てて落ちることを見る。**

        テストが何も守っていないと困るので、**壊れたほうを実際に作る。**
        """
        raw = _raw(split=True)
        plain = split_adjusted(raw)
        # 直す前はここが `plain[CLOSE]` だった。
        wrong = 10.0 / plain[CLOSE].to_numpy(dtype=float)[_EX_AT - 1]
        right = 10.0 / raw[CLOSE].to_numpy(dtype=float)[_EX_AT - 1]

        assert wrong == pytest.approx(10 * right, rel=1e-12)
        assert _applied(split=True)[0] == pytest.approx(right, rel=1e-12)


class TestNothingIsSkippedSilently:
    """**黙って飛ばさない。** 以前は倍率が 1 以上だと1件も抜かずに通した。"""

    @staticmethod
    def _counted(rows) -> DividendAdjustment:
        raw = _raw(split=False)
        _frame, counted = dividend_adjusted(
            split_adjusted(raw), rows, base=raw[CLOSE].to_numpy(dtype=float)
        )
        return counted

    def test_each_reason_is_counted_on_its_own(self) -> None:
        day = _INDEX[_EX_AT].date()
        first = _INDEX[0].date()
        counted = self._counted(
            [
                (first, day, 10.0),  # 当たる
                (_INDEX[_SPLIT_AT].date(), day, 10.0),  # 公表が権利落ちより後
                (first, day, 0.0),  # 額 0
                (first, day, 99_999.0),  # 株価以上
                (first, dt.date(1999, 1, 4), 10.0),  # 足が無い
                (first, _INDEX[0].date(), 10.0),  # 前日が無い
            ]
        )

        assert counted.applied == 1
        assert counted.unpublished == 1
        assert counted.not_a_drop == 1
        assert counted.impossible == 1
        assert counted.not_in_frame == 1
        assert counted.no_base == 1
        assert counted.skipped == 5

    def test_an_impossible_ratio_is_said_out_loud(self) -> None:
        """**0 件でも数える。** 尺度を間違えるとここが静かに増える。"""
        counted = self._counted([(_INDEX[0].date(), _INDEX[_EX_AT].date(), 99_999.0)])

        assert any("前日終値以上" in line for line in counted.warnings())

    def test_a_clean_run_says_nothing(self) -> None:
        """**両向きに置く。** 常に鳴る旗は何も区別しない。"""
        counted = self._counted([(_INDEX[0].date(), _INDEX[_EX_AT].date(), 10.0)])

        assert counted.warnings() == []
        assert counted.skipped == 0

    def test_the_breakdown_adds_up(self) -> None:
        """銘柄ごとの内訳を足し合わせられること。"""
        one = DividendAdjustment(applied=2, impossible=1)
        two = DividendAdjustment(applied=3, no_base=4)

        both = one + two

        assert both.applied == 5
        assert both.impossible == 1
        assert both.no_base == 4
        assert both.skipped == 5


class TestTheBaseMustLineUp:
    """**渡し忘れが黙って通らないこと。** 既定を作ると、また尺度が混ざる。"""

    def test_a_base_of_the_wrong_length_is_refused(self) -> None:
        raw = _raw(split=False)
        with pytest.raises(ValueError, match="base の長さ"):
            dividend_adjusted(
                split_adjusted(raw),
                [(_INDEX[0].date(), _INDEX[_EX_AT].date(), 10.0)],
                base=np.ones(3),
            )

    def test_the_base_is_keyword_only_and_required(self) -> None:
        """**位置引数で渡せない。** 渡し忘れは `TypeError` で落ちる。"""
        import inspect

        signature = inspect.signature(dividend_adjusted)
        base = signature.parameters["base"]

        assert base.kind is inspect.Parameter.KEYWORD_ONLY
        assert base.default is inspect.Parameter.empty

    def test_the_length_is_checked_even_with_nothing_to_apply(self) -> None:
        """**配当が無くても長さは見る。** 配線のずれは、そこで出る。"""
        raw = _raw(split=False)
        with pytest.raises(ValueError, match="base の長さ"):
            dividend_adjusted(split_adjusted(raw), None, base=np.ones(3))
