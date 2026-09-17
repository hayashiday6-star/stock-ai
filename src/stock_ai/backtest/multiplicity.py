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

#: **このプロジェクトが使う予算。2026-09-16 に決めた。**
#:
#: 判定を消費したのが 5 本、登録して未判定が 3 本。**複合型は組み合わせ1通り
#: につき1本**（`docs/PURPOSE.md`）なので、本数はこれから速く増える。20 なら
#: あと 12 本ぶん残る。
#:
#: **小さく取ると、超えた日に予算を取り直すことになる。** それは線を動かす
#: ことで、このプロジェクトがいちばん嫌う形である。**一度決めて、触らない。**
#:
#: 必要な `t` は **3.02** になる。高い。文献に載っているアノマリーの多くは、
#: この線では §0 を通らない。**それは補正が厳しすぎるのではなく、5% の
#: 家族単位の保証がそれだけ強いということである。**
#:
#: **緩めたくなったときに動かすのは、予算ではなく `FAMILY_ALPHA` のほうである。**
#: 予算を縮めるのは「何本試すつもりか」を偽ることになる。α を上げるのは
#: 「どれだけの誤りを許すか」を決め直すことで、**そちらは正直に書ける。**
HYPOTHESIS_BUDGET = 20


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


#: 帰無の下で `t` がどれだけ膨らむか。**測った値である。**
#:
#: 陰性対照を 400 回回して **SD 1.12**（1.00 のはず）だった（2026-09-17、
#: `research\陰性対照を400回.bat`）。Newey-West の標準誤差が 100ヶ月程度では
#: 小さめに出る——標準誤差が小さければ `t` は大きくなる。
#:
#: **これで `t ≥ 3.02` は 0.25% のつもりで、実際は 0.71% だった。3倍甘い。**
#:
#: **測って厳しくしている。** 結果を見てから緩めるのが禁じ手であって、こちらは
#: その逆である。しかも**説のデータではなく、対照から出た数**である。
#:
#: **月次・α・低ボラ universe で測った値である。** イベント型（#8・#5）は系列も
#: ラグも違うので、**同じ数字とは限らない。** そちらは別に測る。
MEASURED_INFLATION = 1.12

#: 膨張を測った回数。**3件しか出ない裾ではなく、SD を見ている。**
#:
#: 400 回なら SD の誤差は ±0.04 程度で、1.12 は 1.00 から明確に離れている。
#: 同じ回の `|t| ≥ 3.02` は 400 回中 **3 回**で、**その行からは何も言えない。**
INFLATION_RUNS = 400


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


def calibrated_t(
    budget: int,
    alpha: float = FAMILY_ALPHA,
    inflation: float = MEASURED_INFLATION,
) -> float:
    """実測の膨張を掛けた、**封印に使う線**。

    `required_t` は「`t` が素直に効いているなら」の値である。**効いていな
    かった**——陰性対照で SD 1.12 が出た（`MEASURED_INFLATION`）。

    **今後の封印にだけ当てる。** 封印済みの説の線は動かさない（#7 は
    `t ≥ 2.0` のままである）。

    Args:
        budget: 試すつもりの本数。
        alpha: 全体の有意水準。
        inflation: 帰無の下での `t` の SD。**1.0 なら `required_t` と同じ。**

    Raises:
        ValueError: ``inflation`` が 0 以下。
    """
    if inflation <= 0:
        raise ValueError(f"inflation must be positive; got {inflation}.")
    return required_t(budget, alpha) * inflation


def adjust(budget: int, alpha: float = FAMILY_ALPHA) -> Adjustment:
    """予算から、1本あたりの有意水準と必要な `t` を出す。"""
    return Adjustment(budget=budget, alpha=alpha / budget, required_t=required_t(budget, alpha))


def ladder(budgets: tuple[int, ...] = (1, 5, 10, 20, 50)) -> list[Adjustment]:
    """予算をいくつか並べて、**代償が見えるようにする。**

    1本だけ選んで出すと、その線がどこから来たのか分からなくなる。**並べれば、
    予算を増やすことの値段が読める。**
    """
    return [adjust(budget) for budget in budgets]
