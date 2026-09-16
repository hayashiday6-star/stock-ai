"""何本試したかを、合格の線に反映させる。

## なぜ要るか

**当たりを引こうとした回数が多いほど、まぐれ当たりも増える。** 20本試せば、
本当は何も無くても1本は「両側5%」を越える。`docs/PURPOSE.md` の「合格判定では
多重検定を考慮する」はそのことである。

## 本数の数え方

**判定を消費した説だけを数える。** 補正したいのは「何回当たりを引こうとしたか」
で、**回さなかった説は当たりを引こうとしていない。**

登録しただけの説を数えると甘くなる方向ではなく、**厳しくなりすぎる方向**に
外れる——回していない説のぶんまで線が上がる。どちらもまずいが、こちらは
「本当は在る効果を落とす」側である。

## 線をあとから動かさない

**すでに封印した説の線は、絶対に動かさない。** #7 は `t ≥ 2.0` で封印してある。
本数が増えたからといって遡って上げるのは、判定が出たあとに基準を変えることで
ある——`docs/PURPOSE.md` が禁じている当のものである。

だから補正は**これから封印する説にだけ**掛かる。そして掛け方そのものを、
**封印前に事前登録へ書く。** 本数は時間とともに増えるので、「いま何本か」で
線を決めると、封印のたびに線が動いてしまう。**先に予算を決めて割る。**

## 予算で割る

「これから全部で何本試すつもりか」を先に決め、`0.05` をその本数で割る
（Bonferroni）。本数が増えても線は動かない。**予算を使い切ったら、そこで
一度立ち止まる**——そこが、この方式が答えを出す場所である。
"""

from __future__ import annotations

import dataclasses
from statistics import NormalDist

#: 補正しないときの両側の有意水準。`power.TARGET_T = 2.0` はこれに対応する。
FAMILY_ALPHA = 0.05


@dataclasses.dataclass
class Adjustment:
    """ある予算のもとでの、合格に必要な線。"""

    budget: int
    """これから全部で何本試すつもりか。"""

    alpha: float
    """1本あたりに割り当てた両側の有意水準。"""

    required_t: float
    """必要な `t`。**補正しなければ 2.0 である。**"""

    @property
    def cost_in_t(self) -> float:
        """補正で線がどれだけ上がるか。**代償を数字で見せる。**"""
        return self.required_t - required_t(1)

    def summary(self) -> str:
        """1行のまとめ。"""
        return (
            f"予算 {self.budget} 本なら、1本あたり α={self.alpha:.4f}、"
            f"必要な t は {self.required_t:.2f}"
            f"（補正なしの {required_t(1):.2f} より {self.cost_in_t:+.2f}）。"
        )


def required_t(budget: int, alpha: float = FAMILY_ALPHA) -> float:
    """``budget`` 本に ``alpha`` を割ったときの、両側の臨界値。

    ``budget=1`` なら補正なしで、`power.TARGET_T` とほぼ同じ 1.96 になる。

    Args:
        budget: 試すつもりの本数。1 以上。
        alpha: 全体の有意水準。

    Raises:
        ValueError: ``budget`` が 1 未満、または ``alpha`` が 0〜1 の外。
    """
    if budget < 1:
        raise ValueError(f"budget must be at least 1; got {budget}.")
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be between 0 and 1; got {alpha}.")
    return NormalDist().inv_cdf(1.0 - alpha / budget / 2.0)


def adjust(budget: int, alpha: float = FAMILY_ALPHA) -> Adjustment:
    """予算から、1本あたりの有意水準と必要な `t` を出す。"""
    return Adjustment(budget=budget, alpha=alpha / budget, required_t=required_t(budget, alpha))


def ladder(budgets: tuple[int, ...] = (1, 5, 10, 20, 50)) -> list[Adjustment]:
    """予算をいくつか並べて、**代償が見えるようにする。**

    1本だけ選んで出すと、その線がどこから来たのか分からなくなる。**並べれば、
    予算を増やすことの値段が読める。**
    """
    return [adjust(budget) for budget in budgets]
