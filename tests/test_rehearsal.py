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

import dataclasses

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

    def test_the_only_places_without_a_calibrated_line_are_named(self) -> None:
        """**例外は数えられるだけで、どれも名前で分かること。**

        校正済みの線を持てない場所が2つある。**どちらも、まだ測っていない
        ものを測る前に当てられない**という同じ理由である。

        - `plain_line`（`rehearsal_calendar`）——**線を作る側の対照**である
        - `line_floor`（`january_power`）——**校正していない管の、線の下限。**
          膨張には下限 1.0 があるので（`INFLATION_FLOOR`）線はこれより下がら
          ない。**ここで通らないなら、対照を回しても通らない**——だから対照を
          回す前に §0 を当てられる

        **どちらも `target` と名付けない。** 判定に見える名前を、判定でない
        ものに付けない。**3つ目が現れたら、ここが落ちる。**
        """
        import pathlib
        import re

        body = pathlib.Path("src/stock_ai/cli.py").read_text(encoding="utf-8")
        plain = re.findall(r"(\w+) = required_t\(HYPOTHESIS_BUDGET\)", body)

        assert sorted(plain) == ["line_floor", "plain_line"], plain
        assert len(plain) == 2, plain

    def test_the_floor_is_not_used_as_the_judging_line(self) -> None:
        """**下限は「通らなければ閉じる」側にしか使わない。**

        #14 は**この管をまだ校正していない**。下限で通っただけで封印に進むと、
        **測っていない線で判定したことになる。**
        """
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli.january_power)

        assert "対照を回しても通らない" in body
        assert "対照を回すまでもない" in body
        assert "この管の対照を回してから決める" in body

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

        for command in (cli.revision_power, cli.margin_power, cli.rehearsal_events):
            body = inspect.getsource(command)
            assert "_event_inflation(subtract)" in body, command.__name__

    def test_the_inflation_follows_what_is_actually_subtracted(self) -> None:
        """**測った条件と違う条件の数字を当てない。**

        引く相手を替えたら SD が 0.94 → 1.09 に動いた（2026-09-18、どちらも
        400回・同じ種）。**片方の数字をもう片方に当てれば、線はもっともらしい
        まま根拠を失う。**
        """
        from stock_ai.backtest.multiplicity import (
            MEASURED_INFLATION_EVENT,
            MEASURED_INFLATION_EVENT_INDEX,
        )
        from stock_ai.cli import _event_inflation

        assert _event_inflation("index") == MEASURED_INFLATION_EVENT_INDEX
        assert _event_inflation("universe") == MEASURED_INFLATION_EVENT
        assert MEASURED_INFLATION_EVENT != MEASURED_INFLATION_EVENT_INDEX

    def test_an_unknown_subtraction_is_refused(self) -> None:
        """**この検査が落ちる条件を、実際に1つ作る。**"""
        import typer

        from stock_ai.cli import _event_inflation

        with pytest.raises(typer.BadParameter):
            _event_inflation("topix")

    def test_both_measured_numbers_say_where_they_came_from(self) -> None:
        import inspect

        from stock_ai.backtest import multiplicity

        source = inspect.getsource(multiplicity)

        assert "イベント型の対照" in source
        # **どちらの測定も、条件と結果が読めること。**
        assert "+0.49" in source
        assert "時価総額加重" in source
        assert "等加重の宇宙" in source


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


class TestTheDenominatorIsShownToo:
    """**`t` だけ見ていると、分子と分母のどちらが動いたのか分からない。**

    引く相手を直したら SD が 0.94 → 1.09 に動いた。`t = 平均 / 標準誤差`
    なので、**分母の形を見ないと理由に近づけない**（2026-09-18）。
    それは既に計算されていて、捨てられていた。
    """

    def test_the_run_keeps_the_newey_west_factor(self) -> None:
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli.rehearsal_events)

        assert "spreads.append(estimate.inflation)" in body
        assert "observations.append(len(values))" in body

    def test_the_run_prints_the_denominator(self) -> None:
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli.rehearsal_events)

        assert "`t` の分母の形" in body

    def test_the_thinning_goes_through_to_the_builder(self) -> None:
        """**口を開けただけで配線を忘れる**形を止める。

        `BulkIngester` はプロバイダを差し替え可能な作りなのに、CLI 側が
        新しい設定を配線し忘れて、切り替えたはずが旧経路を叩き続けていた
        実例が複数ある（`CLAUDE.md`）。
        """
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli.rehearsal_events)

        assert "benchmark_fraction" in body
        assert "fraction=benchmark_fraction" in body
        assert "fraction=fraction" in inspect.getsource(cli._universe_to_subtract)


# --- 陽性対照 -----------------------------------------------------------------


def _month(count: int, seed: int = 0):
    import numpy as np

    rng = np.random.default_rng(seed)
    return [((float(value),), float(forward)) for value, forward in rng.normal(size=(count, 2))]


class TestThePlantedEffectIsTheOneAskedFor:
    """**大きさの分かった効果を埋める。** 分位の組み方は書き直さない。"""

    def test_zero_strength_is_the_negative_control(self) -> None:
        """**0 でも足し算の経路は通す。** 特別扱いすると、一致が確かめにならない。"""
        from stock_ai.backtest.rehearsal import planted_sections

        sections = [_month(50, 1), _month(40, 2)]

        assert planted_sections(sections, 0.0, seed=7) == placebo_sections(sections, seed=7)

    def test_the_signal_is_the_negative_controls(self) -> None:
        from stock_ai.backtest.rehearsal import planted_sections

        sections = [_month(50, 1)]
        planted = planted_sections(sections, 0.3, seed=7)
        placebo = placebo_sections(sections, seed=7)

        assert [signal for signal, _f in planted[0]] == [signal for signal, _f in placebo[0]]

    def test_the_quintile_gap_opens_by_strength_times_the_rank_gap(self) -> None:
        """**別の切り口で照合する。** 管の分位ではなく、理屈の順位の差 0.8 で。"""
        from stock_ai.backtest.rehearsal import planted_sections, rank_gap

        sections = [_month(1_000, 3)]
        planted = planted_sections(sections, 0.05, seed=7)[0]
        placebo = placebo_sections(sections, seed=7)[0]
        added = sorted(
            (signal, after - before)
            for (signal, after), (_s, before) in zip(planted, placebo, strict=True)
        )
        top = [gain for _s, gain in added[-200:]]
        bottom = [gain for _s, gain in added[:200]]

        gap = sum(top) / 200 - sum(bottom) / 200
        assert gap == pytest.approx(0.05 * rank_gap(5), rel=0.01)

    def test_a_higher_signal_gets_more(self) -> None:
        """**向きを取り違えると、効果が逆に埋まる。**"""
        from stock_ai.backtest.rehearsal import planted_sections

        sections = [_month(30, 4)]
        planted = planted_sections(sections, 0.1, seed=7)[0]
        placebo = placebo_sections(sections, seed=7)[0]
        best = max(range(30), key=lambda i: planted[i][0])
        worst = min(range(30), key=lambda i: planted[i][0])

        assert planted[best][1] - placebo[best][1] == pytest.approx(0.05)
        assert planted[worst][1] - placebo[worst][1] == pytest.approx(-0.05)

    def test_a_month_too_small_to_rank_is_kept(self) -> None:
        from stock_ai.backtest.rehearsal import planted_sections

        assert len(planted_sections([_month(1), _month(5)], 0.1, seed=7)[0]) == 1

    def test_the_rank_gap_is_one_minus_one_over_q(self) -> None:
        from stock_ai.backtest.rehearsal import rank_gap

        assert rank_gap(5) == pytest.approx(0.8)
        assert rank_gap(10) == pytest.approx(0.9)
        with pytest.raises(ValueError):
            rank_gap(1)

    def test_the_monthly_effect_inverts_the_information_ratio(self) -> None:
        """``情報比 = μ√r / σ`` を ``μ`` について解いたもの。"""
        import math

        from stock_ai.backtest.rehearsal import monthly_effect

        effect = monthly_effect(1.2, 0.04)

        assert effect * math.sqrt(12) / 0.04 == pytest.approx(1.2)


class TestThePredictionAndTheBand:
    def test_an_effect_on_the_line_is_a_coin_flip(self) -> None:
        """**要る情報比ちょうどは五分五分である。** 合格が保証される大きさではない。"""
        from stock_ai.backtest.rehearsal import predicted_share

        assert predicted_share(3.39 * 0.01, [0.01] * 5, 3.39, 1.12) == pytest.approx(0.5)

    def test_the_null_spread_is_used(self) -> None:
        """**帰無の SD を 1.0 と置くと、線に掛けた膨張を予測だけ落とす。**"""
        from stock_ai.backtest.rehearsal import predicted_share

        wide = predicted_share(0.0, [0.01], 3.39, 1.12)
        narrow = predicted_share(0.0, [0.01], 3.39, 1.0)

        assert wide > narrow

    def test_a_zero_error_is_not_counted(self) -> None:
        from stock_ai.backtest.rehearsal import predicted_share

        assert predicted_share(0.03, [0.0, 0.01], 3.0, 1.0) == pytest.approx(0.5)

    def test_the_band_at_a_coin_flip(self) -> None:
        """200回・五分五分なら ±3標準誤差は約11ポイント。"""
        from stock_ai.backtest.rehearsal import share_band

        assert share_band(0.5, 200) == pytest.approx(0.106, abs=0.001)

    def test_the_band_never_shrinks_below_one_run(self) -> None:
        """**予測がほぼ 0 のとき、1回の合格だけで「壊れている」と言わない。**"""
        from stock_ai.backtest.rehearsal import share_band

        assert share_band(0.0, 200) == pytest.approx(1 / 200)

    def test_both_conditions_can_fire(self) -> None:
        """**両向きに置く。** 落ちる条件を作って、落ちることを見る。"""
        from stock_ai.backtest.rehearsal import PositiveRow

        def row(transmission, measured):
            return PositiveRow(
                multiple=1.0,
                information_ratio=1.1,
                effect=0.01,
                transmission=transmission,
                gate_passes=True,
                predicted=0.5,
                measured=measured,
                runs=200,
            )

        assert not row(1.0, 0.52).problems()
        assert any("伝達率" in line for line in row(0.85, 0.52).problems())
        assert any("予測" in line for line in row(1.0, 0.30).problems())

    def test_the_multiples_were_decided_before_measuring(self) -> None:
        """**書いたあとには動かさない**（2026-09-26、ユーザーが承認）。"""
        from stock_ai.backtest.rehearsal import (
            PASS_BAND_SE,
            POSITIVE_MULTIPLES,
            TRANSMISSION_BAND,
        )

        assert POSITIVE_MULTIPLES == (0.0, 0.5, 1.0, 1.25, 1.5)
        assert TRANSMISSION_BAND == (0.9, 1.1)
        assert PASS_BAND_SE == 3.0


class TestThePositiveControlRunsEndToEnd:
    """**組み立てを1本通す。** 中身の入った DB で、本物のコマンドを叩く。"""

    ARGS = (
        "positive-control",
        "--is-start",
        "2022-01-03",
        "--is-end",
        "2023-06-30",
        "--oos-end",
        "2024-09-30",
        "--window",
        "60",
        "--min-symbols",
        "10",
        "--runs",
        "40",
        "--lags",
        "3",
    )

    def _run(self, monkeypatch):
        import sys
        from pathlib import Path

        from typer.testing import CliRunner

        from stock_ai import cli

        sys.path.insert(0, str(Path(__file__).parent))
        from test_factor_panel import _database

        database = _database(40)
        monkeypatch.setenv("COLUMNS", "200")
        monkeypatch.setattr(cli, "Database", lambda *a, **k: database)
        return CliRunner().invoke(cli.app, list(self.ARGS))

    def test_the_table_comes_out_and_nothing_is_broken(self, monkeypatch) -> None:
        result = self._run(monkeypatch)

        assert result.exit_code == 0, result.output
        assert "陽性対照" in result.output
        assert "要る情報比" in result.output
        for multiple in ("0.5", "1.25", "1.5"):
            assert f"│ {multiple.rjust(4)} │" in result.output
        assert "どれも当たらなかった" in result.output
        assert "陰性対照と同じ t にならない" not in result.output
        rows = {
            line.split("│")[1].strip(): line
            for line in result.output.splitlines()
            if line.startswith("│")
        }
        assert "止める" in rows["0"], "**0 を埋めた設計を §0 が通したら壊れている。**"
        assert "通す" in rows["1.5"]

    def test_the_label_shows_the_inflation_the_formula_used(self, monkeypatch) -> None:
        """**札と計算が別のことを言わない。** 測った膨張が 1.0 を下回っても、式は 1.0 を使う。

        実データで「IS の膨張 0.74」と出たのに、要る情報比 1.16 は 1.0 で計算
        されていた（2026-09-26）。
        """
        import re

        from stock_ai.backtest import power

        original = power.estimate_power

        def shrunk(values, lags=power.DEFAULT_LAGS):
            estimate = original(values, lags=lags)
            return dataclasses.replace(estimate, omega=estimate.variance * 0.5)

        monkeypatch.setattr(power, "estimate_power", shrunk)

        result = self._run(monkeypatch)

        assert result.exit_code == 0, result.output
        text = re.sub(r"\s+", "", result.output)
        assert "IS の膨張1.00（測った0.71を床に上げた）".replace(" ", "") in text

    def test_the_prediction_uses_the_measured_null_spread(self) -> None:
        """**部品が正しくても、呼ぶ側が 1.0 を渡せば意味が無い。**"""
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli.positive_control)

        assert 'predicted_share(effect, tally["errors"], target, MEASURED_INFLATION)' in body

    def test_an_effect_planted_backwards_is_caught(self, monkeypatch) -> None:
        """**向きを逆に埋めたら、赤くなること。**"""
        from stock_ai.backtest import rehearsal

        original = rehearsal.planted_sections
        monkeypatch.setattr(
            rehearsal,
            "planted_sections",
            lambda sections, strength, seed: original(sections, -strength, seed=seed),
        )

        result = self._run(monkeypatch)

        assert result.exit_code == 0, result.output
        assert "伝達率" in result.output
        assert "どれも当たらなかった" not in result.output

    def test_a_path_that_reshuffles_the_pairs_is_caught(self, monkeypatch) -> None:
        """**0 を埋めた行は、陰性対照と同じ t のはず。** 銘柄とリターンの組を崩す経路なら違う。

        月を落とす経路は、管の側が月数の食い違いで例外にする——ここで拾うのは、
        **例外にならずに黙って値が変わる**ほうである。
        """
        from stock_ai.backtest import rehearsal

        original = rehearsal.planted_sections

        def rotated(sections, strength, seed):
            built = original(sections, strength, seed=seed)
            return [
                [(signal, month[index - 1][1]) for index, (signal, _f) in enumerate(month)]
                for month in built
            ]

        monkeypatch.setattr(rehearsal, "planted_sections", rotated)

        result = self._run(monkeypatch)

        assert result.exit_code == 0, result.output
        assert "陰性対照と同じ t にならない" in result.output
