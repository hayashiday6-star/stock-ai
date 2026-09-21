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

import datetime as dt
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


#: 膨張の下限。**1.0 を下回らせない。**
#:
#: **`calibrated_t` には最初から在った規則である**——「補正は足りない分を
#: 足すためのもので、割り引くためのものではない」。**標準誤差の側に無かった**
#: （2026-09-21 に発覚）。
#:
#: 実データで、候補6（予想変動率の跳ね）が **0.82x** を出した。IS の観測が
#: 12 なのに `lags=20` で Newey-West を当てていて、**いちばん長いラグが
#: 1組の積からできている。** その推定が 1.0 を割り、**壁が 18% 低く出た**
#: ——また緩む向きである。
#:
#: **「記録した数字は動かない」と書いた。それは確かめずに書いた**
#: （2026-09-21。`tests/test_passing.py` が落ちて分かった）。`SHAPES` の
#: イベント型だけを見て、月次と暦を見ていなかった。
#:
#: | 形 | 膨張 | 要るリターン |
#: |---|---|---|
#: | #13（月替わり・暦） | 0.97x | 年 11.3% → **11.7%** |
#: | #11（複合・ロングショート） | 0.95x | 年 16.9% → **17.8%** |
#:
#: **どちらも厳しくなる向きで、どちらも「上限すら届かない」で閉じている**
#: ので、判定は1つも動かない。**床が過去の不合格を合格に変えることは、
#: 定義上ありえない。**
INFLATION_FLOOR = 1.0


def standard_error(sd: float, inflation: float, periods: int) -> float:
    """``periods`` 期ぶんの平均の標準誤差。**式はここ1箇所だけ。**

    **膨張を掛け忘れる形を、3度書いた。** `PowerEstimate`・`passing.Shape`・
    `wall.Wall` が同じ式を別々に持っていて、**3つ目が膨張を落としていた**
    （2026-09-19）。20日保有・毎日エントリーなら膨張は理屈の上で4倍前後に
    なるので、**落とすと壁が数倍低く出る**——緩む向きである。

    **そして 1.0 を下回らせない**（:data:`INFLATION_FLOOR`）。`calibrated_t`
    には最初から在った規則が、こちらには無かった（2026-09-21）。**同じ規則を
    2箇所に置いて、片方だけ落としていた形である。**

    Args:
        sd: 1期あたりの標準偏差。
        inflation: 重なりで標準誤差が何倍になるか。**重ならないなら 1.0。**
            **1.0 を下回る値を渡しても 1.0 として扱う。**
        periods: 期数。

    Returns:
        平均の標準誤差。

    Raises:
        ValueError: ``sd`` が負、``inflation`` が 0 以下、``periods`` が1未満。
    """
    if sd < 0:
        raise ValueError(f"sd must not be negative; got {sd}.")
    if inflation <= 0:
        raise ValueError(f"inflation must be positive; got {inflation}.")
    if periods < 1:
        raise ValueError(f"periods must be at least 1; got {periods}.")
    return sd * max(inflation, INFLATION_FLOOR) / math.sqrt(periods)


def detectable_difference(
    sd: float,
    inflation: float,
    periods: int,
    target_t: float = TARGET_T,
) -> float:
    """``periods`` 期で ``target_t`` に届くのに要る差。**`periods_needed` の逆。**

    Args:
        sd: 1期あたりの標準偏差。
        inflation: 重なりによる標準誤差の膨張。
        periods: 期数。
        target_t: 合格に要する `t`。

    Returns:
        見分けられる最小の差。

    Raises:
        ValueError: :func:`standard_error` と同じ条件。
    """
    return target_t * standard_error(sd, inflation, periods)


@dataclass(frozen=True)
class PowerEstimate:
    """分散だけから作った検出力の見積もり。

    **平均を持たない。** ここに平均を足すと、判定に使わない期間から効果の
    点推定が漏れてくる。足したくなったら、そのときは別の dataclass にする。
    """

    observations: int
    """推定に使った日数。"""
    lags: int
    """Newey-West のラグ。**実際に使った数**（標本が短ければ切り詰められる）。"""
    variance: float
    """γ0。1日ぶんの分散。"""
    omega: float
    """重なりを織り込んだ長期分散。"""

    requested_lags: int = -1
    """呼ぶ側が頼んだラグ。``-1`` なら :attr:`lags` と同じとみなす。

    **切り詰められたことを、出力に出すために持つ。** ``autocovariances`` は
    ``min(lags, n-1)`` に黙って切り詰めるので、**12 個の観測に 20 ラグを
    頼んでも例外は出ない**——いちばん長いラグが**1組の積**からできた値が
    返るだけである（2026-09-21、候補6 が 0.82x を出して発覚）。
    """

    @property
    def undersampled(self) -> bool:
        """**ラグが標本より長いか。** 長ければ、膨張の推定は当てにならない。

        γ_k は ``n − k`` 組の積からできている。``k`` が ``n`` に近づくと
        1組か2組になり、**Ω がただの雑音になる。** 1.0 を割ることさえある
        ——実データで **0.82x** が出た。
        """
        asked = self.lags if self.requested_lags < 0 else self.requested_lags
        return asked > 0 and self.observations <= asked

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

        **式は `standard_error` に1つだけ置いてある。**

        Raises:
            ValueError: ``sample_days`` が1未満。
        """
        return standard_error(self.daily_sd, self.inflation, sample_days)

    def detectable(self, sample_days: int, target_t: float = TARGET_T) -> float:
        """``sample_days`` 日で ``target_t`` に届くのに必要な差。

        これが費用のしきい値より大きければ、**その期間では合格を出しようが
        ない**。封印する前に知っておく数字である。

        **式は `detectable_difference` に1つだけ置いてある。**
        """
        return detectable_difference(self.daily_sd, self.inflation, sample_days, target_t)


def trimmed_variance(values: Sequence[float], fraction: float = 0.01) -> tuple[float, int]:
    """最も外れた ``fraction`` を落としたときの分散と、落とした件数。

    **推定量ではない。感度である。** 分散の 90% が全体の 1% の日でできている
    なら、その分散は「毎日どれくらい散らばるか」ではなく「まれに何が起きるか」
    を測っている。どちらを検出力の根拠にするかは設計の判断で、この関数は
    どちらかを選ばない——両方を見せるためだけにある。

    外れの大きさは**中央値**からの距離で測る。平均を使うと、外れ値自身が
    中心を引っ張って外れて見えなくなる。中央値は位置の統計量なので、これを
    内部で使っても効果の点推定は漏れない（返すのは散らばりだけである）。

    Raises:
        ValueError: ``fraction`` が 0 以上 1 未満でない。
    """
    if not 0.0 <= fraction < 1.0:
        raise ValueError(f"fraction must be in [0, 1); got {fraction}.")
    count = len(values)
    drop = int(count * fraction)
    if count - drop < 2:
        raise ValueError(f"trimming {drop} of {count} leaves too few observations.")

    ordered = sorted(values)
    centre = ordered[count // 2]
    kept = sorted(values, key=lambda value: abs(value - centre))[: count - drop]
    mean = sum(kept) / len(kept)
    variance = sum((value - mean) ** 2 for value in kept) / len(kept)
    return variance, drop


@dataclass(frozen=True)
class Judgement:
    """判定に使う3点。**ここには平均がある。**

    :class:`PowerEstimate` が平均を持たないのと対になっている。検出力の
    見積もりは判定を先食いしてはならないが、判定そのものは平均を出すのが
    仕事である。**同じ dataclass に混ぜないことで、どちらの計算をしているかが
    型で分かる。**
    """

    days: int
    mean: float
    """平均。20営業日あたりのリターン差。"""
    standard_error: float
    """Newey-West（Bartlett 重み）の標準誤差。"""

    @property
    def t_statistic(self) -> float:
        """``mean / standard_error``。標準誤差が0なら ``nan``。"""
        return self.mean / self.standard_error if self.standard_error > 0 else float("nan")


def judge(values: Sequence[float], lags: int = DEFAULT_LAGS) -> Judgement:
    """日次系列から平均・標準誤差・t を求める。**判定にだけ使う。**

    重なりの扱いは :func:`estimate_power` と同一である。検出力の見積もりと
    判定で別の分散を使うと、「必要な差」と「出た差」が比較できなくなる。

    **同じ式を2つ書いていた。** ここは ``sqrt(Ω/n)`` を直に書いていて、
    :func:`standard_error` を通らないので、**膨張の床（1.0）が掛からな
    かった**（2026-09-21）。`tests/test_power.py` の
    `test_the_judgement_uses_the_same_variance_as_the_power_estimate` が
    落ちて分かった——**そのために置いてあるテストである。**
    """
    count = len(values)
    # **見積もりと同じ入れ物を通す。** 式を2つ持たない。
    estimate = estimate_power(values, lags)
    return Judgement(
        days=count,
        mean=sum(values) / count,
        standard_error=estimate.standard_error(count),
    )


def estimate_power(values: Sequence[float], lags: int = DEFAULT_LAGS) -> PowerEstimate:
    """日次系列から :class:`PowerEstimate` を作る。**平均は返さない。**"""
    gammas = autocovariances(values, lags)
    estimate = PowerEstimate(
        observations=len(values),
        lags=len(gammas) - 1,
        variance=gammas[0],
        omega=long_run_variance(gammas),
        requested_lags=lags,
    )
    if estimate.undersampled:
        logger.warning(
            "**ラグ %d が標本 %d より長い。** 膨張 %.2f は当てにならない"
            "——いちばん長いラグが1組の積からできている。",
            lags,
            estimate.observations,
            estimate.inflation,
        )
    logger.info(
        "検出力の見積もり: %d 日、日次SD %.4f、重なりによる膨張 %.2f 倍",
        estimate.observations,
        estimate.daily_sd,
        estimate.inflation,
    )
    return estimate


# --- §0 検出可能性ゲート -----------------------------------------------------
#
# **封印する前に「そもそも検出できるのか」を通す関門である。**
#
# #2 と #3 は封印した後で検出力不足に気付いた。#7 は封印前に気付いたが、
# 「五分五分」と書いたうえで回して、五分五分の負けた側に落ちた。**気付いて
# いたのに止めなかった。** 止める場所が無かったからである。ここがその場所。
#
# 判定の当てはめを `verdict()` にしたのと同じ理由で、これも関数にする。
# 人が読んで当てはめると、封印前でも基準が動く。

GATE_PASS = "通す"
GATE_FAIL = "通さない"


def periods_needed(
    sd: float,
    inflation: float,
    effect: float,
    target_t: float = TARGET_T,
) -> int:
    """``effect`` を ``target_t`` で検出するのに要る期数。

    ``n = (target_t × sd × inflation ÷ effect)²`` を解いただけである。

    **これを先に出すと、話が「何年ぶん要るか」になる。** 「検出力が足りない」
    と言われても打ち手が浮かばないが、「年3%を見るには21年ぶん要る」なら、
    手元の年数と引き算ができる。

    Args:
        sd: 1期あたりの標準偏差。
        inflation: 重なりによる標準誤差の膨張（重ならないなら 1.0）。
        effect: 検出したい差（1期あたり、``sd`` と同じ単位）。
        target_t: 合格に要する t。

    Returns:
        必要な期数（切り上げ）。

    Raises:
        ValueError: ``effect`` が 0 以下。
    """
    if effect <= 0:
        raise ValueError(f"effect must be positive; got {effect}.")
    if sd <= 0 or inflation <= 0:
        raise ValueError(f"sd and inflation must be positive; got {sd}, {inflation}.")
    return math.ceil((target_t * sd * inflation / effect) ** 2)


@dataclass(frozen=True)
class Requirement:
    """「この効果を検出するのに要る期数」の1行。

    **期数と年数を、同じ標本で数える。** 別々に作ると、片方が推定した窓
    （IS）、もう片方が判定する窓（OOS）を指す——`_event_gate` が実際に
    そうなっていて、**「6.9年 要る」と「足りている」が同じ行に並んだ**
    （2026-09-20、ユーザーが発見）。読む側はどちらの枠とも取れて、
    **答えが食い違う。**

    ここでは ``years`` を**手元の率から割り出す**ので、
    ``years ÷ have_years == periods ÷ have_periods`` が**構成上いつでも
    成り立つ。** 2つの標本を混ぜる余地が無い。
    """

    effect: float
    """検出したい差（1期あたり）。"""

    periods: int
    """それに要る期数。"""

    years: float
    """**手元と同じ率**で数えた年数。"""

    have_periods: int
    """いま在る期数。"""

    have_years: float
    """いま在る期数が何年ぶんか。"""

    def __post_init__(self) -> None:
        """枠が揃っていることを、作った時点で確かめる。

        Raises:
            ValueError: 手元の期数・年数が正でない、または比が一致しない。
        """
        if self.have_periods <= 0 or self.have_years <= 0:
            raise ValueError(
                f"have_periods and have_years must be positive; "
                f"got {self.have_periods}, {self.have_years}."
            )
        # **比で見る。** 差で見ると、単位が違っても通ってしまう。
        want = self.periods / self.have_periods
        got = self.years / self.have_years
        if abs(want - got) > 1e-9 * max(1.0, want):
            raise ValueError(
                f"期数の比 {want} と年数の比 {got} が合わない。**別々の標本で数えている。**"
            )

    @property
    def enough(self) -> bool:
        """いま在る期数で足りるか。"""
        return self.periods <= self.have_periods

    @property
    def shortfall(self) -> int:
        """あと何期要るか。足りていれば 0。"""
        return max(self.periods - self.have_periods, 0)


def requirement(  # noqa: PLR0913 - 効果と手元の枠を両方受け取る
    effect: float,
    sd: float,
    inflation: float,
    have_periods: int,
    have_years: float,
    target_t: float = TARGET_T,
) -> Requirement:
    """``effect`` を検出するのに要る期数を、**手元と同じ枠で**出す。

    **1年あたりの期数は、``have_periods ÷ have_years`` から作る。**
    推定に使った標本の件数から作らない——それが枠の食い違いの出どころで
    ある（:class:`Requirement`）。

    Args:
        effect: 検出したい差（1期あたり、``sd`` と同じ単位）。
        sd: 1期あたりの標準偏差。
        inflation: 重なりによる標準誤差の膨張。
        have_periods: **判定に使える**期数。
        have_years: その期数が何年ぶんか。
        target_t: 合格に要する t。

    Returns:
        :class:`Requirement`。

    Raises:
        ValueError: ``have_years`` が 0 以下。
    """
    if have_years <= 0:
        raise ValueError(f"have_years must be positive; got {have_years}.")
    count = periods_needed(sd, inflation, effect, target_t)
    per_year = have_periods / have_years
    return Requirement(
        effect=effect,
        periods=count,
        years=count / per_year,
        have_periods=have_periods,
        have_years=have_years,
    )


def required_improvement(detectable: float, plausible_low: float) -> float:
    """通すのに要る「推定量の改善倍率」。

    期数を増やせないとき、残る手は**分散を下げる設計に変える**ことである。
    分位ソートは上下2割しか使わず、真ん中を捨てている。横断回帰なら断面全体を
    使える。

    **その改善は t の比で測る。SD の比ではない。** 分位スプレッドは、断面が
    正規なら上位20%が約 +1.40σ、下位が −1.40σ なので、``2.8 × 1σチルト``に
    あたる。**推定量を変えると SD も効果も一緒に縮む。** SD 比だけを掛けた
    ところに文献の分位スプレッドの効果量を当てると、2.8倍の改善が無料で出た
    ように見える。t は無次元なのでこの取り違えが起きない。

    Args:
        detectable: いまの設計で検出できる差。
        plausible_low: 見込みの下限。

    Returns:
        必要な倍率。1.0 以下なら、いまの設計で既に足りている。

    Raises:
        ValueError: ``plausible_low`` が 0 以下。
    """
    if plausible_low <= 0:
        raise ValueError(f"plausible_low must be positive; got {plausible_low}.")
    return detectable / plausible_low


@dataclass(frozen=True)
class Gate:
    """検出可能性ゲートの結果。**平均は持たない。**

    `PowerEstimate` と同じで、ここに実測の平均は入らない。入れられる形にすると、
    「効果がありそうだから通す」が書けてしまう。**通すかどうかは、見込みと
    検出できる差だけで決まる。**
    """

    detectable: float
    plausible_low: float
    plausible_high: float
    verdict: str
    reading: str

    @property
    def passed(self) -> bool:
        """通ったか。"""
        return self.verdict == GATE_PASS


def gate(detectable: float, plausible_low: float, plausible_high: float) -> Gate:
    """封印してよいかを決める。**見込みの下限が検出できる差を超えること。**

    中央値ではなく**下限**で見る。中央値で通すと、「アノマリーが文献どおりに
    実在していても五分五分」という検定を封印できてしまう。#7 がそれだった。

    **偽陰性と偽陽性は等価ではない。** 通らない検定を回して不合格を得ると、
    「効果が無い」と「小さすぎて見えない」の区別が付かないまま説を1本失う。
    回さなければ、設計を直してから出直せる。

    Args:
        detectable: その設計で検出できる差（1期あたり）。
        plausible_low: 見込みの下限。文献・機構・先行検証から**封印前に**置く。
        plausible_high: 見込みの上限。読み方に使うだけで、合否には使わない。

    Returns:
        通すか通さないかと、その読み方。

    Raises:
        ValueError: 下限が上限を超えている。
    """
    if plausible_low > plausible_high:
        raise ValueError(
            f"見込みの下限 {plausible_low} が上限 {plausible_high} を超えている。"
            "範囲を逆に置いていないか。"
        )
    if plausible_low > detectable:
        return Gate(
            detectable,
            plausible_low,
            plausible_high,
            GATE_PASS,
            "見込みの下限が検出できる差を上回る。**封印してよい。**",
        )
    if plausible_high <= detectable:
        return Gate(
            detectable,
            plausible_low,
            plausible_high,
            GATE_FAIL,
            "見込みの上限すら検出できる差に届かない。"
            "**回しても不合格にしかならない。** 設計を変えるか、説を閉じる。",
        )
    return Gate(
        detectable,
        plausible_low,
        plausible_high,
        GATE_FAIL,
        "見込みが検出できる差をまたいでいる。**通るかどうかが運になる。** "
        "#7 がこれで、負けた側に落ちた。期数を増やすか、分散を下げる設計にする。",
    )


# --- 合成の利得（r）の当てはめ ------------------------------------------------
#
# 閾値は `docs/HYPOTHESES.md` に**測る前から**書いてある。ここは、その表を
# 人が読んで当てはめないようにするためだけの関数である。
#
# **曖昧域は打ち切りに倒してある。** 「過小評価だから財務系を足せば届くかも
# しれない」は測定後にしか使えない理屈で、それで測り直す形は「当てはまるまで
# 測り方を変えること」と区別が付かない。

COMPOSITE_PROCEED = "通過"
COMPOSITE_STOP = "打ち切り"

#: r がこれ以上なら財務抽出に進む。
COMPOSITE_PASS = 2.0

#: r がこれ未満なら「明確に不足」。**行動は曖昧域と同じ打ち切りである。**
#:
#: この線は記録上の区別だけで置いてある。「明確に足りない」と「惜しかった」を
#: 後から読み分けるためで、どちらも 4' に進む。
COMPOSITE_AMBIGUOUS = 1.5


def composite_verdict(ratio: float) -> tuple[str, str]:
    """合成の利得 r を、封印済みの表に当てはめる。

    Args:
        ratio: 合成の t ÷ 最も良い単一因子の t。

    Returns:
        ``(扱い, 読み方)``。
    """
    if ratio >= COMPOSITE_PASS:
        return COMPOSITE_PROCEED, (
            "財務抽出に進み、合成を**1本だけ**封印する。封印前に §0 ゲートを当てること。"
        )
    if ratio >= COMPOSITE_AMBIGUOUS:
        return COMPOSITE_STOP, (
            "**曖昧域。打ち切る。** 財務系を足した再測定はしない——"
            "「過小評価だから」は測定後にしか使えない理屈で、それで測り直すのは"
            "当てはまるまで測り方を変えることと区別が付かない。4' へ。"
        )
    return COMPOSITE_STOP, "**明確に不足。** 4' へ。"


# --- #3（52週高値更新）の封印しない線 ------------------------------------
#
# **推定を1つも見ないうちに置いた（2026-09-05）。** 見てから動かさない。
# 経緯は `docs/NEXT_CANDIDATES.md`「決定3」。

#: IS の終わり。**この日を含めて IS、翌日から OOS。**
#:
#: 2021-09 にしたのは生存バイアスである。廃止銘柄の株価はそこからしか手元に
#: 無く、それ以前で判定すれば #6 が何日もかけて直した欠陥をそのまま踏む。
HIGH_IS_END = dt.date(2021, 9, 30)

#: 封印する窓。20営業日。
#:
#: 効果は窓の長さにほぼ比例して積み上がるが、費用は往復1回ぶんで一定、検出
#: できる差は窓の平方根より緩やかにしか増えない。1日あたりに直すと 20日は
#: 5日の3分の1以下のハードルになる。
HIGH_HOLDING = 20

#: **IS の推定値がこれを下回れば封印しない。** #3 はそこで閉じる。
#:
#: 1.26% ＝ OOS 1,197日での検出できる差 0.86% ＋ 費用 0.40%。
#:
#: **この線は候補文書が「現実的な水準」と書いた 1.0% より上にある。** §0 を
#: 段2（自分の IS から推定する）で埋めた代償で、選別に半分以上を使ったぶん
#: 判定に残る期数が減った。通らない見込みが薄いと分かったうえで測る。
HIGH_SEAL_FLOOR = 0.0126

HIGH_SEAL = "封印する"
HIGH_STOP = "封印しない"


# --- 2つ目の校正（共通因子を抜く）の線 ------------------------------------
#
# **測る前に確定させた（2026-09-16）。** 出どころは `docs/HYPOTHESES.md`
# 「2つ目の校正」。**見てから動かさない。**

#: 動いたと言える比。**#7 が要った 1.38倍を上回る線。**
NEUTRAL_PASS = 1.4

#: 曖昧域の下端。ここから上は「もう少し」だが、**扱いは打ち切りで同じ。**
NEUTRAL_AMBIGUOUS = 1.2

NEUTRAL_PROCEED = "動いた"
NEUTRAL_STOP = "打ち切る"


def neutral_verdict(ratio: float | None) -> tuple[str, str]:
    """共通因子を抜いた利得 r を、封印済みの表に当てはめる。

    Args:
        ratio: 中立版の t ÷ いまの方法の t。符号が違えば ``None``。

    Returns:
        ``(扱い, 読み方)``。
    """
    if ratio is None:
        return NEUTRAL_STOP, (
            "**符号が違うので比を返せない。** 片方が負なら「何倍良い」は意味を"
            "持たない（2026-09-05 に合成でそうなった）。打ち切る。"
        )
    if ratio >= NEUTRAL_PASS:
        return NEUTRAL_PROCEED, (
            "**新しい説の設計に使える。** ただし #8（5.3倍要る）と #9（10倍要る）"
            "は戻らない。戻るのは「月次・technical・効果が年3〜4%」の一角だけである。"
        )
    if ratio >= NEUTRAL_AMBIGUOUS:
        return NEUTRAL_STOP, (
            "**曖昧域。打ち切る。** 「もう少しで届くから別の因子で測り直す」は"
            "測定後にしか出てこない理屈で、当てはまるまで測り方を変えることと"
            "区別が付かない。"
        )
    return NEUTRAL_STOP, (
        "**動かない。** 散らばりの大部分は業種でも規模でもなかった。"
        "推定量をいじって届く距離ではない。"
    )


def high_verdict(estimate: float) -> tuple[str, str]:
    """IS の推定値を、封印済みの線に当てはめる。

    Args:
        estimate: IS の20日窓の推定値（1イベントあたり、小数）。

    Returns:
        ``(扱い, 読み方)``。
    """
    if estimate >= HIGH_SEAL_FLOOR:
        return HIGH_SEAL, (
            "**段2 で §0 を通った。** 事前登録を書き、OOS で一度だけ判定する。"
            "記録には「段2で通した」と書く——段1（同じ設計の文献値）で通したものと"
            "同じ主張ではない。"
        )
    return HIGH_STOP, (
        "**線を下回った。#3 を閉じる。再測定はしない。** 窓を変える・期間を変える・"
        "費用の仮定を下げる、はどれも測定後にしか出てこない理屈である。"
    )
