"""月次で組み替えるときの暦を、1箇所で決める。

#7（低ボラ）と #9（買いにくい相場は高い）は、並べる材料が違うだけで、
**いつ組み替えて、いつ降りるかは同じ**である。

**`end` は「この日より後のデータを1つも使わない」という意味である。** 組み替え
日だけで切ると、その月の保有期間が `end` を越えて伸びる。#7 で実際に漏れた
——2013-12-31 で切ったつもりの推定期間が、リターンを 2014年1月まで含んでいた。

**ここが IS と OOS の排他を担保している。**
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from stock_ai.backtest.monthly_grid import build_grid
from stock_ai.backtest.pead import Period


def _calendar(days: int = 200) -> pd.DatetimeIndex:
    return pd.bdate_range("2024-01-01", periods=days)


def _formations(calendar: pd.DatetimeIndex) -> list[int]:
    """各月の最後の営業日の位置。"""
    month = calendar.to_period("M")
    return [
        int(positions[-1])
        for positions in (
            [index for index, value in enumerate(month) if value == period]
            for period in month.unique()
        )
    ]


class TestTheEndStopLooksAtTheExitDay:
    def test_a_rebalance_whose_exit_falls_after_end_is_dropped(self) -> None:
        """**組み替え日だけで切らない。** 保有が `end` を越えて伸びる。"""
        calendar = _calendar()
        formations = _formations(calendar)

        cut = calendar[formations[2]].date()
        grid = build_grid(calendar, formations, end=cut)

        for index, _position in grid.usable:
            assert calendar[grid.exit_at(index)].date() <= cut

    def test_without_an_end_every_rebalance_but_the_last_is_usable(self) -> None:
        calendar = _calendar()
        formations = _formations(calendar)

        grid = build_grid(calendar, formations)

        assert grid.months == len(formations) - 1

    def test_start_drops_the_earlier_rebalances(self) -> None:
        calendar = _calendar()
        formations = _formations(calendar)

        cut = calendar[formations[2]].date()
        grid = build_grid(calendar, formations, start=cut)

        assert all(calendar[position].date() >= cut for _index, position in grid.usable)


class TestTwoWindowsDoNotTouch:
    def test_an_is_window_and_an_oos_window_share_no_month(self) -> None:
        """**1日も重ねない。** 事前登録が求めているのはこれである。"""
        calendar = _calendar(400)
        formations = _formations(calendar)
        split = calendar[formations[5]].date()

        early = build_grid(calendar, formations, end=split)
        late = build_grid(calendar, formations, start=split + dt.timedelta(days=1))

        assert not {index for index, _ in early.usable} & {index for index, _ in late.usable}

    def test_the_earlier_window_never_reaches_into_the_later_one(self) -> None:
        """**退場日まで見る。** ここが漏れると、最後の1ヶ月が隣に食い込む。"""
        calendar = _calendar(400)
        formations = _formations(calendar)
        split = calendar[formations[5]].date()

        early = build_grid(calendar, formations, end=split)
        last = max(early.exit_at(index) for index, _ in early.usable)

        assert calendar[last].date() <= split


class TestRefusingWhatCannotBeBuilt:
    def test_fewer_than_two_rebalances(self) -> None:
        calendar = _calendar(10)

        with pytest.raises(ValueError, match="2つ未満"):
            build_grid(calendar, [0])

    def test_a_window_with_nothing_in_it(self) -> None:
        calendar = _calendar()
        formations = _formations(calendar)

        with pytest.raises(ValueError, match="1つも無い"):
            build_grid(calendar, formations, start=dt.date(2099, 1, 1))


class TestTheSameGridServesBothHypotheses:
    def test_the_period_filter_uses_real_dates_not_a_fraction(self) -> None:
        """`Period.IS` は**実在の年代**を指す。割合ではない。

        2024年しか無い暦に `IS` を当てると、1つも残らない。**それが正しい。**
        割合で切る実装なら何かしら残ってしまい、**どの年代を推定に使ったのかが
        暦によって変わる。**
        """
        calendar = _calendar(800)
        formations = _formations(calendar)

        assert build_grid(calendar, formations, Period.ALL).months > 0
        with pytest.raises(ValueError, match="1つも無い"):
            build_grid(calendar, formations, Period.IS)

    def test_the_month_count_is_what_a_judgement_would_have(self) -> None:
        """**期数はここから取る。** §0 の「判定に使える期数」がこれである。"""
        calendar = _calendar(400)
        formations = _formations(calendar)

        assert build_grid(calendar, formations).months == len(formations) - 1
