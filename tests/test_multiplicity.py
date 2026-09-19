"""何本試したかを、合格の線に反映させる。

**当たりを引こうとした回数が多いほど、まぐれ当たりも増える。** 20本試せば、
本当は何も無くても1本は両側5%を越える。`docs/PURPOSE.md` の「合格判定では
多重検定を考慮する」はそのことである。

ここで押さえるのは2つ。

1. **すでに封印した説の線は動かさない。** 本数が増えたから遡って上げるのは、
   判定が出たあとに基準を変えることである。
2. **「いま何本目か」で線を決めない。** 本数は増えるので、封印のたびに線が
   動く。**先に予算を決めて割る。**
"""

from __future__ import annotations

import pytest

from stock_ai.backtest.multiplicity import (
    FAMILY_ALPHA,
    HYPOTHESIS_BUDGET,
    adjust,
    ladder,
    required_t,
)
from stock_ai.backtest.power import TARGET_T


class TestTheUncorrectedLineIsWhereItAlwaysWas:
    def test_a_budget_of_one_is_the_two_sided_five_percent_line(self) -> None:
        assert abs(required_t(1) - 1.96) < 0.01

    def test_that_is_what_the_project_has_been_using(self) -> None:
        """`power.TARGET_T` は「両側5%のおおよその臨界値」と書いてある。"""
        assert abs(required_t(1) - TARGET_T) < 0.05


class TestSpendingTheBudgetRaisesTheLine:
    def test_more_hypotheses_need_a_higher_t(self) -> None:
        lines = [required_t(budget) for budget in (1, 5, 10, 20, 50)]

        assert lines == sorted(lines)
        assert lines[0] < lines[-1]

    def test_the_alpha_is_split_evenly(self) -> None:
        assert abs(adjust(20).alpha - FAMILY_ALPHA / 20) < 1e-12

    def test_the_cost_is_reported_as_well_as_the_line(self) -> None:
        """**代償を数字で見せる。** 線だけ出すと、どこから来たか分からない。"""
        step = adjust(20)

        assert step.cost_in_t > 1.0
        assert abs(step.cost_in_t - (step.required_t - required_t(1))) < 1e-12

    def test_twenty_hypotheses_land_near_three(self) -> None:
        """**これは高い線である。** 黙って入れてよい変更ではない。"""
        assert 2.9 < required_t(20) < 3.1


class TestRefusingNonsense:
    def test_a_budget_below_one_is_refused(self) -> None:
        with pytest.raises(ValueError):
            required_t(0)

    def test_an_alpha_outside_zero_to_one_is_refused(self) -> None:
        for bad in (0.0, 1.0, -0.1, 1.5):
            with pytest.raises(ValueError):
                required_t(5, alpha=bad)


class TestTheLadderMakesTheChoiceVisible:
    def test_it_starts_from_no_correction(self) -> None:
        """**比べる相手が無いと、線の高さが読めない。**"""
        assert ladder()[0].budget == 1
        assert ladder()[0].cost_in_t == 0.0

    def test_every_step_is_more_demanding_than_the_last(self) -> None:
        steps = ladder()

        assert [step.budget for step in steps] == sorted(step.budget for step in steps)
        assert all(
            later.required_t > earlier.required_t
            for earlier, later in zip(steps, steps[1:], strict=False)
        )

    def test_the_summary_names_both_the_line_and_what_it_replaced(self) -> None:
        text = adjust(10).summary()

        assert "10" in text
        assert "2.81" in text
        assert "1.96" in text


class TestTheBudgetThisProjectChose:
    """**予算 20 本、両側 5%。2026-09-16 に決めた。**

    判定を消費したのが 5 本、登録して未判定が 3 本。複合型は組み合わせ1通りに
    つき1本なので、本数はこれから速く増える。20 ならあと 12 本ぶん残る。

    **小さく取ると、超えた日に予算を取り直すことになる。** それは線を動かす
    ことで、このプロジェクトがいちばん嫌う形である。
    """

    def test_the_budget_is_twenty(self) -> None:
        assert HYPOTHESIS_BUDGET == 20

    def test_it_lands_just_above_three(self) -> None:
        """**高い線である。** 文献のアノマリーの多くは §0 を通らない。"""
        assert 3.0 < required_t(HYPOTHESIS_BUDGET) < 3.05

    def test_it_leaves_room_beyond_what_is_already_registered(self) -> None:
        """登録済み 8 本を使い切っていないこと。**超えた日に取り直さないため。**"""
        assert HYPOTHESIS_BUDGET > 8

    def test_loosening_means_raising_alpha_not_shrinking_the_budget(self) -> None:
        """**予算を縮めるのは「何本試すつもりか」を偽ること。**

        α を上げるのは「どれだけの誤りを許すか」を決め直すことで、そちらは
        正直に書ける。どちらも線を下げるが、意味が違う。
        """
        honest = required_t(HYPOTHESIS_BUDGET, alpha=0.10)
        dishonest = required_t(5, alpha=FAMILY_ALPHA)

        assert honest < required_t(HYPOTHESIS_BUDGET)
        assert dishonest < required_t(HYPOTHESIS_BUDGET)


class TestTheLineWhenThereAreOnlyNineObservations:
    """**`t` の SD から線を引いてよいのは、自由度が大きいときだけ。**

    既存の3管は陰性対照で測った `t` の SD を正規分布の分位点に掛けている。
    自由度 100 以上ならそれでよい（3.10 と 3.05）。**年1観測の管（#14）は
    自由度 8 で、SD 方式が 24% 甘くなる。**

    **緩める向きの取り違えである。** 線が低ければ検出できる差も小さく出るので、
    **通ってはいけない設計が §0 を通る**（`power-gate` で1度踏んだ形）。
    """

    def test_the_known_values(self) -> None:
        """**書き写していない。** 閉じた式から出る値である。"""
        from stock_ai.backtest.multiplicity import student_t_line

        assert student_t_line(8) == pytest.approx(4.333, abs=0.002)
        assert student_t_line(103) == pytest.approx(3.099, abs=0.002)
        assert student_t_line(210) == pytest.approx(3.060, abs=0.002)

    def test_it_approaches_the_normal_line_as_the_sample_grows(self) -> None:
        """**自由度が大きければ正規と一致する。** ここがずれたら式が違う。"""
        from stock_ai.backtest.multiplicity import student_t_line

        assert student_t_line(100_000) == pytest.approx(required_t(HYPOTHESIS_BUDGET), abs=0.001)

    def test_fewer_observations_need_a_higher_line(self) -> None:
        from stock_ai.backtest.multiplicity import student_t_line

        lines = [student_t_line(df) for df in (8, 20, 50, 200)]

        assert lines == sorted(lines, reverse=True)

    def test_the_sd_method_is_too_lenient_at_eight(self) -> None:
        """**24% 甘い。** これがこの管で SD 方式を使わない理由である。"""
        import math

        from stock_ai.backtest.multiplicity import student_t_line

        sd_method = required_t(HYPOTHESIS_BUDGET) * math.sqrt(8 / 6)

        assert student_t_line(8) > sd_method
        assert student_t_line(8) / sd_method == pytest.approx(1.24, abs=0.01)

    def test_the_sd_method_is_fine_where_the_existing_pipes_live(self) -> None:
        """**遡って直す話ではない。** 自由度 103 なら差は 2% 未満。"""
        import math

        from stock_ai.backtest.multiplicity import student_t_line

        sd_method = required_t(HYPOTHESIS_BUDGET) * math.sqrt(103 / 101)

        assert student_t_line(103) / sd_method == pytest.approx(1.0, abs=0.02)

    def test_the_cauchy_case_is_right(self) -> None:
        """**自由度1は閉じた形で確かめられる。** `tan` で逆算できる。"""
        import math

        from stock_ai.backtest.multiplicity import student_t_line

        expected = math.tan(math.pi / 2 * (1.0 - FAMILY_ALPHA / HYPOTHESIS_BUDGET))

        assert student_t_line(1) == pytest.approx(expected, rel=1e-6)

    def test_two_degrees_of_freedom_matches_the_closed_form(self) -> None:
        """**自由度2も閉じた形が在る。** 別の切り口で同じ式を確かめる。"""
        import math

        from stock_ai.backtest.multiplicity import student_t_line

        share = 1.0 - FAMILY_ALPHA / HYPOTHESIS_BUDGET
        expected = math.sqrt(2.0 * share**2 / (1.0 - share**2))

        assert student_t_line(2) == pytest.approx(expected, rel=1e-6)

    def test_the_tail_probability_is_what_was_asked_for(self) -> None:
        """**分位点を、確率のほうから確かめる。** 逆問題を解いている。"""
        from stock_ai.backtest.multiplicity import _abs_t_cdf, student_t_line

        for df in (3, 8, 40):
            line = student_t_line(df)

            assert _abs_t_cdf(line, df) == pytest.approx(
                1.0 - FAMILY_ALPHA / HYPOTHESIS_BUDGET, abs=1e-9
            )

    def test_a_negative_t_is_refused(self) -> None:
        """**この検査が落ちる条件を、実際に1つ作る。**"""
        from stock_ai.backtest.multiplicity import _abs_t_cdf

        with pytest.raises(ValueError, match="must not be negative"):
            _abs_t_cdf(-1.0, 8)

    def test_a_degenerate_sample_is_refused(self) -> None:
        from stock_ai.backtest.multiplicity import student_t_line

        with pytest.raises(ValueError, match="df must be at least 1"):
            student_t_line(0)
