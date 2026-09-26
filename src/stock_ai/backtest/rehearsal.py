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
import datetime as dt
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


def placebo_events(
    days: Sequence[dt.date],
    symbols: Sequence[str],
    count: int,
    seed: int = SEED,
) -> list[tuple[str, dt.date]]:
    """日と銘柄を乱数で選んで、イベントの並びを作る。**イベント型の管の対照。**

    月次の盤面で測った膨張（`MEASURED_INFLATION`）は、**イベント型には当ては
    まらないかもしれない。** #8・#5 は系列がイベント日ごとで、Newey-West の
    ラグも保有日数に取ってある。**別の管には別の数字がありうる。**

    **日の固まり方は本物に合わせない。** 合わせたければ本物のイベント日を
    渡す——ここは「同じ日数・同じ件数で、中身だけ乱数」を作る。

    Args:
        days: 選んでよい日。
        symbols: 選んでよい銘柄。
        count: 作るイベント数。
        seed: 乱数の種。

    Returns:
        ``(銘柄, 日)``。**重複しうる**——本物も同じ日に複数出る。

    Raises:
        ValueError: 日か銘柄が空、または ``count`` が 1 未満。
    """
    if not days or not symbols:
        raise ValueError("日か銘柄が空では、イベントを作れない。")
    if count < 1:
        raise ValueError(f"count must be at least 1; got {count}.")
    rng = np.random.default_rng(seed)
    picked_days = rng.integers(0, len(days), size=count)
    picked_symbols = rng.integers(0, len(symbols), size=count)
    return [
        (symbols[int(symbol)], days[int(day)])
        for day, symbol in zip(picked_days, picked_symbols, strict=True)
    ]


def placebo_windows(
    month_ends: Sequence[int],
    length: int,
    seed: int = SEED,
) -> list[tuple[int, int]]:
    """窓の位置を乱数に差し替える。**日次の暦の管の対照。**

    **リターンは本物のまま。** 動かすのは「窓の内か外か」のラベルだけである
    ——月次の対照が signal だけを乱数にしたのと同じ形。

    **偽の窓は、本物の窓の外に置く。** 重ねると本物の効果が漏れ込み、
    「何も無いときの分布」にならない。

    Args:
        month_ends: 月の最終営業日の位置。
        length: 窓の長さ（営業日）。
        seed: 乱数の種。

    Returns:
        月替わりごとの ``(開始位置, 長さ)``。**先頭は使われないので本物のまま。**

    Raises:
        ValueError: ``length`` が 1 未満、または月末が2つ未満。
    """
    if length < 1:
        raise ValueError(f"length must be at least 1; got {length}.")
    if len(month_ends) < 2:
        raise ValueError("月末が2つ未満。窓を差し替えられない。")

    rng = np.random.default_rng(seed)
    built: list[tuple[int, int]] = [(month_ends[0], length)]
    for index in range(1, len(month_ends)):
        # 本物の窓の外に収まる範囲。前の窓が終わった翌日から、今回の窓の前日まで。
        low = month_ends[index - 1] + length
        high = month_ends[index] - length
        begin = int(rng.integers(low, high + 1)) if high >= low else low
        built.append((begin, length))
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


# --- 陽性対照 -----------------------------------------------------------------
#
# **陰性対照は「何も無いときに合格を出さないか」しか見ていない。** 逆の問い
# ——**在るときに合格を出せるか**——は一度も試していなかった（2026-09-26、
# LLM Council の議長が指摘し、リポジトリで確かめた）。
#
# 合格 0 が「効果が無いから」なのか「検出器が厳しすぎるから」なのかは、
# 陰性対照だけでは区別できない。**大きさの分かった効果を埋め、同じ管に通して、
# 合格する割合が予測どおりかを見る。**

#: 埋める効果。**要る情報比の何倍か**（2026-09-26、測る前に決めた。ユーザーが承認）。
#:
#: **出典は無い。決めの値である。** 1.0 は、合格が五分五分になる点である
#: ——要る情報比は「期待される `t` がちょうど線に乗る」大きさだからである
#: （`power.required_information_ratio`）。
POSITIVE_MULTIPLES = (0.0, 0.5, 1.0, 1.25, 1.5)

#: 伝達率（管から戻ってきた効果 ÷ 埋めたつもりの効果）がこの外なら、**効果が
#: 途中で削られているか、膨らんでいる。** 出典は無い。決めの値である。
TRANSMISSION_BAND = (0.9, 1.1)

#: 合格の割合が、予測からこの標準誤差の倍数より離れたら、**線か標準誤差の
#: 見積もりがずれている。** 出典は無い。決めの値である。
PASS_BAND_SE = 3.0

#: m ごとに回す回数（既定）。200回・五分五分なら ±3標準誤差が ±約11ポイント。
POSITIVE_RUNS = 200


def rank_gap(quantiles: int) -> float:
    """順位（0〜1）が一様なとき、**上の分位と下の分位の平均順位の差**。

    上の分位の平均は ``1 − 1/(2q)``、下は ``1/(2q)`` なので差は ``1 − 1/q``。
    五分位なら 0.8。**分位の組み方を書き直さない**——ここは理屈の値で、管が
    実際に組んだ分位と突き合わせるための、別の切り口である。

    Raises:
        ValueError: ``quantiles`` が 2 未満。
    """
    if quantiles < 2:  # noqa: PLR2004 - 上と下の2つが要る
        raise ValueError(f"quantiles must be at least 2; got {quantiles}.")
    return 1.0 - 1.0 / quantiles


def monthly_effect(information_ratio: float, sd: float, periods_per_year: int = 12) -> float:
    """年率の情報比を、1期あたりの効果に直す。``情報比 × SD ÷ √(年あたりの期数)``。

    `power.required_information_ratio` の「情報比 ``= μ√r / σ``」を ``μ`` に
    ついて解いたもの。
    """
    return information_ratio * sd / math.sqrt(periods_per_year)


def planted_sections(
    sections: Sequence[Sequence[tuple[tuple[float, ...], float]]],
    strength: float,
    seed: int = SEED,
) -> list[list[tuple[float, float]]]:
    """陰性対照と同じ乱数の signal に、**大きさの分かった効果を埋める。**

    翌月リターンに ``strength × (その月の signal の順位 − 0.5)`` を足す。順位は
    0〜1。**signal と種は陰性対照と同じ**なので、``strength=0`` なら陰性対照と
    同じ数字が出るはずである——**0 でも足し算の経路は通す**（特別扱いすると、
    その一致が確かめにならない）。

    Args:
        sections: 盤面の月ごとの断面。
        strength: 順位 0 と 1 のあいだで開くリターンの差。
        seed: 乱数の種。

    Returns:
        ``(乱数 signal, 効果を足した翌月リターン)`` の断面。
    """
    built = placebo_sections(sections, seed=seed)
    planted: list[list[tuple[float, float]]] = []
    for month in built:
        count = len(month)
        if count < 2:  # noqa: PLR2004 - 順位を振るには2つ要る
            planted.append(list(month))
            continue
        order = sorted(range(count), key=lambda position: month[position][0])
        rank = [0.0] * count
        for place, position in enumerate(order):
            rank[position] = place / (count - 1)
        planted.append(
            [
                (signal, forward + strength * (rank[position] - 0.5))
                for position, (signal, forward) in enumerate(month)
            ]
        )
    return planted


def predicted_share(
    effect: float, errors: Sequence[float], line: float, null_spread: float
) -> float:
    """``t ≥ line`` になる割合の予測。回ごとの ``Φ((効果 ÷ 標準誤差 − 線) ÷ 帰無の SD)`` の平均。

    **帰無の下の `t` の SD（陰性対照で測った値）を使う。** 1.0 と置くと、線に
    掛けた膨張と同じものを予測だけ落とすことになる。標準誤差が 0 以下の回は
    数えない。

    Raises:
        ValueError: ``null_spread`` が 0 以下。
    """
    if null_spread <= 0:
        raise ValueError(f"null_spread must be positive; got {null_spread}.")
    shares = [
        _normal_cdf((effect / error - line) / null_spread)
        for error in errors
        if error > 0 and math.isfinite(error)
    ]
    return fmean(shares) if shares else float("nan")


def share_band(predicted: float, runs: int) -> float:
    """実測の割合が、予測からどれだけ離れたら「ずれている」と言うか。

    ``PASS_BAND_SE × √(p(1−p)/回数)``。**ただし 1回ぶん（1/回数）より狭くしない**
    ——予測がほぼ 0 のとき、1回の合格だけで「壊れている」と言わないため。
    **この下限も決めの値である。**
    """
    if runs < 1:
        raise ValueError(f"runs must be at least 1; got {runs}.")
    spread = PASS_BAND_SE * math.sqrt(max(predicted * (1.0 - predicted), 0.0) / runs)
    return max(spread, 1.0 / runs)


@dataclasses.dataclass(frozen=True)
class PositiveRow:
    """1つの大きさで、埋めた効果が判定まで届いたか。"""

    multiple: float
    """要る情報比の何倍を埋めたか。"""

    information_ratio: float
    """埋めた年率の情報比（IS の SD で換算）。"""

    effect: float
    """埋めたつもりの、1期あたりの効果。"""

    transmission: float | None
    """OOS で戻ってきた効果 ÷ 埋めたつもりの効果（回の平均）。0 を埋めた行は ``None``。"""

    gate_passes: bool
    """IS で §0 を通るか（IS で戻ってきた効果を、見込みの下限として当てた）。"""

    predicted: float
    measured: float
    runs: int

    @property
    def band(self) -> float:
        """:func:`share_band`。"""
        return share_band(self.predicted, self.runs)

    def problems(self) -> list[str]:
        """**壊れていると言う条件**（測る前に決めた）に当たったもの。"""
        found: list[str] = []
        low, high = TRANSMISSION_BAND
        if self.transmission is not None and not low <= self.transmission <= high:
            found.append(
                f"伝達率 {self.transmission:.3f} が {low}〜{high} の外"
                "——**効果が途中で削られているか、膨らんでいる。**"
            )
        if not math.isfinite(self.predicted) or abs(self.measured - self.predicted) > self.band:
            found.append(
                f"合格 {self.measured:.1%} が予測 {self.predicted:.1%} から "
                f"±{self.band:.1%} の外——**線か標準誤差の見積もりがずれている。**"
            )
        return found
