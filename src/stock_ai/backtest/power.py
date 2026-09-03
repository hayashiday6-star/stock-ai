"""重なった窓を持つ日次系列の検出力を、平均を見ずに見積もる。

**封印の前に「何%あれば有意になるか」を出すためのモジュールである。**
決算ドリフトの2本は、封印してから検出力が足りないと分かった。SUE 版では
1分位あたり1〜2銘柄という母集団の薄さが、日次リターンの標準偏差 21.08% と
いう異常な値になって初めて見えた。順序が逆だった。

**平均は使わない。** 分散と自己共分散だけを推定する。中心化のために標本平均を
使うが、値そのものは返さないし、この dataclass は平均を持たない。分散の推定は
仮説検定を消費しないので、判定に使わない期間の分散から、判定に使う期間の
標準誤差を先に計算できる。

重なりの扱い:

毎営業日エントリーして20営業日持つと、隣り合う観測は19/20が同じ日を共有する。
独立と見なして数えると標準誤差を4倍近く小さく見積もる。Newey-West の
Bartlett 重み（ラグ20）で長期分散に直す。

  Ω = γ0 + 2 Σ_{k=1}^{L} (1 − k/(L+1)) γ_k
  SE(平均) = sqrt(Ω / n)

Bartlett 重みを使うのは、この形だと Ω が必ず非負になるためである。小標本補正
として (n−k)/n を掛ける形もあるが、そちらは負の分散を出しうる。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from stock_ai.core.logging import get_logger

logger = get_logger(__name__)

#: 重なりのラグ。保有営業日数に合わせる。20日保有なら20。
DEFAULT_LAGS = 20

#: 合格に必要な t。両側5%のおおよその臨界値。
TARGET_T = 2.0


def autocovariances(values: Sequence[float], lags: int) -> list[float]:
    """``γ_0 … γ_lags`` を返す。

    標本平均で中心化するが、**平均そのものは返さない。** 呼び出し側が平均を
    受け取れないようにしてあるのは、検出力の見積もりが判定を先食いしない
    ようにするためである。

    Args:
        values: 日次の系列。
        lags: いくつまでの自己共分散を返すか。

    Raises:
        ValueError: 値が2つ未満か、``lags`` が負。
    """
    if lags < 0:
        raise ValueError(f"lags must not be negative; got {lags}.")
    count = len(values)
    if count < 2:
        raise ValueError(f"need at least 2 observations; got {count}.")

    mean = sum(values) / count
    centred = [value - mean for value in values]
    usable = min(lags, count - 1)
    return [
        sum(centred[index] * centred[index + lag] for index in range(count - lag)) / count
        for lag in range(usable + 1)
    ]


def long_run_variance(gammas: Sequence[float]) -> float:
    """Newey-West（Bartlett 重み）の長期分散 Ω。

    ``gammas`` は :func:`autocovariances` の出力（``γ_0`` から）。
    """
    if not gammas:
        raise ValueError("gammas must not be empty.")
    lags = len(gammas) - 1
    omega = gammas[0]
    for lag in range(1, lags + 1):
        omega += 2.0 * (1.0 - lag / (lags + 1)) * gammas[lag]
    return max(omega, 0.0)


@dataclass(frozen=True)
class PowerEstimate:
    """分散だけから作った検出力の見積もり。

    **平均を持たない。** ここに平均を足すと、判定に使わない期間から効果の
    点推定が漏れてくる。足したくなったら、そのときは別の dataclass にする。
    """

    observations: int
    """推定に使った日数。"""
    lags: int
    """Newey-West のラグ。"""
    variance: float
    """γ0。1日ぶんの分散。"""
    omega: float
    """重なりを織り込んだ長期分散。"""

    @property
    def daily_sd(self) -> float:
        """日次の標準偏差。"""
        return math.sqrt(self.variance)

    @property
    def inflation(self) -> float:
        """重なりで標準誤差が何倍になるか。

        1.0 なら重なっていないのと同じ。20日保有・毎日エントリーなら、
        理屈の上では4倍前後になる。
        """
        return math.sqrt(self.omega / self.variance) if self.variance > 0 else float("nan")

    def standard_error(self, sample_days: int) -> float:
        """``sample_days`` 日ぶんの平均の標準誤差。

        Raises:
            ValueError: ``sample_days`` が1未満。
        """
        if sample_days < 1:
            raise ValueError(f"sample_days must be at least 1; got {sample_days}.")
        return math.sqrt(self.omega / sample_days)

    def detectable(self, sample_days: int, target_t: float = TARGET_T) -> float:
        """``sample_days`` 日で ``target_t`` に届くのに必要な差。

        これが費用のしきい値より大きければ、**その期間では合格を出しようが
        ない**。封印する前に知っておく数字である。
        """
        return target_t * self.standard_error(sample_days)


def estimate_power(values: Sequence[float], lags: int = DEFAULT_LAGS) -> PowerEstimate:
    """日次系列から :class:`PowerEstimate` を作る。**平均は返さない。**"""
    gammas = autocovariances(values, lags)
    estimate = PowerEstimate(
        observations=len(values),
        lags=len(gammas) - 1,
        variance=gammas[0],
        omega=long_run_variance(gammas),
    )
    logger.info(
        "検出力の見積もり: %d 日、日次SD %.4f、重なりによる膨張 %.2f 倍",
        estimate.observations,
        estimate.daily_sd,
        estimate.inflation,
    )
    return estimate
