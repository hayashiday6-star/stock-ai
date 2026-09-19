"""合格に要るリターンと、合格の条件（`stock_ai.backtest.passing`）。

**数字を書き写さない。** 線が変われば要るリターンも全部変わる——実際
2026-09-17 に 3.02 → 3.39 に動いた。**書き写した数字は、古いまま
もっともらしく見え続ける。**

ここで固定するのは、**書き写しが戻ってこないこと**である。
"""

from __future__ import annotations

import dataclasses
import pathlib
import re

import pytest

from stock_ai.backtest.multiplicity import HYPOTHESIS_BUDGET, calibrated_t, required_t
from stock_ai.backtest.passing import CONDITIONS, SHAPES

_DOC = pathlib.Path(__file__).resolve().parent.parent / "docs" / "PASSING.md"


class TestTheNumbersAreComputedNotTranscribed:
    def test_raising_the_line_raises_every_requirement(self) -> None:
        """**線が動けば、要るリターンも動く。** 書き写していれば動かない。"""
        for shape in SHAPES:
            assert shape.required(3.39) > shape.required(3.02)

    def test_the_requirement_follows_the_line_in_proportion(self) -> None:
        shape = SHAPES[0]

        assert shape.required(6.04) == pytest.approx(2 * shape.required(3.02))

    def test_more_periods_lower_the_requirement(self) -> None:
        """**期数が増えれば、見分けられる差は小さくなる。**"""
        import dataclasses

        shape = SHAPES[0]
        longer = dataclasses.replace(shape, periods=shape.periods * 4)

        assert longer.required(3.39) == pytest.approx(shape.required(3.39) / 2)

    def test_the_gentlest_design_is_first(self) -> None:
        """**いちばん甘い形でどれだけ要るか**が先頭に来ること。"""
        monthly = [shape for shape in SHAPES if shape.per_year]
        needs = [shape.required_annual(3.39) for shape in monthly]

        assert needs == sorted(needs)


class TestWhatCannotBeAnnualised:
    def test_an_event_design_returns_none(self) -> None:
        """**資金をどれだけ張るかを決めないと年率に直せない。**

        決めずに掛けると、**根拠の無い年率が文書に載る。**
        """
        events = [shape for shape in SHAPES if not shape.per_year]

        assert events, "イベント型が登録から消えている"
        for shape in events:
            assert shape.required_annual(3.39) is None

    def test_a_monthly_design_does_annualise(self) -> None:
        monthly = SHAPES[0]

        assert monthly.required_annual(3.39) == pytest.approx(monthly.required(3.39) * 12)


class TestEveryFigureSaysWhereItCameFrom:
    def test_each_shape_cites_a_prereg(self) -> None:
        """**出典の無い数字を書かない。**"""
        for shape in SHAPES:
            assert "PREREG" in shape.source, shape.name

    def test_the_cited_prereg_exists(self) -> None:
        """**書いてある先が在ること。** 綴りを間違えても例外は出ない。"""
        docs = pathlib.Path(__file__).resolve().parent.parent / "docs"
        for shape in SHAPES:
            name = re.search(r"(PREREG_\w+\.md)", shape.source)
            assert name, shape.source
            assert (docs / name.group(1)).is_file(), shape.source


class TestTheDocumentIsGenerated:
    def test_it_says_it_is_generated(self) -> None:
        """**手で直されないように、文書自身がそう言うこと。**"""
        assert _DOC.is_file(), "docs/PASSING.md が無い"
        assert "生成物である" in _DOC.read_text(encoding="utf-8")

    def test_it_matches_the_current_line(self) -> None:
        """**文書が古くなっていないこと。**

        線を動かしたのに書き直し忘れると、**古い数字がそこに残る。** それを
        止めるのがこのテストである。
        """
        body = _DOC.read_text(encoding="utf-8")
        target = calibrated_t(HYPOTHESIS_BUDGET)

        assert f"`t ≥ {target:.2f}`" in body, "docs/PASSING.md を書き直すこと"
        for shape in SHAPES:
            # **形ごとの線で見る。** ここも1つの線を全部に当てていた
            # （2026-09-18）——**直す側だけでなく、確かめる側も同じ間違いを
            # していた。**
            own = shape.line()
            annual = shape.required_annual(own)
            need = f"年 {annual:.1%}" if annual else f"1{shape.unit} {shape.required(own):.2%}"
            assert need in body, f"{shape.name}: {need} が文書に無い"
            assert f"`t ≥ {own:.2f}`" in body, f"{shape.name}: 線が文書に無い"

    def test_it_carries_all_five_conditions(self) -> None:
        body = _DOC.read_text(encoding="utf-8")

        assert len(CONDITIONS) == 5
        for heading, _text in CONDITIONS:
            assert heading in body

    def test_the_line_in_the_document_is_the_calibrated_one(self) -> None:
        """**素の線 3.02 を判定の線として載せない。**"""
        body = _DOC.read_text(encoding="utf-8")

        assert f"{required_t(HYPOTHESIS_BUDGET):.2f}" in body, "素の線の出どころが書いていない"
        assert f"**`t ≥ {calibrated_t(HYPOTHESIS_BUDGET):.2f}`**" in body


class TestTheLineIsPerPipeHereToo:
    """**1つの線を全部に当てていた**（2026-09-18 まで）。

    線を管ごとにすると決めた日に、ここだけ直し忘れていた。#5 はイベント型
    なのに、月次の盤面で測った膨張が乗った線で「要るリターン」を出していた。

    **決めたことを、決めた場所の全部に当てる。**
    """

    def test_a_monthly_shape_takes_the_monthly_line(self) -> None:
        from stock_ai.backtest.multiplicity import (
            HYPOTHESIS_BUDGET,
            MEASURED_INFLATION,
            calibrated_t,
        )
        from stock_ai.backtest.passing import SHAPES

        monthly = next(shape for shape in SHAPES if shape.pipe == "monthly")

        assert monthly.line() == pytest.approx(
            calibrated_t(HYPOTHESIS_BUDGET, inflation=MEASURED_INFLATION)
        )

    def test_an_event_shape_takes_the_event_line(self) -> None:
        from stock_ai.backtest.multiplicity import (
            HYPOTHESIS_BUDGET,
            MEASURED_INFLATION_EVENT,
            calibrated_t,
        )
        from stock_ai.backtest.passing import SHAPES

        event = next(shape for shape in SHAPES if shape.pipe == "event")

        assert event.line() == pytest.approx(
            calibrated_t(HYPOTHESIS_BUDGET, inflation=MEASURED_INFLATION_EVENT)
        )

    def test_the_two_lines_are_not_the_same(self) -> None:
        """**違う数字であること。** 同じなら、この分けは何も守っていない。"""
        from stock_ai.backtest.passing import SHAPES

        monthly = next(shape for shape in SHAPES if shape.pipe == "monthly")
        event = next(shape for shape in SHAPES if shape.pipe == "event")

        assert monthly.line() != event.line()

    def test_every_shape_names_a_pipe_that_exists(self) -> None:
        from stock_ai.backtest.passing import SHAPES

        for shape in SHAPES:
            assert shape.line() > 0, shape.name

    def test_an_unknown_pipe_is_refused(self) -> None:
        """**この検査が落ちる条件を、実際に1つ作る。**"""
        from stock_ai.backtest.passing import SHAPES

        strange = dataclasses.replace(SHAPES[0], pipe="weekly")

        with pytest.raises(ValueError, match="知らない管"):
            strange.line()


class TestEveryGateUsesTheSameLine:
    """**決めたことを、決めた場所の全部に当てる。**

    線を管ごとにすると決めたのに、`power-gate` だけ校正前の 3.02 を使って
    いた（2026-09-19 に #12 で気付いた）。同じ設計に2つの線が出て、**検出
    できる差が 22.6% と 25.3% に割れた。**

    **緩める向きの取り違えである。** 線が低ければ検出できる差も小さく出る
    ので、**通ってはいけない設計が §0 を通る。**
    """

    def test_the_gate_asks_multiplicity_for_the_line(self) -> None:
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli.power_gate)

        assert "line_for(pipe, budget)" in body
        # **素の値をそのまま線にしていないこと。**
        assert "target_t = adjusted.required_t" not in body

    def test_the_gate_takes_a_pipe(self) -> None:
        from tests.test_cli import declared_options

        assert "--pipe" in declared_options("power-gate")

    def test_the_line_choice_lives_in_one_place(self) -> None:
        """**2箇所目を書いたら、そのうち片方だけ直す。**"""
        import inspect

        from stock_ai.backtest import passing

        body = inspect.getsource(passing.Shape.line)

        assert "line_for" in body
        assert "MEASURED_INFLATION" not in body

    def test_every_pipe_the_shapes_name_is_one_the_line_knows(self) -> None:
        from stock_ai.backtest.multiplicity import PIPES
        from stock_ai.backtest.passing import SHAPES

        for shape in SHAPES:
            assert shape.pipe in PIPES, shape.name

    def test_an_unknown_pipe_is_refused(self) -> None:
        """**この検査が落ちる条件を、実際に1つ作る。**"""
        from stock_ai.backtest.multiplicity import line_for

        with pytest.raises(ValueError, match="知らない管"):
            line_for("weekly")

    def test_the_monthly_line_is_stricter_than_the_uncorrected_one(self) -> None:
        """**校正は足すためのものである。** 素の値を下回らせない。"""
        from stock_ai.backtest.multiplicity import (
            HYPOTHESIS_BUDGET,
            PIPES,
            line_for,
            required_t,
        )

        plain = required_t(HYPOTHESIS_BUDGET)
        for pipe in PIPES:
            assert line_for(pipe) >= plain, pipe


class TestTheCalendarPipeHasItsOwnLine:
    """**3本目の管。** 月次でもイベント型でもない。

    #13 は1本の系列の中で日どうしを比べる別の推定量なので、月次の 1.12 も
    イベント型の 1.09 も当てはまらない。**測った**（2026-09-19、400回で
    SD 1.05）。

    **1回目は使わなかった。** `t` の平均が +0.40 出ていて、原因は対照の
    作りだった。
    """

    def test_the_pipe_is_known(self) -> None:
        from stock_ai.backtest.multiplicity import PIPES

        assert "calendar" in PIPES

    def test_the_line_is_stricter_than_the_plain_one(self) -> None:
        """**校正は足すためのものである。**"""
        from stock_ai.backtest.multiplicity import HYPOTHESIS_BUDGET, line_for, required_t

        assert line_for("calendar") > required_t(HYPOTHESIS_BUDGET)

    def test_it_differs_from_the_other_pipes(self) -> None:
        """**同じなら、この分けは何も守っていない。**"""
        from stock_ai.backtest.multiplicity import line_for

        lines = {line_for(pipe) for pipe in ("monthly", "event", "calendar")}

        assert len(lines) == 3

    def test_the_measured_value_says_where_it_came_from(self) -> None:
        """**出典の無い数字を書かない。**"""
        import inspect

        from stock_ai.backtest import multiplicity

        source = inspect.getsource(multiplicity)

        assert "暦の対照" in source
        assert "211 月替わり" in source

    def test_the_command_uses_it(self) -> None:
        """**口を開けただけで配線を忘れる**形を止める。"""
        import inspect

        from stock_ai import cli

        body = inspect.getsource(cli.turn_of_month_power)

        assert 'line_for("calendar")' in body
        assert "暫定" not in body, "対照を回したのに、まだ暫定と書いてある"

    def test_the_gate_command_accepts_it(self) -> None:
        from stock_ai.backtest.multiplicity import line_for

        assert line_for("calendar") > 0
