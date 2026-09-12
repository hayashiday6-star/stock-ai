"""立花と J-Quants を、重なる5年ぶんで日次に突き合わせる。

**継ぎ目の検査は1日しか見ていない。** 2021-09-01 が普通の1日に見えたことは、
その日に段差が無いことしか言っていない。5年ぶんの毎日が合っているかは別の
話である。

**いましかできない。** 2026-09-22 に解約すると、片方が更新されなくなる。
原本は残るが、2つの生きた経路が同じことを言うかを確かめる機会は無くなる。
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from stock_ai.data.jquants_crosscheck import compare_daily, summarise
from stock_ai.data.schema import ADJ_CLOSE, CLOSE


def _frame(values: dict[str, float], adjusted: dict[str, float] | None = None) -> pd.DataFrame:
    index = pd.DatetimeIndex([pd.Timestamp(day) for day in values], name="date")
    return pd.DataFrame(
        {CLOSE: list(values.values()), ADJ_CLOSE: list((adjusted or values).values())},
        index=index,
    )


class TestOnlyOverlappingDays:
    """**片方にしかない日を食い違いに数えない。**

    立花は 2001年から、原本は 2021-09 から。素直に引き算すると、20年ぶんが
    全部「食い違い」になる。
    """

    def test_days_present_in_both_are_what_gets_compared(self) -> None:
        first = _frame({"2021-08-31": 100.0, "2021-09-01": 101.0})
        second = _frame({"2021-09-01": 101.0, "2021-09-02": 102.0})

        match = compare_daily(first, second, "7203")

        assert match.days == 1
        assert match.close_differs == 0

    def test_the_non_overlapping_days_are_counted_separately(self) -> None:
        first = _frame({"2021-08-31": 100.0, "2021-09-01": 101.0})
        second = _frame({"2021-09-01": 101.0, "2021-09-02": 102.0})

        match = compare_daily(first, second, "7203")

        assert match.only_first == 1
        assert match.only_second == 1

    def test_no_overlap_says_so_rather_than_claiming_agreement(self) -> None:
        """**重なりが無いことを「一致」と読ませない。**"""
        first = _frame({"2021-08-31": 100.0})
        second = _frame({"2021-09-02": 102.0})

        match = compare_daily(first, second, "7203")

        assert match.days == 0
        assert not match.agrees

    def test_an_empty_side_is_not_an_agreement(self) -> None:
        match = compare_daily(pd.DataFrame(), _frame({"2021-09-01": 100.0}), "7203")

        assert not match.agrees
        assert match.only_second == 1


class TestRelativeNotAbsolute:
    """**相対で比べる。**

    5,000円の銘柄と50円の銘柄に同じ絶対値の許容幅を当てると、片方は素通りし、
    もう片方はほぼ全部が食い違いになる。
    """

    def test_one_yen_on_a_cheap_stock_counts(self) -> None:
        first = _frame({"2024-01-04": 51.0})
        second = _frame({"2024-01-04": 50.0})

        assert compare_daily(first, second, "7203").close_differs == 1

    def test_one_yen_on_an_expensive_stock_does_not(self) -> None:
        first = _frame({"2024-01-04": 5001.0})
        second = _frame({"2024-01-04": 5000.0})

        assert compare_daily(first, second, "7203").close_differs == 0

    def test_a_zero_price_does_not_make_everything_differ(self) -> None:
        """**0 で割らない。** 割ると、値の無い日が「無限に違う」になる。"""
        first = _frame({"2024-01-04": 100.0, "2024-01-05": 101.0})
        second = _frame({"2024-01-04": 0.0, "2024-01-05": 101.0})

        match = compare_daily(first, second, "7203")

        assert match.close_differs == 0
        assert match.days == 2


class TestWorstDay:
    """**件数だけでなく、いちばん悪い日を出す。**

    1円のまるめが100日あることと、1日だけ倍半分になることは、件数では同じに
    見える。
    """

    def test_the_largest_difference_and_its_day_are_reported(self) -> None:
        first = _frame({"2024-01-04": 100.0, "2024-01-05": 200.0})
        second = _frame({"2024-01-04": 100.0, "2024-01-05": 100.0})

        match = compare_daily(first, second, "7203")

        assert match.worst == 1.0
        assert match.worst_on == dt.date(2024, 1, 5)

    def test_a_clean_symbol_has_no_worst_day_worth_reading(self) -> None:
        first = _frame({"2024-01-04": 100.0})
        second = _frame({"2024-01-04": 100.0})

        match = compare_daily(first, second, "7203")

        assert match.worst == 0.0
        assert match.agrees


class TestRawAndAdjustedSeparately:
    """**生の終値から比べる。** ここが合わないなら、調整の話をしても意味がない。"""

    def test_the_raw_close_can_agree_while_the_adjustment_differs(self) -> None:
        """分割調整の基準だけが違う形。**生値では気付けない。**"""
        first = _frame({"2024-01-04": 200.0}, adjusted={"2024-01-04": 100.0})
        second = _frame({"2024-01-04": 200.0}, adjusted={"2024-01-04": 200.0})

        match = compare_daily(first, second, "7203")

        assert match.close_differs == 0
        assert match.adjusted_differs == 1
        assert match.agrees  # 生の終値は合っている

    def test_a_missing_adjusted_column_is_skipped_not_counted(self) -> None:
        first = pd.DataFrame(
            {CLOSE: [100.0]},
            index=pd.DatetimeIndex([pd.Timestamp("2024-01-04")], name="date"),
        )
        second = _frame({"2024-01-04": 100.0})

        match = compare_daily(first, second, "7203")

        assert match.adjusted_differs == 0
        assert match.days == 1


class TestSummary:
    """貼られる1行。"""

    def test_it_names_the_worst_symbol_and_day(self) -> None:
        first = _frame({"2024-01-04": 200.0})
        second = _frame({"2024-01-04": 100.0})

        text = summarise([compare_daily(first, second, "7203")])

        assert "7203" in text
        assert "100.00%" in text

    def test_no_overlap_is_said_out_loud(self) -> None:
        first = _frame({"2021-08-31": 100.0})
        second = _frame({"2021-09-02": 100.0})

        assert "重なる日が1日も無い" in summarise([compare_daily(first, second, "7203")])

    def test_nothing_compared_says_so(self) -> None:
        assert "突き合わせた銘柄が無い" in summarise([])
