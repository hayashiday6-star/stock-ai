"""裾の読み方。**平均が同じでも、ここが違えば別の戦略である。**

分位ロングショート（`QuantileSeries`）も、暦の差（`TurnOfMonthSeries`）も、
**持っているのは「1期ごとの値の並び」**だけである。裾の読み方は同じ式なので、
**2つ書かない。**

**いちばん悪い月だけを見ない。** 1点だと、1回の事故なのかそういう性質なのかが
分からない——`CLAUDE.md`「1点から確かさを名乗らない」と同じ形である。
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from stock_ai.core.logging import get_logger

logger = get_logger(__name__)

#: 裾として見る割合。**下から5%。**
#:
#: 5% は「よくある裾の取り方」であって、**測って決めた値ではない。**
#: そう書いておく。
DEFAULT_TAIL_SHARE = 0.05


def worst(values: Sequence[float]) -> float:
    """いちばん悪かった期。**無ければ `nan`**（0 ではない）。"""
    return min(values) if values else float("nan")


def left_tail(values: Sequence[float], share: float = DEFAULT_TAIL_SHARE) -> float:
    """下位 ``share`` の期の平均。**1点ではなく帯で見る。**

    Args:
        values: 1期ごとの値。
        share: 下から取る割合。

    Returns:
        下位の平均。期が無ければ ``nan``。

    Raises:
        ValueError: ``share`` が 0〜1 の外。
    """
    if not 0.0 < share <= 1.0:
        raise ValueError(f"share must be in (0, 1]; got {share}.")
    if not values:
        return float("nan")
    ordered = sorted(values)
    take = max(1, round(len(ordered) * share))
    return float(np.mean(ordered[:take]))


def hit_rate(values: Sequence[float]) -> float:
    """正だった期の割合。**平均だけで語らない。**"""
    if not values:
        return float("nan")
    return sum(1 for value in values if value > 0) / len(values)
