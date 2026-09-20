"""線の当て方が「以下」になっているか。**割り算の丸めで等号を落とさない。**

`a/b - 1.0 <= -drop` は、`680 → 544`（ちょうど −20%）を拾わなかった
（2026-09-20、ユーザーが実データの 2461 で見つけた）。`544/680` の真の値
0.8 は double で表せず、近いほうに丸めると線のわずか上に来る。

人工の値動きで数えると、**その取りこぼしは急落 99 件のうち 2 件**だった。
"""

from __future__ import annotations

from fractions import Fraction

import numpy as np
import pytest

from stock_ai.backtest.fall import fell_at_least
from stock_ai.backtest.gap_fill import GAP_DOWN, gap_positions
from stock_ai.backtest.knife import KNIFE_DAYS, KNIFE_DROP, knife_positions


def _one(reference: float, later: float, drop: float) -> bool:
    return bool(fell_at_least(np.array([reference]), np.array([later]), drop)[0])


class TestTheLineIsInclusive:
    """**事前登録は「−20% 以下」と書いてある。**"""

    def test_the_real_case_from_2461(self) -> None:
        """`680 → 544` はちょうど −20%。**直す前のコードは落としていた。**"""
        assert _one(680.0, 544.0, KNIFE_DROP)
        # 直す前の式が、この足を落としていたことを示す。
        assert not (-KNIFE_DROP >= 544.0 / 680.0 - 1.0)

    @pytest.mark.parametrize(("before", "after"), [(1000.0, 800.0), (250.0, 200.0), (5.0, 4.0)])
    def test_other_round_prices_on_the_line(self, before: float, after: float) -> None:
        """**丸い値は実データに多い。** どれも線の上に乗る。"""
        assert _one(before, after, KNIFE_DROP)

    def test_the_three_per_cent_line_too(self) -> None:
        """#15 の下窓も同じ式である。`100 → 97` はちょうど −3%。"""
        assert _one(100.0, 97.0, GAP_DOWN)
        # **こちらは直す前の式でも拾えていた。** 同じ書き方でも、**定数に
        # よって「以下」になったり「未満」になったりする**——`-0.20` は線を
        # 外し、`-0.03` は外さない（実測 40,142 対 0）。**だから式を1つに決める。**
        assert -GAP_DOWN >= 97.0 / 100.0 - 1.0, "**この線は元から拾えていた。**"

    def test_a_shallower_fall_is_not_one(self) -> None:
        """**落ちようのない検査にしない。** 足りなければ False。"""
        assert not _one(680.0, 545.0, KNIFE_DROP)
        assert not _one(100.0, 98.0, GAP_DOWN)

    def test_a_deeper_fall_is_one(self) -> None:
        assert _one(680.0, 543.0, KNIFE_DROP)

    def test_a_rise_is_not(self) -> None:
        assert not _one(680.0, 700.0, KNIFE_DROP)


class TestItAgreesWithExactArithmetic:
    """**厳密な有理数と突き合わせる。** float の都合で線がずれていないか。"""

    def test_it_matches_on_tick_prices(self) -> None:
        rng = np.random.default_rng(3)
        line = Fraction(1) - Fraction("0.2")
        wrong = old_wrong = ties = 0
        for _ in range(20_000):
            b = float(rng.integers(50, 20_000))
            a = float(round(b * 0.8)) if rng.random() < 0.5 else float(rng.integers(1, int(b)))
            truth = Fraction(a) / Fraction(b) <= line
            ties += Fraction(a) / Fraction(b) == line
            wrong += _one(b, a, 0.20) != truth
            old_wrong += (a / b - 1.0 <= -0.20) != truth

        assert ties > 0, "**線上の組が1つも入っていない。** 検査になっていない。"
        assert wrong == 0
        assert old_wrong == ties, "**直す前の式は、線上をすべて落としていた。**"


class TestBadInput:
    def test_mismatched_lengths_are_refused(self) -> None:
        with pytest.raises(ValueError, match="長さが違う"):
            fell_at_least(np.array([1.0, 2.0]), np.array([1.0]), 0.2)

    @pytest.mark.parametrize("drop", [0.0, 1.0, -0.1, 1.5])
    def test_a_drop_outside_zero_to_one_is_refused(self, drop: float) -> None:
        with pytest.raises(ValueError, match="drop must be between"):
            fell_at_least(np.array([1.0]), np.array([1.0]), drop)

    def test_a_non_positive_price_is_not_a_fall(self) -> None:
        """**判定できない足は False。** 0 や欠測を「下げた」に数えない。"""
        assert not _one(0.0, 1.0, 0.2)
        assert not _one(100.0, 0.0, 0.2)
        assert not _one(100.0, float("nan"), 0.2)


class TestBothPipesUseTheSameLine:
    """**同じ式を2つ持たない。** `#15` と `#16` が別々に書いていた。"""

    def test_the_formula_lives_in_one_place(self) -> None:
        """**コードだけを見る。** 説明の散文に書いてあるのは構わない。"""
        import io
        import pathlib
        import tokenize

        root = pathlib.Path(__file__).resolve().parent.parent / "src" / "stock_ai" / "backtest"
        offenders = []
        for path in sorted(root.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            code = "".join(
                token.string
                for token in tokenize.generate_tokens(io.StringIO(text).readline)
                if token.type not in {tokenize.COMMENT, tokenize.STRING}
            )
            if "-1.0<=-" in code.replace(" ", ""):
                offenders.append(path.name)

        assert not offenders, {
            "線を自分で書いている": offenders,
            "どうするか": "`fall.fell_at_least` を呼ぶ。",
        }

    def test_the_crash_pipe_goes_through_it(self) -> None:
        closes = np.array([680.0] * 6 + [544.0])
        liquid = np.ones(len(closes), dtype=bool)

        assert knife_positions(closes, liquid, KNIFE_DROP, KNIFE_DAYS).tolist() == [6]

    def test_the_gap_pipe_goes_through_it(self) -> None:
        closes = np.array([100.0, 97.0])
        opens = np.array([100.0, 97.0])
        liquid = np.ones(2, dtype=bool)

        assert gap_positions(opens, closes, liquid, GAP_DOWN).tolist() == [1]
