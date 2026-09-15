"""重なる期間だけを見る。**同じ形を3度踏んだので、関数にした。**

3度とも、気付いたのは数字を見たあとである。書いている最中に気付いた回は一度も
無い。だから注意書きではなく、呼べるものにしてある。

ここで押さえるのは、**「比べていない」と「無い」を分けること**。範囲の外に
落ちたものを捨てると、比べていないことが「食い違いが無い」に見える。
"""

from __future__ import annotations

import datetime as dt

from stock_ai.data.overlap import overlapping


def _days(*text: str) -> list[dt.date]:
    return [dt.date.fromisoformat(day) for day in text]


class TestTheRangeIsTheInsideEnds:
    """**片方だけの端を使わない。** それが3度踏んだ形そのものである。"""

    def test_the_start_is_the_later_of_the_two_starts(self) -> None:
        found = overlapping(_days("2008-02-12", "2021-09-01"), _days("2021-09-01"))

        assert found.first == dt.date(2021, 9, 1)

    def test_the_end_is_the_earlier_of_the_two_ends(self) -> None:
        found = overlapping(_days("2021-09-01", "2026-09-15"), _days("2021-09-01"))

        assert found.last == dt.date(2021, 9, 1)

    def test_what_starts_before_the_other_is_not_a_gap(self) -> None:
        """2026-09-15 の誤報そのもの。**欠けではなく、こちらが始まっていない。**"""
        found = overlapping(_days("2008-02-12", "2008-03-13", "2021-09-01"), _days("2021-09-01"))

        assert found.left_only == set()
        assert found.before == set(_days("2008-02-12", "2008-03-13"))


class TestSkippedIsNotZero:
    """**捨てると、比べていないことが「無い」に見える。**"""

    def test_the_outside_is_counted_not_dropped(self) -> None:
        found = overlapping(_days("2008-01-01", "2021-09-01"), _days("2021-09-01"))

        assert found.skipped == 1
        assert found.compared == 1

    def test_the_summary_always_names_the_range_it_compared(self) -> None:
        found = overlapping(_days("2008-01-01", "2021-09-01"), _days("2021-09-01"))

        assert "2021-09-01" in found.summary()
        assert "比べていない" in found.summary()

    def test_no_overlap_says_so_rather_than_reporting_agreement(self) -> None:
        """**重なりが無いのを「全部一致」と読ませない。**"""
        found = overlapping(_days("2008-01-01"), _days("2026-09-15"))

        assert found.first is None
        assert found.common == set()
        assert found.skipped == 2
        assert "比べていない" in found.summary()


class TestRealDisagreementsSurvive:
    def test_a_date_inside_the_range_that_is_missing_is_reported(self) -> None:
        found = overlapping(
            _days("2021-09-01", "2021-09-02", "2021-09-03"),
            _days("2021-09-01", "2021-09-03"),
        )

        assert found.left_only == set(_days("2021-09-02"))
        assert found.right_only == set()

    def test_both_sides_are_kept_apart(self) -> None:
        found = overlapping(
            _days("2021-09-01", "2021-09-02", "2021-09-04"),
            _days("2021-09-01", "2021-09-03", "2021-09-04"),
        )

        assert found.left_only == set(_days("2021-09-02"))
        assert found.right_only == set(_days("2021-09-03"))

    def test_a_date_past_the_other_sides_last_day_is_not_a_disagreement(self) -> None:
        """**これを書いたとき、こちらの予想のほうが間違っていた。**

        左が 09-02 で終わっているのに、右の 09-03 を「右だけにある」と数えたく
        なる。だが左のデータはそこで終わっているので、**本来あるべきだったのか
        どうかを言えない。** 言えないものを食い違いに数えるのが、3度踏んだ形で
        ある。
        """
        found = overlapping(_days("2021-09-01", "2021-09-02"), _days("2021-09-01", "2021-09-03"))

        assert found.right_only == set()
        assert found.after == set(_days("2021-09-03"))


class TestEmptyInput:
    def test_an_empty_side_gives_no_range_and_no_agreement(self) -> None:
        found = overlapping([], _days("2021-09-01"))

        assert found.first is None
        assert found.common == set()
        # **空を「全部一致」にしない。** 比べていないだけである。
        assert found.skipped == 1

    def test_two_empty_sides_do_not_raise(self) -> None:
        found = overlapping([], [])

        assert found.first is None
        assert found.compared == 0
