"""推定量の校正：分位ソートと横断回帰を、同じ断面から作る。

**これは判定ではない。** #6 と #7 は判定済みで閉じている。ここで t を計算する
のは推定量の校正であって、説の合否ではない（`docs/HYPOTHESES.md` の宣言）。

### なぜ SD 比ではなく t 比か

分位ソートの α は上位20%と下位20%の差である。断面が正規なら上位20%の平均は
約 +1.40σ、下位は −1.40σ なので、

    分位スプレッド ≒ 2.8 × 1σチルトのリターン

**推定量を変えると SD も効果も一緒に縮む。** SD だけを揃えて比を掛け、そこに
文献の分位スプレッドの効果量を当てると、**2.8倍の改善が無料で出たように
見える。** このプロジェクトが繰り返し避けてきた型が、SD 側ではなく効果側に
出る形である。

**t は無次元なのでこの取り違えが起きない。** 比べるのは t にする。

### 何と何を比べるか

**ロングショートどうしを比べる。** 分位1−分位5 と 1σチルトは、どちらも
建玉の合計がゼロで、市場感応度がほぼ無い。**β の扱いが両者で違うと、比が
「推定量の差」ではなく「指標の差」を含む。**

ロングオンリー側も出すが、そちらは β を引いていないので #7 の α（t 1.70）
とは直接比べられない。**比べられないものを比べない**ために、出力で分ける。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from stock_ai.core.logging import get_logger

logger = get_logger(__name__)

#: 断面の標準偏差がこれを下回る月は落とす。**割り算が暴れる。**
MIN_DISPERSION = 1e-12


def zscores(values: Sequence[float]) -> list[float] | None:
    """平均0・標準偏差1に揃える。揃えられなければ ``None``。

    **揃わない月を 0 で埋めない。** 全銘柄が同じ値なら、その月に情報は無い。
    0 を入れるとチルトのリターンが 0 になり、「効果が無かった月」として
    平均に混ざる。落とした月として数えられる形で返す。
    """
    count = len(values)
    if count < 2:
        return None
    mean = sum(values) / count
    variance = sum((value - mean) ** 2 for value in values) / (count - 1)
    if variance <= MIN_DISPERSION:
        return None
    sd = math.sqrt(variance)
    return [(value - mean) / sd for value in values]


def tilt_return(signals: Sequence[float], forwards: Sequence[float]) -> float:
    """1σチルトのリターン。**横断回帰の傾きそのものである。**

    標準化した説明変数への OLS の傾きは ``Σ z·r / Σ z²`` で、これは重み
    ``w = z / Σz²`` を持つポートフォリオのリターンに等しい。``Σz = 0`` なので
    **建玉の合計はゼロ**——市場感応度がほぼ無く、β を引く必要がない。

    Args:
        signals: 標準化済みの signal。大きいほど買う側。
        forwards: 同じ並びの翌月リターン。

    Returns:
        1σ傾けたときのリターン。

    Raises:
        ValueError: 長さが違う、または signal の二乗和が 0。
    """
    if len(signals) != len(forwards):
        raise ValueError(f"長さが違う（{len(signals)} 対 {len(forwards)}）。")
    denominator = sum(value * value for value in signals)
    if denominator <= MIN_DISPERSION:
        raise ValueError("signal の二乗和が 0。標準化に失敗している。")
    return sum(z * r for z, r in zip(signals, forwards, strict=True)) / denominator


def long_only_return(signals: Sequence[float], forwards: Sequence[float]) -> float | None:
    """上側だけを、傾きに比例した重みで買ったときのリターン。

    重みは ``max(z, 0)`` を合計1に正規化したもの。**分位1を等金額で買うのと
    同じ「買うだけ」の形だが、切り捨てずに滑らかに重み付ける。**

    上側が1銘柄も無ければ ``None``。

    **β は引いていない。** #7 の α（β=0.542 を引いたもの）とは直接比べられ
    ないので、呼ぶ側で混ぜないこと。
    """
    if len(signals) != len(forwards):
        raise ValueError(f"長さが違う（{len(signals)} 対 {len(forwards)}）。")
    weights = [value if value > 0 else 0.0 for value in signals]
    total = sum(weights)
    if total <= MIN_DISPERSION:
        return None
    return sum(w * r for w, r in zip(weights, forwards, strict=True)) / total


@dataclass(frozen=True)
class EstimatorSeries:
    """同じ断面から作った、推定量ごとの月次系列。

    **月の並びは揃っている。** 揃っていない系列どうしの t を比べると、
    推定量の差ではなく期間の差を測ることになる。
    """

    months: int
    quantile_spread: list[float]
    """分位1 − 分位5。ロングショート。"""
    tilt: list[float]
    """1σチルト。ロングショート。**上のと比べる相手はこれ。**"""
    long_only_tilt: list[float]
    """上側だけを傾きで重み付けて買ったもの。**β を引いていない。**"""
    quantile_long_only: list[float]
    """分位1を等金額で買ったもの。**β を引いていない。** 上と比べる相手。"""
    skipped_flat: int = 0
    """断面がばらつかず落とした月。"""


def build_estimators(
    cross_sections: Sequence[Sequence[tuple[float, float]]],
    quantiles: int = 5,
) -> EstimatorSeries:
    """月ごとの断面から、推定量ごとの系列をまとめて作る。

    **同じ断面から作る。** 別経路で組み直すと、比が「推定量の差」ではなく
    「フィルタの差」を含む。

    Args:
        cross_sections: 月ごとの ``(ボラティリティ, 翌月リターン)``。
        quantiles: 分位数。

    Returns:
        推定量ごとの月次系列。月の並びは揃っている。
    """
    spread: list[float] = []
    tilt: list[float] = []
    long_tilt: list[float] = []
    quantile_long: list[float] = []
    skipped = 0

    for members in cross_sections:
        if len(members) < quantiles:
            skipped += 1
            continue
        ordered = sorted(members, key=lambda pair: pair[0])
        size = len(ordered)
        forwards = [forward for _vol, forward in ordered]

        # **signal は「ボラティリティの符号を反転したもの」。** 低ボラが買う側
        # なので、こうすると傾きが正＝低ボラが勝つ、と読める。分位1が添字0で
        # あることと向きが揃う。
        signals = zscores([-vol for vol, _forward in ordered])
        if signals is None:
            skipped += 1
            continue

        low = forwards[: size // quantiles]
        high = forwards[size - size // quantiles :]
        spread.append(sum(low) / len(low) - sum(high) / len(high))
        quantile_long.append(sum(low) / len(low))

        tilt.append(tilt_return(signals, forwards))
        upper = long_only_return(signals, forwards)
        if upper is None:
            # ここに来るのは断面が全部同符号のときで、上で弾けていないなら
            # 揃っていない系列を返すことになる。**揃わない月は全部落とす。**
            spread.pop()
            quantile_long.pop()
            tilt.pop()
            skipped += 1
            continue
        long_tilt.append(upper)

    if skipped:
        logger.info("断面がばらつかず落とした月: %d", skipped)
    return EstimatorSeries(
        months=len(spread),
        quantile_spread=spread,
        tilt=tilt,
        long_only_tilt=long_tilt,
        quantile_long_only=quantile_long,
        skipped_flat=skipped,
    )


def t_ratio(new: float, old: float) -> float | None:
    """新しい推定量が、古いものの何倍の t を出したか。

    **符号が違えば比を返さない。** 片方が負なら「何倍良い」は意味を持たない。
    """
    if old == 0 or (new < 0) != (old < 0):
        return None
    return new / old
