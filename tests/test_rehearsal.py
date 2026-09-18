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


class TestTheEventTypeControlUsesTheSamePipe:
    """**月次で測った 1.12 が、イベント型に当てはまるとは限らない。**

    #8・#5 は系列がイベント日ごとで、Newey-West のラグも保有日数に取ってある。
    **別の管には別の数字がありうる。**
    """

    @staticmethod
    def _days(count: int = 50):
        import datetime as dt

        return [dt.date(2020, 1, 6) + dt.timedelta(days=offset) for offset in range(count)]

    def test_the_draw_reproduces_from_its_seed(self) -> None:
        from stock_ai.backtest.rehearsal import placebo_events

        days = self._days()

        assert placebo_events(days, ["1301", "1302"], 20, seed=3) == placebo_events(
            days, ["1301", "1302"], 20, seed=3
        )

    def test_a_different_seed_draws_differently(self) -> None:
        from stock_ai.backtest.rehearsal import placebo_events

        days = self._days()

        assert placebo_events(days, ["1301", "1302"], 20, seed=3) != placebo_events(
            days, ["1301", "1302"], 20, seed=4
        )

    def test_it_draws_the_number_asked_for(self) -> None:
        from stock_ai.backtest.rehearsal import placebo_events

        assert len(placebo_events(self._days(), ["1301"], 37, seed=1)) == 37

    def test_the_same_day_can_come_up_twice(self) -> None:
        """**本物も同じ日に複数出る。** 重複を禁じると、固まり方が本物と変わる。"""
        from stock_ai.backtest.rehearsal import placebo_events

        drawn = placebo_events(self._days(count=3), ["1301", "1302"], 60, seed=1)

        assert len(drawn) > len(set(drawn))

    def test_it_only_picks_from_what_it_was_given(self) -> None:
        from stock_ai.backtest.rehearsal import placebo_events

        days = self._days()
        drawn = placebo_events(days, ["1301", "1302"], 40, seed=1)

        assert {symbol for symbol, _day in drawn} <= {"1301", "1302"}
        assert {day for _symbol, day in drawn} <= set(days)

    def test_an_empty_pool_is_an_error_not_an_empty_draw(self) -> None:
        """**「引けなかった」を「イベントが無い」に化けさせない。**"""
        from stock_ai.backtest.rehearsal import placebo_events

        with pytest.raises(ValueError):
            placebo_events([], ["1301"], 10)
        with pytest.raises(ValueError):
            placebo_events(self._days(), [], 10)

    def test_the_command_calls_the_real_event_window(self) -> None:
        """**別の管を作ったら、確かめたことにならない。**"""
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli.rehearsal_events)

        assert "from stock_ai.backtest.event_window import EventSample, event_sample" in body
        assert "event_sample(" in body
        assert "drawn," in body

    def test_the_hypotheses_call_the_same_function(self) -> None:
        """**対照が本物と同じ管を通るとは、#5・#8 も同じ口を呼ぶということ。**

        文字列で書き方を留めると、改行が入っただけで落ちる。**留めるのは
        「どこから import しているか」**——別の管が生えたらそこに出る。
        """
        import inspect

        from stock_ai import cli

        for command in (cli.rehearsal_events, cli.revision_power, cli.margin_power):
            body = inspect.getsource(command)
            assert "from stock_ai.backtest.event_window import" in body, command.__name__
            assert "event_sample" in body, command.__name__

    def test_it_says_so_when_the_two_pipes_disagree(self) -> None:
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli.rehearsal_events)

        assert "別の数字を当てるべき" in body


class TestTheCalibratedLineIsUsedEverywhere:
    """**測って厳しくした線を、1箇所でも忘れると意味が無い。**"""

    def test_the_calibrated_line_is_above_the_plain_one(self) -> None:
        from stock_ai.backtest.multiplicity import (
            HYPOTHESIS_BUDGET,
            calibrated_t,
            required_t,
        )

        assert calibrated_t(HYPOTHESIS_BUDGET) > required_t(HYPOTHESIS_BUDGET)

    def test_an_inflation_of_one_changes_nothing(self) -> None:
        from stock_ai.backtest.multiplicity import calibrated_t, required_t

        assert calibrated_t(20, inflation=1.0) == pytest.approx(required_t(20))

    def test_a_zero_inflation_is_refused(self) -> None:
        from stock_ai.backtest.multiplicity import calibrated_t

        with pytest.raises(ValueError):
            calibrated_t(20, inflation=0.0)

    def test_no_gate_still_uses_the_uncalibrated_line(self) -> None:
        """**素の線で判定している場所が残っていないこと。**

        `required_t` は表示（素の線がいくつだったか）にだけ使ってよい。
        """
        import pathlib
        import re

        body = pathlib.Path("src/stock_ai/cli.py").read_text(encoding="utf-8")
        assigned = re.findall(r"target = (\w+)\(HYPOTHESIS_BUDGET\)", body)

        assert assigned, "判定の線を置いている場所が見つからない"
        assert set(assigned) == {"calibrated_t"}, assigned

    def test_the_measured_inflation_says_where_it_came_from(self) -> None:
        """**出典の無い数字を書かない。** 400回の対照から出た値である。"""
        import inspect

        from stock_ai.backtest import multiplicity

        source = inspect.getsource(multiplicity)

        assert "陰性対照" in source
        assert "400" in source
        assert "月次・α・低ボラ universe" in source


class TestTheFloorStopsTheCorrectionFromLoosening:
    """**補正は足りない分を足すためのもので、割り引くためのものではない。**

    イベント型の実測は 0.94 だった。当てれば線が 3.02 → 2.85 に緩む。
    **緩める根拠になった測定が、同時に未解決の偏り（`t` の平均 +0.49）を出して
    いる。**
    """

    def test_a_measurement_below_one_does_not_loosen(self) -> None:
        from stock_ai.backtest.multiplicity import calibrated_t, required_t

        assert calibrated_t(20, inflation=0.94) == pytest.approx(required_t(20))

    def test_a_measurement_above_one_still_tightens(self) -> None:
        from stock_ai.backtest.multiplicity import calibrated_t, required_t

        assert calibrated_t(20, inflation=1.12) > required_t(20)

    def test_the_two_pipes_have_their_own_numbers(self) -> None:
        from stock_ai.backtest.multiplicity import (
            MEASURED_INFLATION,
            MEASURED_INFLATION_EVENT,
        )

        assert MEASURED_INFLATION != MEASURED_INFLATION_EVENT

    def test_the_event_gates_use_the_event_number(self) -> None:
        """**月次の 1.12 をイベント型に当てるのは、測った根拠の無い厳しさ。**"""
        import inspect

        from stock_ai import cli

        for command in (cli.revision_power, cli.margin_power):
            body = inspect.getsource(command)
            assert "inflation=MEASURED_INFLATION_EVENT" in body, command.__name__

    def test_both_measured_numbers_say_where_they_came_from(self) -> None:
        import inspect

        from stock_ai.backtest import multiplicity

        source = inspect.getsource(multiplicity)

        assert "イベント型の対照" in source
        assert "平均が +0.49" in source


class TestTheWatchLooksAtTheCentreNotOnlyTheSpread:
    """**散らばりが素直でも、中心がずれていれば判定は歪む。**

    最初は SD しか警告にしていなかった。`t` の平均が +0.49 出ているのに、
    **表の1行に出しただけで素通りさせ、線を緩める向きに促した**（2026-09-17）。
    """

    def test_a_shifted_null_is_called_out(self) -> None:
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli.rehearsal_events)

        assert "found.mean" in body
        assert "中心のずれ" in body

    def test_it_reads_the_decomposition_instead_of_naming_a_suspect(self) -> None:
        """**決め打ちの犯人を刷らない。**

        最初は「窓の途中で価格が途切れるイベントが落ちるのがいちばん疑わしい」
        と刷っていた。**同じ出力の上に分解が出ているのに、である。**
        そして外れた——生存フィルタの押し上げは **-0.00%/件** だった
        （2026-09-17、400回）。

        **表が否定しているものを、その下の行が断定する**形になっていた。
        読み上げるのは、測った分解のほうである。
        """
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli.rehearsal_events)

        # 分解の両側を読み上げていること。
        assert "lifted" in body
        assert "gap" in body
        assert "大きいほうが、直すべきほうである" in body
        # **決め打ちの犯人が戻っていないこと。**
        assert "いちばん疑わしいのは" not in body
