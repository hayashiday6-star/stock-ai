"""月次で組み替えるときの**暦**を、1箇所で決める。

## なぜ切り出したか

#7（低ボラ）と #9（買いにくい相場は高い）は、並べる材料が違うだけで、
**いつ組み替えて、いつ降りるかは同じである。** 2つ持つと、片方だけ直したときに
気付けない——このプロジェクトが繰り返し踏んでいる型である。

## `end` は「この日より後のデータを1つも使わない」

**組み替え日だけで切らない。** 切ると、その月の保有期間が `end` を越えて伸びる。
実際 #7 で、2013-12-31 で切ったつもりの推定期間の最後の1ヶ月が、**リターンを
2014年1月まで**含んでいた——判定期間の最初の月である。1/138 の重みでしかないが、
**止め具が漏れていること自体が問題**なので、退場日まで見る。

**ここが IS と OOS の排他を担保している。** 事前登録が「1日も重ねない」と書いて
いるのは、この処理のことである。
"""

from __future__ import annotations

import dataclasses
import datetime as dt

import pandas as pd

from stock_ai.backtest.pead import Period
from stock_ai.core.logging import get_logger

logger = get_logger(__name__)


@dataclasses.dataclass
class MonthlyGrid:
    """組み替え日の一覧と、その暦。"""

    calendar: pd.DatetimeIndex
    formations: list[int]
    """組み替え日の、暦の中での位置。"""

    usable: list[tuple[int, int]]
    """使える組み替え（`formations` の添字, 暦の位置）。"""

    @property
    def months(self) -> int:
        """使える月数。**判定に使える期数はこれである。**"""
        return len(self.usable)

    def exit_at(self, index: int) -> int:
        """その組み替えから降りる日の、暦の中での位置。"""
        return self.formations[index + 1] + 1


def build_grid(
    calendar: pd.DatetimeIndex,
    formations: list[int],
    period: Period = Period.ALL,
    start: dt.date | None = None,
    end: dt.date | None = None,
) -> MonthlyGrid:
    """使える組み替え日を決める。

    Args:
        calendar: ベンチマークの暦。**銘柄ごとの暦を使わない**——銘柄によって
            穴の位置が違うので、月の切れ目がずれる。
        formations: 組み替え日の位置。
        period: IS / OOS / ALL。
        start: この日より前の組み替え日を使わない。
        end: **この日より後のデータを1つも使わない。** 組み替え日だけでなく、
            その月の退場日まで見る。

    Returns:
        :class:`MonthlyGrid`。

    Raises:
        ValueError: 組み替え日が2つ未満か、条件に合う組み替えが1つも無い。
    """
    if len(formations) < 2:
        raise ValueError("組み替え日が2つ未満。月次リバランスを作れない。")

    usable = [
        (index, position)
        for index, position in enumerate(formations[:-1])
        if period.contains(calendar[position].date())
        and (start is None or calendar[position].date() >= start)
        and (
            end is None
            or (
                formations[index + 1] + 1 < len(calendar)
                and calendar[formations[index + 1] + 1].date() <= end
            )
        )
    ]
    if not usable:
        raise ValueError("指定した期間に組み替え日が1つも無い。")
    return MonthlyGrid(calendar=calendar, formations=formations, usable=usable)
