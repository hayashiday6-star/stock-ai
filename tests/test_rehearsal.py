"""陰性対照（`stock_ai.backtest.rehearsal`）。

**このプロジェクトには対照が1本も無かった。** 関門も判定の当てはめも、本物の
データの上でしか試していない。

ここで固定するのは、**対照が対照であり続けること**である。

- signal だけが乱数で、**リターンは本物のまま**（全部乱数にすると、重なりも
  自己相関も消えて、いちばん確かめたい部分が消える）
- 種で**再現する**（変えれば違う答えが出るので、記録に残せなければ意味が無い）
- **予算に数えない**（世界について何も主張していない）
"""

from __future__ import annotations

import pytest

from stock_ai.backtest.rehearsal import (
    CALIBRATION_LIMIT,
    SEED,
    calibrate,
    placebo_sections,
)

_SECTIONS = [
    [((0.1,), 0.02), ((0.2,), -0.03), ((0.3,), 0.01)],
    [((0.4,), 0.04), ((0.5,), -0.01), ((0.6,), 0.02)],
]


class TestThePlaceboKeepsWhatItMustKeep:
    def test_the_returns_are_the_real_ones(self) -> None:
        """**全部を乱数にすると、重なりも自己相関も消える。**"""
        built = placebo_sections(_SECTIONS, seed=SEED)

        for month, source in zip(built, _SECTIONS, strict=True):
            assert [forward for _signal, forward in month] == [
                forward for _signals, forward in source
            ]

    def test_the_signal_is_not_the_real_one(self) -> None:
        built = placebo_sections(_SECTIONS, seed=SEED)

        assert [signal for signal, _forward in built[0]] != [0.1, 0.2, 0.3]

    def test_the_same_seed_gives_the_same_draw(self) -> None:
        """**再現できなければ、記録に残す意味が無い。**"""
        assert placebo_sections(_SECTIONS, seed=7) == placebo_sections(_SECTIONS, seed=7)

    def test_a_different_seed_gives_a_different_draw(self) -> None:
        assert placebo_sections(_SECTIONS, seed=7) != placebo_sections(_SECTIONS, seed=8)

    def test_the_shape_is_kept(self) -> None:
        built = placebo_sections(_SECTIONS, seed=SEED)

        assert [len(month) for month in built] == [len(month) for month in _SECTIONS]

    def test_an_empty_month_survives(self) -> None:
        """**月を落とさない。** 落とすと本物と月数が合わなくなる。"""
        built = placebo_sections([[], _SECTIONS[0]], seed=SEED)

        assert built[0] == []
        assert len(built[1]) == 3


class TestTheCalibrationLooksAtTheShapeNotTheTail:
    """**1回では校正できない。** `t ≥ 3.02` は 0.125% で、400回の期待値が 0.5。"""

    def test_a_well_behaved_null_is_called_calibrated(self) -> None:
        import numpy as np

        draws = list(np.random.default_rng(0).normal(size=500))

        found = calibrate(draws, 3.02)

        assert found.calibrated
        assert found.spread == pytest.approx(1.0, abs=0.15)
        assert found.plain_share == pytest.approx(0.05, abs=0.03)

    def test_an_inflated_null_is_caught(self) -> None:
        """**SD が 1 より大きければ、線は見かけより甘い。**"""
        import numpy as np

        draws = list(np.random.default_rng(0).normal(scale=1.4, size=500))

        found = calibrate(draws, 3.02)

        assert not found.calibrated
        assert any("補正が足りていない" in line for line in found.warnings())

    def test_the_implied_level_is_looser_when_the_spread_is_wider(self) -> None:
        tight = calibrate([1.0, -1.0] * 50, 3.02)
        wide = calibrate([1.4, -1.4] * 50, 3.02)

        assert wide.implied_level(3.02) > tight.implied_level(3.02)

    def test_the_designed_level_comes_back_at_a_spread_of_one(self) -> None:
        """**帰無で SD 1.0 なら、`t ≥ 3.02` はちょうど 0.25% である。**"""
        found = calibrate([1.0, -1.0] * 50, 3.02)

        assert found.implied_level(3.02) == pytest.approx(0.0025, abs=0.0003)

    def test_too_few_runs_says_so(self) -> None:
        found = calibrate([0.1, -0.2, 0.3], 3.02)

        assert any("形が読めない" in line for line in found.warnings())

    def test_no_runs_at_all_says_so(self) -> None:
        assert any("1回も回していない" in line for line in calibrate([], 3.02).warnings())

    def test_the_limit_has_no_measured_backing_and_says_so(self) -> None:
        """**1.10 に根拠は無い。** 目安であって、測って出した値ではない。"""
        import inspect

        from stock_ai.backtest import rehearsal

        assert CALIBRATION_LIMIT == 1.10
        assert "根拠は無い" in inspect.getsource(rehearsal)


class TestTheControlIsNotCountedInTheBudget:
    """**世界について何も主張していないので、当たりを引こうとした回数に入らない。**"""

    @staticmethod
    def _hypothesis(composition: str):
        from stock_ai.hypotheses import Hypothesis

        return Hypothesis(
            identifier="X",
            number="0",
            title="t",
            kind="technical",
            composition=composition,
            market="jp",
            source="",
            prereg="",
            verdict="不合格",
            sealed_on="",
            one_line="",
        )

    def test_a_control_is_excluded(self) -> None:
        assert not self._hypothesis("control").counted

    def test_a_real_hypothesis_is_counted(self) -> None:
        assert self._hypothesis("single").counted

    def test_the_split_is_by_composition_not_by_verdict(self) -> None:
        """**判定の欄で分けると、対照の合格が本物の合格に混ざる。**"""
        control = self._hypothesis("control")

        assert control.judged, "対照にも判定は出る"
        assert not control.counted


class TestTheTwoHalvesAreDrawnIndependently:
    """**同じ種を両方に使わない。** 乱数の流れが共有されると独立でなくなる。

    最初はそうしていて、IS +1.24・OOS +1.29 が揃って見えた——**偶然か共有の
    せいかを区別できなかった**（2026-09-17）。
    """

    def test_the_out_of_sample_seed_differs(self) -> None:
        from stock_ai.backtest.rehearsal import oos_seed

        assert oos_seed(SEED) != SEED

    def test_the_offset_is_big_enough_to_not_collide_with_repeats(self) -> None:
        """**`--repeat 400` は種を 400 ずらす。** そこにぶつからないこと。"""
        from stock_ai.backtest.rehearsal import OOS_OFFSET

        assert OOS_OFFSET > 100_000

    def test_the_seed_is_reproducible_by_hand(self) -> None:
        """**`spawn` にしない。** 種を記録すれば手で再現できるようにする。"""
        from stock_ai.backtest.rehearsal import OOS_OFFSET, oos_seed

        assert oos_seed(123) == 123 + OOS_OFFSET

    def test_the_command_uses_different_streams_for_the_two_halves(self) -> None:
        """**部品が正しくても、呼ぶ側が同じ種を渡せば意味が無い。**"""
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli.rehearsal)

        assert "score(inside, seed)" in body
        assert "score(outside, oos_seed(seed))" in body
