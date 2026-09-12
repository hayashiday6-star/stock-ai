"""立花と J-Quants の日足を、重なる5年ぶんで日次に突き合わせる。

**継ぎ目の検査は1日しか見ていない。** 2021-09-01 が普通の1日に見えたことは、
その日に段差が無いことしか言っていない。**5年ぶんの毎日が合っているかは、
別の話である。**

| 検査 | 見る範囲 |
|---|---|
| 継ぎ目 | 1日 × 76銘柄 |
| これ | **5年 × 選んだ銘柄の全営業日** |

## いましかできない

立花は 2001年から、J-Quants の原本は 2021-09 から。**重なるのは5年ある。**

2026-09-22 に J-Quants を解約すると、片方が更新されなくなる——原本は残るが、
**「2つの生きた経路が同じことを言うか」を確かめる機会は無くなる。**

## 何を比べるか

**生の終値から比べる。** 両者が同じ公式の値を見ているはずの、いちばん素の
ところである。ここが合わないなら、調整の話をしても意味がない。

| | 立花 | J-Quants |
|---|---|---|
| 生の終値 | `pDPP` | `C` |
| 調整後 | `pDPPxK` | `C` × 後の `AdjFactor` の積 |
| 出来高 | — | `Vo` |

**相対で比べる。** 5,000円の銘柄と50円の銘柄に同じ絶対値の許容幅を当てると、
片方は素通りし、もう片方はほぼ全部が食い違いになる。

**件数だけでなく、いちばん悪い日を出す。** 1円のまるめが100日あることと、
1日だけ倍半分になることは、件数では同じに見える。

**出来高は比べない。** 立花と J-Quants で立会外や自己売買の扱いが同じとは
限らず、合わなくてもおかしくない。**合わないことを不具合と呼べない値を
並べても、警告が1本増えるだけである。**
"""

from __future__ import annotations

import dataclasses
import datetime as dt

import pandas as pd

from stock_ai.core.logging import get_logger
from stock_ai.data.schema import ADJ_CLOSE, CLOSE

logger = get_logger(__name__)

#: 相対でこれを超えたら「違う」と数える。
#:
#: 0.1% は、100円の銘柄で 0.1円。まるめでは届かず、値そのものの違いなら届く。
DEFAULT_TOLERANCE = 0.001


@dataclasses.dataclass
class DailyMatch:
    """1銘柄ぶんの突き合わせ。"""

    symbol: str
    days: int = 0
    """両方にある日。**ここだけを比べる。**"""

    close_differs: int = 0
    adjusted_differs: int = 0
    only_first: int = 0
    """片方にしかない日。**食い違いではない。** 別に数える。"""

    only_second: int = 0
    worst: float = 0.0
    """生の終値の、いちばん大きい相対差。"""

    worst_on: dt.date | None = None

    @property
    def agrees(self) -> bool:
        """生の終値が、比べたすべての日で一致したか。"""
        return self.days > 0 and self.close_differs == 0


def compare_daily(
    first: pd.DataFrame,
    second: pd.DataFrame,
    symbol: str,
    tolerance: float = DEFAULT_TOLERANCE,
) -> DailyMatch:
    """2つの日足を、**重なる日だけ**突き合わせる。

    片方にしかない日は食い違いに数えない。**立花は 2001年から、原本は
    2021-09 から**なので、素直に引き算すると全部が食い違いになる。

    Args:
        first: 一方の日足（日付を索引に持つ）。
        second: もう一方。
        symbol: 記録用の銘柄コード。
        tolerance: 相対でこれを超えたら「違う」と数える。

    Returns:
        :class:`DailyMatch`。
    """
    match = DailyMatch(symbol=symbol)
    if first.empty or second.empty:
        match.only_first = len(first)
        match.only_second = len(second)
        return match

    shared = first.index.intersection(second.index)
    match.days = len(shared)
    match.only_first = len(first.index.difference(second.index))
    match.only_second = len(second.index.difference(first.index))
    if match.days == 0:
        return match

    left, right = first.loc[shared], second.loc[shared]
    for column, counter in ((CLOSE, "close_differs"), (ADJ_CLOSE, "adjusted_differs")):
        if column not in left or column not in right:
            continue
        base = right[column].abs()
        # **相対で比べる。** 0 や欠測で割らない——割ると、値の無い日が
        # 「無限に違う」になって全部を埋める。
        usable = base > 0
        gap = (left[column][usable] - right[column][usable]).abs() / base[usable]
        setattr(match, counter, int((gap > tolerance).sum()))
        if column == CLOSE and len(gap):
            match.worst = float(gap.max())
            match.worst_on = gap.idxmax().date()
    return match


def summarise(matches: list[DailyMatch]) -> str:
    """1行のまとめ。"""
    if not matches:
        return "突き合わせた銘柄が無い。"
    days = sum(m.days for m in matches)
    if days == 0:
        return f"{len(matches)} 銘柄を見たが、**重なる日が1日も無い。**"
    off = [m for m in matches if not m.agrees]
    worst = max(matches, key=lambda m: m.worst)
    return (
        f"{len(matches)} 銘柄・のべ {days:,} 日を比べ、"
        f"生の終値が食い違った銘柄は {len(off)}。"
        f"いちばん悪い日で {worst.worst:.2%}（{worst.symbol} {worst.worst_on}）"
    )
