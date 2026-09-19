"""価格の系列が不連続になっている場所（`stock_ai.backtest.discontinuity`）。

**同じ4行が5箇所に書かれていた。** 片方だけ直しても落ちないし、**規則を
落としても落ちずに数字だけ変わる**——#6 は不連続を外すだけで SD が
24.42% → 3.64% になった。

ここで押さえるのは3つ。

1. **売買停止を挟んだ併合を素通りさせない。** 暦の `NaN` と比べるとそうなる
2. **6箇所目を書かせない。** 同じ4行が `src/` に戻ったら落ちる
3. **落ちる条件を実際に1つ作る。** 8308 の 1:1000 併合を盤面にする
"""

from __future__ import annotations

import pathlib

import numpy as np
import pandas as pd
import pytest

from stock_ai.backtest.discontinuity import (
    MAX_SESSION_MOVE,
    crossings,
    session_breaks,
    spans_break,
)


class TestWhatCountsAsABreak:
    def test_a_quiet_series_has_none(self) -> None:
        closes = pd.Series([100.0, 101.0, 99.0, 103.0])

        assert not session_breaks(closes).any()

    def test_the_8308_merger_is_caught(self) -> None:
        """**実測の形**（#6、2005年）。1:1000 の併合を系列がまたいでいない。"""
        closes = pd.Series([2.0, 2.0, 2_040.0, 2_050.0])

        found = session_breaks(closes)

        assert found.tolist() == [False, False, True, False]

    def test_a_halt_does_not_hide_it(self) -> None:
        """**直前に値のあった日と比べる。** `NaN` と比べると素通りする。"""
        closes = pd.Series([2.0, float("nan"), float("nan"), 2_040.0])

        found = session_breaks(closes)

        assert found[3]

    def test_the_threshold_is_the_one_from_hypothesis_six(self) -> None:
        """**この検査が落ちる条件を、実際に1つ作る。**"""
        under = pd.Series([100.0, 100.0 * (1.0 + MAX_SESSION_MOVE * 0.9)])
        over = pd.Series([100.0, 100.0 * (1.0 + MAX_SESSION_MOVE * 1.1)])

        assert not session_breaks(under).any()
        assert session_breaks(over).any()

    def test_a_downward_break_counts_too(self) -> None:
        """**併合だけでなく分割も。** 1:2 の分割は −50% に見える。"""
        closes = pd.Series([1_000.0, 400.0])

        assert session_breaks(closes)[1]

    def test_the_first_bar_is_never_a_break(self) -> None:
        assert not session_breaks(pd.Series([100.0]))[0]

    def test_an_empty_series_is_not_an_exception(self) -> None:
        assert len(session_breaks(pd.Series(dtype=float))) == 0


class TestAskingWhetherAWindowSpansOne:
    @staticmethod
    def _prefix(flags: list[bool]) -> np.ndarray:
        return crossings(np.array(flags, dtype=bool))

    def test_a_window_before_the_break_is_clean(self) -> None:
        prefix = self._prefix([False, False, True, False])

        assert not spans_break(prefix, 0, 1)

    def test_a_window_containing_it_is_not(self) -> None:
        prefix = self._prefix([False, False, True, False])

        assert spans_break(prefix, 1, 3)

    def test_the_ends_are_included(self) -> None:
        """**両端を含む。** 片方だけ外すと、境目の窓が通る。"""
        prefix = self._prefix([False, False, True, False])

        assert spans_break(prefix, 2, 2)

    def test_a_window_after_it_is_clean(self) -> None:
        prefix = self._prefix([False, False, True, False])

        assert not spans_break(prefix, 3, 3)


class TestNobodyWritesTheSixthCopy:
    """**同じ4行が5箇所に書かれていた。** 戻ってきたら、ここが落ちる。"""

    def test_the_inline_rule_is_gone_from_src(self) -> None:
        root = pathlib.Path(__file__).resolve().parent.parent / "src"
        offenders = [
            path
            for path in root.rglob("*.py")
            if path.name != "discontinuity.py"
            and "np.abs(step - 1.0) > MAX_SESSION_MOVE" in path.read_text(encoding="utf-8")
        ]

        assert not offenders, [str(path) for path in offenders]

    def test_the_constant_has_one_home(self) -> None:
        """**`reversal` からも引けるが、値はここから来ている。**"""
        from stock_ai.backtest import reversal

        assert reversal.MAX_SESSION_MOVE is MAX_SESSION_MOVE

    def test_the_four_callers_use_the_helper(self) -> None:
        root = pathlib.Path(__file__).resolve().parent.parent / "src" / "stock_ai" / "backtest"
        for name in ("reversal", "lowvol", "factor_panel", "lowvol_census", "wall"):
            body = (root / f"{name}.py").read_text(encoding="utf-8")

            assert "session_breaks(" in body, name


def test_the_module_says_what_it_cost_to_learn_this() -> None:
    """**出典の無い数字を書かない。** 24.42% → 3.64% は #6 の実測である。"""
    import inspect

    from stock_ai.backtest import discontinuity

    source = inspect.getsource(discontinuity)

    assert "24.42%" in source
    assert "8308" in source


@pytest.mark.parametrize("bars", [0, 1])
def test_a_series_too_short_to_compare_is_not_an_exception(bars: int) -> None:
    assert not session_breaks(pd.Series([100.0] * bars, dtype=float)).any()
