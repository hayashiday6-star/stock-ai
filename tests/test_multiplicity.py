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
