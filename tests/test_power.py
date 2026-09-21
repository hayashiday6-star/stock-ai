"""検出力の見積もりが、重なりを正しく織り込んでいるか。

守りたいのは2つ。**平均が漏れないこと**と、**重なった窓を独立扱いしない
こと**。SUE 版は封印してから検出力が足りないと分かった。今回はここを先に
固める。
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from stock_ai.backtest import power
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


# --- 合成の利得（r）の当てはめ ------------------------------------------------
#
# 閾値は docs/HYPOTHESES.md に測る前から書いてある。ここは、その表を人が読んで
# 当てはめないようにするための関数である。


def test_the_composite_table_is_applied_by_code_not_by_reading() -> None:
    from stock_ai.backtest.power import (
        COMPOSITE_PROCEED,
        COMPOSITE_STOP,
        composite_verdict,
    )

    assert composite_verdict(2.0)[0] == COMPOSITE_PROCEED
    assert composite_verdict(2.5)[0] == COMPOSITE_PROCEED
    assert composite_verdict(1.99)[0] == COMPOSITE_STOP
    assert composite_verdict(1.5)[0] == COMPOSITE_STOP
    assert composite_verdict(1.49)[0] == COMPOSITE_STOP
    assert composite_verdict(0.5)[0] == COMPOSITE_STOP


def test_the_ambiguous_band_stops_and_says_it_will_not_be_remeasured() -> None:
    """**ここが緩むと、当てはまるまで測り方を変えられる。**"""
    from stock_ai.backtest.power import COMPOSITE_STOP, composite_verdict

    verdict, reading = composite_verdict(1.8)

    assert verdict == COMPOSITE_STOP
    assert "曖昧域" in reading
    assert "再測定はしない" in reading


def test_the_two_stopping_bands_read_differently_but_act_the_same() -> None:
    """1.5 の線は記録上の区別だけ。**行動は同じ。**"""
    from stock_ai.backtest.power import composite_verdict

    ambiguous = composite_verdict(1.7)
    clearly_short = composite_verdict(1.0)

    assert ambiguous[0] == clearly_short[0]
    assert ambiguous[1] != clearly_short[1]


def test_the_gate_command_survives_a_floor_that_straddles_zero() -> None:
    """**下限が 0 をまたぐのは、珍しい形ではない。**

    段2 で自分の IS から見込みを置けば、効かない設計では普通に起きる。そこで
    `required_improvement` が例外を投げ、**traceback がそのまま出ていた**
    （2026-09-16、#11 の低ボラ × バリュー）。

    倍率は「下限を検出できる差まで持ち上げる比」なので、下限が負なら持ち上げる
    先が無い。**計算できないと言うのが正しく、落ちるのは正しくない。**
    """
    from typer.testing import CliRunner

    from stock_ai.cli import app

    result = CliRunner().invoke(
        app,
        [
            "power-gate",
            "--sd",
            "4.46",
            "--periods",
            "104",
            "--low",
            "-7.5",
            "--high",
            "11.8",
            "--inflation",
            "0.95",
            "--budget",
            "20",
        ],
    )

    # **2 は「通さない」である。** 落ちたときの 1 と区別が付くこと。
    assert result.exit_code == 2, result.output
    assert "Traceback" not in result.output
    assert "計算できない" in result.output
    # **要る期数の表は、正の効果についてはちゃんと出ること。**
    assert "要る期数" in result.output


def test_the_gate_command_still_gives_the_ratio_when_the_floor_is_positive() -> None:
    """上の分岐が、正の下限まで飲み込んでいないこと。**片側だけ見ない。**"""
    from typer.testing import CliRunner

    from stock_ai.cli import app

    result = CliRunner().invoke(
        app,
        [
            "power-gate",
            "--sd",
            "4.46",
            "--periods",
            "104",
            "--low",
            "2.0",
            "--high",
            "11.8",
            "--inflation",
            "0.95",
            "--budget",
            "20",
        ],
    )

    assert result.exit_code == 2, result.output
    assert "Traceback" not in result.output
    assert "計算できない" not in result.output
    assert "倍" in result.output


class TestTheStandardErrorHasOneHome:
    """**同じ式が3箇所にあり、3つ目が膨張を落としていた**（2026-09-19）。

    `PowerEstimate` と `passing.Shape` は掛けていたのに、`wall.Wall` だけが
    落としていた。**20日保有・毎日エントリーなら理屈の上で4倍前後**なので、
    壁が数倍低く出る——**緩む向き**である。
    """

    def test_the_estimate_delegates(self) -> None:
        import inspect

        from stock_ai.backtest.power import PowerEstimate

        assert "standard_error(" in inspect.getsource(PowerEstimate.standard_error)
        assert "math.sqrt(self.omega" not in inspect.getsource(PowerEstimate.standard_error)

    def test_the_shape_delegates(self) -> None:
        import inspect

        from stock_ai.backtest.passing import Shape

        body = inspect.getsource(Shape.standard_error)

        assert "standard_error(" in body
        assert "periods**0.5" not in body

    def test_the_wall_delegates(self) -> None:
        import inspect

        from stock_ai.backtest.wall import Wall

        body = inspect.getsource(Wall)

        assert "detectable_difference(" in body
        assert "math.sqrt(self.observations)" not in body

    def test_the_formula_is_what_it_says(self) -> None:
        import math

        from stock_ai.backtest.power import standard_error

        assert standard_error(0.05, 2.0, 100) == pytest.approx(0.05 * 2.0 / math.sqrt(100))

    def test_it_is_the_inverse_of_periods_needed(self) -> None:
        """**往復して同じところに戻ること。** 片方だけ直しても、ここで落ちる。"""
        from stock_ai.backtest.power import detectable_difference, periods_needed

        effect = detectable_difference(0.05, 1.5, 144, target_t=3.39)

        assert periods_needed(0.05, 1.5, effect, target_t=3.39) == 144

    def test_a_zero_inflation_is_refused(self) -> None:
        """**この検査が落ちる条件を、実際に1つ作る。**"""
        from stock_ai.backtest.power import standard_error

        with pytest.raises(ValueError, match="inflation must be positive"):
            standard_error(0.05, 0.0, 100)

    def test_no_periods_is_refused(self) -> None:
        from stock_ai.backtest.power import standard_error

        with pytest.raises(ValueError, match="periods must be at least 1"):
            standard_error(0.05, 1.0, 0)


# --- 「要る期数」と「要る年数」は、同じ標本で数える ----------------------


class TestRequirementCountsInOneFrame:
    """**期数と年数が別々の標本を指さないこと。**

    2026-09-20、ユーザーが見つけた。`_event_gate` が年数を IS の率
    （992日÷5.0年）で出しながら、「いまとの差」は OOS の 1,861 日と
    比べていた。**「6.9年 要る」と「足りている」が同じ行に並ぶ。**
    """

    def test_the_two_ratios_agree(self) -> None:
        """``periods ÷ have_periods`` と ``years ÷ have_years`` が一致する。"""
        row = power.requirement(0.012, 0.0989, 1.36, have_periods=1861, have_years=8.67)
        assert row.periods / row.have_periods == pytest.approx(row.years / row.have_years)

    @pytest.mark.parametrize("have_periods", [100, 992, 1861, 5000])
    @pytest.mark.parametrize("have_years", [1.0, 5.0, 8.67, 17.0])
    def test_the_ratios_agree_everywhere(self, have_periods: int, have_years: float) -> None:
        """どの枠でも成り立つ。**構成上そうなっている。**"""
        row = power.requirement(0.02, 0.1, 1.2, have_periods, have_years)
        assert row.periods / have_periods == pytest.approx(row.years / have_years)

    def test_the_rate_is_not_the_estimation_sample(self) -> None:
        """**手元の率を変えれば年数が動く。** IS の件数では動かない。

        直す前のコードは ``len(values) / span_years`` で率を作っていたので、
        `periods` を替えても年数の列は動かなかった。
        """
        narrow = power.requirement(0.012, 0.0989, 1.36, have_periods=992, have_years=5.0)
        wide = power.requirement(0.012, 0.0989, 1.36, have_periods=1861, have_years=8.67)
        assert narrow.periods == wide.periods
        assert narrow.years != pytest.approx(wide.years)

    def test_enough_matches_the_counts(self) -> None:
        """``enough`` は期数だけで決まる。年数とずれない。"""
        row = power.requirement(0.012, 0.0989, 1.36, have_periods=1861, have_years=8.67)
        assert row.enough is (row.periods <= row.have_periods)
        assert row.enough is (row.years <= row.have_years + 1e-9)
        assert row.shortfall == 0

    def test_shortfall_is_positive_when_it_is_short(self) -> None:
        """足りないときだけ差が出る。"""
        row = power.requirement(0.004, 0.0989, 1.36, have_periods=1861, have_years=8.67)
        assert not row.enough
        assert row.shortfall == row.periods - row.have_periods
        assert row.years > row.have_years

    def test_mixed_frames_are_refused_when_built_by_hand(self) -> None:
        """**枠を混ぜた行は、作った時点で落ちる。**"""
        with pytest.raises(ValueError, match="別々の標本"):
            power.Requirement(
                effect=0.012,
                periods=1375,
                years=6.9,  # IS の率で出した年数
                have_periods=1861,  # OOS の日数
                have_years=8.67,
            )

    def test_a_consistent_row_is_accepted(self) -> None:
        """同じ枠で数えた行は通る。**落ちようのない検査にしない。**"""
        power.Requirement(
            effect=0.012,
            periods=1375,
            years=1375 / (1861 / 8.67),
            have_periods=1861,
            have_years=8.67,
        )

    @pytest.mark.parametrize(("periods", "years"), [(0, 1.0), (10, 0.0), (-1, 1.0), (10, -1.0)])
    def test_an_empty_frame_is_refused(self, periods: int, years: float) -> None:
        """手元が 0 なら率が作れない。"""
        with pytest.raises(ValueError, match="must be positive"):
            power.Requirement(
                effect=0.01, periods=1, years=1.0, have_periods=periods, have_years=years
            )

    def test_requirement_refuses_a_zero_span(self) -> None:
        """年数 0 で呼ばれたら、0 割りではなく例外にする。"""
        with pytest.raises(ValueError, match="have_years"):
            power.requirement(0.01, 0.1, 1.0, have_periods=100, have_years=0.0)

    def test_it_agrees_with_periods_needed(self) -> None:
        """**期数そのものは `periods_needed` のまま。** 2つ目の式を書かない。"""
        row = power.requirement(0.012, 0.0989, 1.36, 1861, 8.67, target_t=3.30)
        assert row.periods == power.periods_needed(0.0989, 1.36, 0.012, 3.30)


# --- 膨張の床 ---------------------------------------------------------------
#
# **`calibrated_t` には最初から在った規則が、標準誤差の側に無かった**
# （2026-09-21）。実データで候補6が 0.82x を出し、**壁が 18% 低く出た。**
# 補正は足りない分を足すためのもので、割り引くためのものではない。


def test_an_inflation_below_one_does_not_shrink_the_standard_error() -> None:
    """**1.0 を下回らせない。** 割り引く向きには効かせない。"""
    from stock_ai.backtest.power import standard_error

    assert standard_error(0.05, 0.82, 100) == pytest.approx(standard_error(0.05, 1.0, 100))


def test_an_inflation_above_one_still_widens_it() -> None:
    """**両向きに置く。** 床がすべてを潰す形でも緑にならないように。"""
    from stock_ai.backtest.power import standard_error

    assert standard_error(0.05, 2.0, 100) == pytest.approx(2.0 * standard_error(0.05, 1.0, 100))


def test_the_detectable_difference_uses_the_same_floor() -> None:
    """**式は1つだけ。** 検出できる差も同じ床を通る。"""
    from stock_ai.backtest.power import detectable_difference

    assert detectable_difference(0.05, 0.5, 100, 3.0) == pytest.approx(
        detectable_difference(0.05, 1.0, 100, 3.0)
    )


def test_the_floor_is_written_in_one_place() -> None:
    """**同じ規則を2箇所に書かない。** それで片方が落ちた。"""
    import inspect

    from stock_ai.backtest import power

    source = inspect.getsource(power)

    # `standard_error` の中に1回だけ。`judge` は通すだけである。
    assert source.count("max(inflation, INFLATION_FLOOR)") == 1
    assert "math.sqrt(long_run_variance(gammas) / count)" not in source


# --- ラグが標本より長い ------------------------------------------------------
#
# `autocovariances` は `min(lags, n-1)` に**黙って**切り詰める。12 個の観測に
# 20 ラグを頼んでも例外は出ず、**いちばん長いラグが1組の積**からできた値が
# 返る。**それが 1.0 を割った。**


def test_more_lags_than_observations_is_flagged() -> None:
    """**黙って切り詰めない。** 当てにならないことを言う。"""
    from stock_ai.backtest.power import estimate_power

    estimate = estimate_power([0.01, -0.02, 0.03, -0.01, 0.02] * 2, lags=20)

    assert estimate.undersampled
    assert estimate.requested_lags == 20  # noqa: PLR2004 - 頼んだ数を覚えていること


def test_enough_observations_is_not_flagged() -> None:
    """**両向きに置く。** 常に点く旗は、何も区別しない。"""
    from stock_ai.backtest.power import estimate_power

    estimate = estimate_power([0.01, -0.005, 0.02, -0.01] * 50, lags=5)

    assert not estimate.undersampled


def test_no_lags_is_never_undersampled() -> None:
    """重なりを見ない設計（#3・#14）で旗が立たないこと。"""
    from stock_ai.backtest.power import estimate_power

    assert not estimate_power([0.01, -0.02, 0.03], lags=0).undersampled


def test_just_longer_than_the_lag_is_still_undersampled() -> None:
    """**「ラグより長ければよい」では足りない。**

    ラグ 20 に標本 21 でも、**いちばん長いラグは1組の積**からできている。
    線は `SAMPLE_PER_LAG` で、**出典の無い決めの値**である。
    """
    from stock_ai.backtest.power import SAMPLE_PER_LAG, estimate_power, sample_needed

    estimate = estimate_power([0.01, -0.02, 0.03] * 7, lags=20)

    assert estimate.observations == 21  # noqa: PLR2004 - ラグ 20 より1つ長い
    assert estimate.undersampled
    assert estimate.needed == 20 * SAMPLE_PER_LAG
    assert sample_needed(20) == estimate.needed


def test_the_requirement_scales_with_the_lag() -> None:
    """**要る数はラグに比例する。** 式は1箇所だけ。"""
    from stock_ai.backtest.power import sample_needed

    assert sample_needed(1) * 20 == sample_needed(20)
    assert sample_needed(0) == 0


def test_the_existing_event_designs_are_not_newly_flagged() -> None:
    """**常に点く旗は、何も区別しない。**

    #9・#10 の IS は1,000日を超えるので、この線では鳴らない。
    """
    from stock_ai.backtest.power import estimate_power

    assert not estimate_power([0.01, -0.02, 0.03, -0.01] * 300, lags=20).undersampled


def test_the_warning_says_how_many_pairs_the_longest_lag_uses() -> None:
    """**札が、数えているものと違うことを言っていた。**

    「いちばん長いラグが1組の積からできている」と書いていたが、
    **標本 147・ラグ 20 なら 127 組**である（2026-09-21、実データで出た）。
    `n <= lags` のときの文面が残っていた。
    """
    from stock_ai.backtest.power import estimate_power

    estimate = estimate_power([0.01, -0.02, 0.03] * 49, lags=20)

    assert estimate.observations == 147  # noqa: PLR2004 - 実データと同じ形
    assert estimate.longest_pairs == 127  # noqa: PLR2004 - 147 − 20
    assert estimate.per_lag == pytest.approx(147 / 20)
    assert estimate.undersampled, "**決めた線（10倍）に届いていない。**"


def test_the_pairs_never_go_negative() -> None:
    """ラグが標本より長くても、0 で止まること。"""
    from stock_ai.backtest.power import estimate_power

    assert estimate_power([0.01, -0.02, 0.03], lags=20).longest_pairs == 0


def test_no_lags_reports_no_ratio_rather_than_dividing() -> None:
    from stock_ai.backtest.power import estimate_power

    assert math.isinf(estimate_power([0.01, -0.02, 0.03], lags=0).per_lag)


# --- 重なりの膨張の上限 ------------------------------------------------------
#
# **推定できないときは、推定値の代わりに上限を置く**（2026-09-21、ユーザーの
# 案）。当てにならない推定を掛けるより正直である。


def _overlap_inflation(window: int, spacing: int, days: int = 200_000, seed: int = 11) -> float:
    """独立な日次から重なる窓を作り、**平均の SD の膨張を実測する。**

    **理屈の値を書き写さない。** ここで作って突き合わせる。
    """
    rng = np.random.default_rng(seed)
    daily = rng.normal(0.0, 1.0, days)
    cumulative = np.concatenate([[0.0], np.cumsum(daily)])
    starts = np.arange(0, days - window, spacing)
    values = cumulative[starts + window] - cumulative[starts]
    chunks = 200
    per = len(values) // chunks
    means = np.array([values[index * per : (index + 1) * per].mean() for index in range(chunks)])
    return float(means.std(ddof=1) / (values.std(ddof=1) / np.sqrt(per)))


def test_the_ceiling_is_the_square_root_of_the_window() -> None:
    """**毎日入るときの膨張が ``√W`` であること。** 測って確かめる。"""
    from stock_ai.backtest.power import overlap_ceiling

    assert overlap_ceiling(20) == pytest.approx(_overlap_inflation(20, 1), rel=0.05)
    assert overlap_ceiling(10) == pytest.approx(_overlap_inflation(10, 1), rel=0.10)


def test_no_entry_pattern_exceeds_the_ceiling() -> None:
    """**上限であること。** 間隔を空けるほど膨張は下がる。"""
    from stock_ai.backtest.power import overlap_ceiling

    for spacing in (1, 5, 10):
        assert _overlap_inflation(20, spacing) <= overlap_ceiling(20) * 1.05


def test_the_spacing_formula_is_not_a_ceiling() -> None:
    """**`√(W/s)` を採らなかった理由。** 実測がそれを超える。

    W=20・s=5 で実測 2.22 に対し `√(W/s)` は 2.00 だった（2026-09-21）。
    **超えるものを「上限」と呼ばない。**
    """
    assert _overlap_inflation(20, 5) > np.sqrt(20 / 5)


def test_newey_west_runs_low_in_the_same_setting() -> None:
    """**推定量そのものが、真の値より低く出る。** 緩む向きである。"""
    from stock_ai.backtest.power import estimate_power

    rng = np.random.default_rng(5)
    daily = rng.normal(0.0, 1.0, 60_000)
    cumulative = np.concatenate([[0.0], np.cumsum(daily)])
    starts = np.arange(0, 60_000 - 20)
    series = list(cumulative[starts + 20] - cumulative[starts])

    assert estimate_power(series, lags=20).inflation < _overlap_inflation(20, 1)


def test_a_window_that_cannot_overlap_has_no_ceiling() -> None:
    """**重ならない設計には当てない。** 常に点く旗は何も区別しない。"""
    from stock_ai.backtest.power import overlap_ceiling

    assert overlap_ceiling(1) == pytest.approx(1.0)
    assert overlap_ceiling(0) == pytest.approx(1.0)
