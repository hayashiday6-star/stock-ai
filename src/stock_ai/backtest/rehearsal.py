"""陰性対照 — **何も無いときに、この仕組みは合格を出すか。**

このプロジェクトには対照が1本も無い。関門も判定の当てはめも、**本物のデータの
上でしか試していない。**

ここは乱数で作った signal を、**本物と同じ管**に通す。別の管を作ったら、
確かめたことにならない——通すのは `build_estimators` → `estimate_power` →
判定の当てはめ、という実際に使っている経路そのものである。

### 本物のリターンを使う

**signal だけを乱数にする。** 月も universe もリターンも本物のままである。
全部を乱数にすると、重なりも自己相関も消えて、**いちばん確かめたい部分が
消える。**

### 説ではない

世界について何も主張していないので、**多重検定の予算に入らない。** `§0` も
通さない——§0 は「一度きりの判定を弱い設計に使わない」ための関門で、
**判定を消費しないものには守るものが無い。**

### 1回では校正できない

`t ≥ 3.02` を越える確率は 0.125%（片側）である。400回回しても期待値は 0.5 回で、
**出る/出ないだけでは何も分からない。**

**代わりに `t` の分布そのものを見る。** 帰無の下で `t` の SD は 1.0 のはずで、
1.15 なら補正は足りていない——`t ≥ 3.02` の実際の意味が α=0.25% ではなく
0.6% 前後になる。**|t| ≥ 1.96 の割合（期待 5%）なら、400回で測れる。**
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Sequence
from statistics import fmean, stdev

import numpy as np

from stock_ai.core.logging import get_logger

logger = get_logger(__name__)

#: 既定の種。**固定する。** 変えれば違う答えが出るので、記録に残す。
SEED = 20260917

#: OOS の種を IS からずらす幅。
#:
#: **同じ種を両方に使わない。** 乱数の流れが共有されるので、2つが独立な引きに
#: ならない。最初はそうしていて、IS +1.24・OOS +1.29 が揃って見えた——**偶然か
#: 共有のせいかを区別できなかった**（2026-09-17）。
#:
#: `spawn` ではなく定数のずらしにするのは、**種を記録すれば手で再現できる**
#: ようにするためである。
OOS_OFFSET = 1_000_000


def oos_seed(seed: int = SEED) -> int:
    """OOS の種。**IS と同じ流れを使わない。**"""
    return seed + OOS_OFFSET


#: 帰無の下で `|t|` がこれを越える割合。**補正なしの両側5%。**
PLAIN_LEVEL = 1.96

#: 帰無の下で `t` の SD がこれを超えたら、補正が足りていない。
#:
#: **1.10 に根拠は無い。** 「1割ずれたら見過ごさない」という目安であって、
#: 測って出した値ではない。**そう書いておく。**
CALIBRATION_LIMIT = 1.10


def placebo_sections(
    sections: Sequence[Sequence[tuple[tuple[float, ...], float]]],
    seed: int = SEED,
) -> list[list[tuple[float, float]]]:
    """Signal を乱数に差し替える。**リターンはそのまま。**

    Args:
        sections: 盤面の月ごとの断面。
        seed: 乱数の種。

    Returns:
        ``(乱数 signal, 本物の翌月リターン)`` の断面。
    """
    rng = np.random.default_rng(seed)
    built: list[list[tuple[float, float]]] = []
    for month in sections:
        draws = rng.normal(size=len(month))
        built.append(
            [
                (float(value), forward)
                for value, (_signals, forward) in zip(draws, month, strict=True)
            ]
        )
    return built


@dataclasses.dataclass(frozen=True)
class Calibration:
    """帰無の下での `t` の分布。**まれな裾ではなく、形を見る。**"""

    runs: int
    spread: float
    """`t` の SD。**帰無なら 1.0 のはず。**"""

    mean: float
    plain_share: float
    """``|t| >= 1.96`` の割合。**期待 5%。400回で測れる。**"""

    strict_share: float
    """``|t| >= 3.02`` の割合。**期待 0.25%。400回では測れない。**"""

    worst: float

    @property
    def calibrated(self) -> bool:
        """`t` が素直に効いているか。"""
        return self.spread <= CALIBRATION_LIMIT

    def implied_level(self, target: float) -> float:
        """実測の散らばりの下で、``t >= target`` が本当は何%か。

        **`t` の SD が 1 より大きければ、線は見かけより甘い。**
        """
        if self.spread <= 0:
            return float("nan")
        return 2.0 * (1.0 - _normal_cdf(target / self.spread))

    def warnings(self) -> list[str]:
        """気付かなくても目に入るべきこと。"""
        found: list[str] = []
        if not self.runs:
            return ["**1回も回していない。**"]
        if not self.calibrated:
            found.append(
                f"**帰無の下で `t` の SD が {self.spread:.2f} ある**（1.0 のはず）。"
                "**補正が足りていない。** 判定の線は見かけより甘い。"
            )
        if self.plain_share > 0.10:
            found.append(
                f"**|t| ≥ 1.96 が {self.plain_share:.1%} 出ている**（期待 5%）。"
                "同じことを別の切り口が言っている。"
            )
        if self.runs < 200:
            found.append(
                f"**{self.runs} 回では形が読めない。** |t| ≥ 1.96 の割合を測るには200回以上が要る。"
            )
        return found


def _normal_cdf(value: float) -> float:
    """標準正規の累積分布。**表を引かずに済ませる。**"""
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def calibrate(scores: Sequence[float], target: float) -> Calibration:
    """回した `t` の並びから :class:`Calibration` を作る。

    Args:
        scores: 1回ごとの `t`。
        target: 合格の線。

    Returns:
        :class:`Calibration`。
    """
    if not scores:
        return Calibration(0, float("nan"), float("nan"), float("nan"), float("nan"), float("nan"))
    kept = [value for value in scores if math.isfinite(value)]
    if len(kept) < 2:
        return Calibration(
            len(kept), float("nan"), float("nan"), float("nan"), float("nan"), float("nan")
        )
    result = Calibration(
        runs=len(kept),
        spread=stdev(kept),
        mean=fmean(kept),
        plain_share=sum(1 for value in kept if abs(value) >= PLAIN_LEVEL) / len(kept),
        strict_share=sum(1 for value in kept if abs(value) >= target) / len(kept),
        worst=max(kept, key=abs),
    )
    logger.info(
        "陰性対照: %d 回、t の SD %.2f、|t|>=1.96 が %.1f%%",
        result.runs,
        result.spread,
        result.plain_share * 100,
    )
    return result
