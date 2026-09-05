"""検出力の見積もりが、重なりを正しく織り込んでいるか。

守りたいのは2つ。**平均が漏れないこと**と、**重なった窓を独立扱いしない
こと**。SUE 版は封印してから検出力が足りないと分かった。今回はここを先に
固める。
"""

from __future__ import annotations

import math

import pytest

from stock_ai.backtest.power import (
    autocovariances,
    estimate_power,
    judge,
    long_run_variance,
    trimmed_variance,
)


def test_the_estimate_carries_no_mean() -> None:
    """**平均を持たない dataclass であること。**

    足せば「判定に使わない期間」から効果の点推定が漏れてくる。フィールド名で
    固定しておく。
    """
    estimate = estimate_power([0.0, 1.0, -1.0, 0.5, -0.5] * 20, lags=3)
    fields = set(vars(estimate))
    assert "mean" not in fields
    assert not any("mean" in name for name in fields)


def test_shifting_every_value_leaves_the_estimate_unchanged() -> None:
    """平均をずらしても分散は動かない ＝ 平均に依存していない。"""
    values = [0.01, -0.02, 0.03, 0.00, -0.01] * 30
    base = estimate_power(values, lags=5)
    shifted = estimate_power([value + 10.0 for value in values], lags=5)
    assert base.variance == pytest.approx(shifted.variance)
    assert base.omega == pytest.approx(shifted.omega)


def test_white_noise_is_not_inflated() -> None:
    """自己相関が無ければ Ω ≒ γ0、膨張率 ≒ 1。"""
    values = [1.0, -1.0] * 500  # γ1 が負で交互 - 完全な白色ではないが有限
    estimate = estimate_power(values, lags=0)
    assert estimate.lags == 0
    assert estimate.omega == pytest.approx(estimate.variance)
    assert estimate.inflation == pytest.approx(1.0)


def test_a_perfectly_repeated_series_inflates_by_the_bartlett_sum() -> None:
    """完全に相関した系列では Ω が γ0 より大きくなる。

    重なった窓を独立扱いすると標準誤差を小さく見積もる、というのが
    この検証全体で効いてくる点である。
    """
    values = [1.0] * 200 + [-1.0] * 200  # 隣接ラグはほぼ完全相関
    estimate = estimate_power(values, lags=20)
    assert estimate.omega > estimate.variance
    assert estimate.inflation > 2.0


def test_standard_error_shrinks_with_the_square_root_of_the_sample() -> None:
    estimate = estimate_power([0.02, -0.01, 0.015, -0.02] * 100, lags=4)
    assert estimate.standard_error(400) == pytest.approx(
        estimate.standard_error(100) / 2.0, rel=1e-9
    )


def test_detectable_difference_is_the_target_t_times_the_standard_error() -> None:
    estimate = estimate_power([0.02, -0.01, 0.015, -0.02] * 100, lags=4)
    assert estimate.detectable(600, target_t=2.0) == pytest.approx(
        2.0 * estimate.standard_error(600)
    )


def test_long_run_variance_never_goes_negative() -> None:
    """Bartlett 重みを使う理由。負の分散を出す推定量は使わない。"""
    gammas = [1.0, -0.9, -0.9, -0.9]
    assert long_run_variance(gammas) >= 0.0


def test_autocovariances_rejects_a_series_too_short_to_have_any() -> None:
    with pytest.raises(ValueError, match="at least 2"):
        autocovariances([0.1], lags=2)


def test_lags_are_capped_by_the_sample() -> None:
    """ラグ20を要求しても、5点しか無ければ4までしか返らない。"""
    gammas = autocovariances([1.0, 2.0, 3.0, 2.0, 1.0], lags=20)
    assert len(gammas) == 5


def test_zero_variance_reports_nan_inflation_rather_than_dividing_by_zero() -> None:
    estimate = estimate_power([0.5] * 50, lags=3)
    assert estimate.variance == pytest.approx(0.0)
    assert math.isnan(estimate.inflation)


def test_trimming_shows_when_the_spread_is_a_handful_of_days() -> None:
    """**分散が「毎日の散らばり」か「まれな出来事」かを分ける。**

    静かな日が99日、桁違いの日が1日。全体のSDはその1日でできている。
    """
    values = [0.01, -0.01] * 50 + [50.0]
    full = estimate_power(values, lags=0)
    trimmed, dropped = trimmed_variance(values, fraction=0.01)

    assert dropped == 1
    assert full.variance > 20.0
    assert trimmed < 0.001  # 1日落とすだけで消える


def test_trimming_a_well_behaved_series_barely_moves_it() -> None:
    values = [0.02, -0.01, 0.015, -0.02] * 100
    full = estimate_power(values, lags=0)
    trimmed, _dropped = trimmed_variance(values, fraction=0.01)
    assert trimmed == pytest.approx(full.variance, rel=0.15)


def test_the_centre_is_the_median_so_an_outlier_cannot_hide_itself() -> None:
    """平均を中心にすると、外れ値自身が中心を引っ張って外れて見えなくなる。"""
    values = [0.0] * 99 + [100.0]
    trimmed, dropped = trimmed_variance(values, fraction=0.01)
    assert dropped == 1
    assert trimmed == pytest.approx(0.0)


def test_trimming_rejects_a_fraction_that_would_empty_the_series() -> None:
    with pytest.raises(ValueError, match="too few"):
        trimmed_variance([0.1, 0.2, 0.3], fraction=0.9)


def test_the_judgement_carries_the_mean_and_the_estimate_does_not() -> None:
    """**型でどちらの計算かが分かること。**

    検出力の見積もりは判定を先食いしてはならないが、判定そのものは平均を
    出すのが仕事である。同じ dataclass に混ぜない。
    """
    values = [0.01, -0.005, 0.02, -0.01] * 50
    assert "mean" in vars(judge(values))
    assert not any("mean" in name for name in vars(estimate_power(values)))


def test_the_judgement_uses_the_same_variance_as_the_power_estimate() -> None:
    """別の分散を使うと「必要な差」と「出た差」が比較できなくなる。"""
    values = [0.01, -0.005, 0.02, -0.01] * 50
    estimate = estimate_power(values, lags=5)
    verdict_of = judge(values, lags=5)
    assert verdict_of.standard_error == pytest.approx(estimate.standard_error(len(values)))


def test_a_zero_standard_error_reports_nan_rather_than_dividing() -> None:
    assert math.isnan(judge([0.05] * 40, lags=2).t_statistic)


# --- §0 検出可能性ゲート -----------------------------------------------------
#
# 封印する前に「そもそも検出できるのか」を通す関門。#2 と #3 は封印後に
# 検出力不足が分かった。#7 は封印前に「五分五分」と気付いていたのに回して、
# 負けた側に落ちた。**気付いていたのに止めなかった。** 止める場所を作る。


def test_the_gate_refuses_the_case_that_actually_happened() -> None:
    """#7 を封印前のゲートに当てると通らない。**これが作った理由である。**

    実測（`PREREG_LOWVOL_JP.md` §8）: 検出できる差 0.33%／月、見込みは
    年 2.3〜4.3%。中央値なら通るが、下限は届かない。
    """
    from stock_ai.backtest.power import GATE_FAIL, gate

    result = gate(0.0033, 0.023 / 12, 0.043 / 12)

    assert result.verdict == GATE_FAIL
    assert not result.passed
    assert "運になる" in result.reading


def test_the_gate_passes_only_when_the_floor_clears_the_bar() -> None:
    """**下限で見る。** 中央値で通すと、#7 のような検定を封印できてしまう。"""
    from stock_ai.backtest.power import GATE_PASS, gate

    # 下限 0.40% が検出できる差 0.33% を上回る。
    assert gate(0.0033, 0.0040, 0.0060).verdict == GATE_PASS
    # 下限がちょうど同じなら通さない（上回ること、が条件）。
    assert not gate(0.0033, 0.0033, 0.0060).passed


def test_the_gate_separates_hopeless_from_a_coin_flip() -> None:
    """上限すら届かないのと、またいでいるのは、次の一手が違う。"""
    from stock_ai.backtest.power import gate

    hopeless = gate(0.0033, 0.0005, 0.0020)
    coin_flip = gate(0.0033, 0.0020, 0.0060)

    assert not hopeless.passed and not coin_flip.passed
    assert "上限すら" in hopeless.reading
    assert "運になる" in coin_flip.reading
    assert hopeless.reading != coin_flip.reading


def test_the_gate_refuses_a_range_given_backwards() -> None:
    """下限と上限を逆に置くと、上限すら届かない扱いで静かに落ちる。"""
    import pytest as _pytest

    from stock_ai.backtest.power import gate

    with _pytest.raises(ValueError):
        gate(0.0033, 0.0060, 0.0020)


def test_the_gate_carries_no_mean() -> None:
    """`PowerEstimate` と同じ。**実測の平均を入れられる形にしない。**

    入れられると「効果がありそうだから通す」が書けてしまう。通すかどうかは
    見込みと検出できる差だけで決まる。
    """
    import dataclasses

    from stock_ai.backtest.power import Gate

    fields = {f.name for f in dataclasses.fields(Gate)}
    assert "mean" not in fields
    assert "observed" not in fields


def test_periods_needed_inverts_the_detectable_difference() -> None:
    """必要な期数と検出できる差は、同じ式の裏表であること。"""
    from stock_ai.backtest.power import periods_needed

    sd, inflation = 0.0184, 1.09
    months = periods_needed(sd, inflation, 0.04 / 12)

    # その期数で検出できる差が、狙った効果とほぼ一致する。
    detectable = 2.0 * sd * inflation / months**0.5
    assert detectable == pytest.approx(0.04 / 12, rel=0.01)


def test_periods_needed_reproduces_the_low_vol_registration() -> None:
    """#7 は 150ヶ月で年4.0%だった。式が実績と合うこと。"""
    from stock_ai.backtest.power import periods_needed

    # §8 の実測 SD 1.84%、膨張 1.09。
    assert periods_needed(0.0184, 1.09, 0.04 / 12) == 145
    # 年3% を見るには倍近く要る。**「検出力不足」より、この形のほうが動ける。**
    assert periods_needed(0.0184, 1.09, 0.03 / 12) == 258


def test_periods_needed_refuses_a_zero_effect() -> None:
    """0 を検出するのに要る期数は無限。黙って巨大な数を返さない。"""
    import pytest as _pytest

    from stock_ai.backtest.power import periods_needed

    with _pytest.raises(ValueError):
        periods_needed(0.0184, 1.09, 0.0)
    with _pytest.raises(ValueError):
        periods_needed(0.0, 1.09, 0.003)


def test_required_improvement_is_the_ratio_that_closes_the_gap() -> None:
    """期数を増やせないときに要る、推定量の改善倍率。"""
    from stock_ai.backtest.power import required_improvement

    # 検出できる差 0.33%、見込みの下限 0.25% なら 1.32 倍要る。
    assert required_improvement(0.0033, 0.0025) == pytest.approx(1.32, rel=1e-3)
    # 既に足りているなら 1 未満。
    assert required_improvement(0.0033, 0.0050) < 1.0


def test_required_improvement_refuses_a_zero_floor() -> None:
    """0 を検出するのに要る改善は無限。黙って巨大な数を返さない。"""
    import pytest as _pytest

    from stock_ai.backtest.power import required_improvement

    with _pytest.raises(ValueError):
        required_improvement(0.0033, 0.0)
