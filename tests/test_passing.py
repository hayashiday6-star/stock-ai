"""合格に要るリターンと、合格の条件（`stock_ai.backtest.passing`）。

**数字を書き写さない。** 線が変われば要るリターンも全部変わる——実際
2026-09-17 に 3.02 → 3.39 に動いた。**書き写した数字は、古いまま
もっともらしく見え続ける。**

ここで固定するのは、**書き写しが戻ってこないこと**である。
"""

from __future__ import annotations

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
            annual = shape.required_annual(target)
            need = f"年 {annual:.1%}" if annual else f"1{shape.unit} {shape.required(target):.2%}"
            assert need in body, f"{shape.name}: {need} が文書に無い"

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
