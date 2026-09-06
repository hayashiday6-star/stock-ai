"""市場の残高・内訳の読み取り。

固定データはすべて配布サンプル `sample_data_v2` **そのまま**である。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from stock_ai.data.jquants_markets import (
    ShortPosition,
    earliest_publication,
    parse_breakdown,
    parse_margin_interest,
    parse_short_positions,
    total_short_position,
)

FIXTURES = Path(__file__).parent / "fixtures"


class TestMarginInterest:
    """信用取引の週末残高。"""

    SAMPLE = FIXTURES / "jquants_margin_interest_sample.csv"

    def test_the_distributed_sample_parses(self) -> None:
        (item,) = parse_margin_interest(self.SAMPLE.read_bytes())

        assert item.symbol == "8697"
        assert item.as_of == dt.date(2022, 1, 7)
        assert item.short_volume == 61800.0
        assert item.long_volume == 229300.0

    def test_the_date_is_when_it_was_true_not_when_it_was_known(self) -> None:
        """**ずれが2営業日ある。** `Date` という名前が「その日のデータ」に
        見えるので、ここでは `as_of` と呼ぶ。
        """
        (item,) = parse_margin_interest(self.SAMPLE.read_bytes())

        assert not hasattr(item, "date")
        assert item.as_of == dt.date(2022, 1, 7)  # 金曜

    def test_the_publication_bound_is_a_lower_bound(self) -> None:
        """**祝日の週は後ろ倒しになる。**

        下限をそのままイベント日に使うと、その週だけ未公表の値動きを使う。
        例外は出ないし、1週ぶんなので件数からも気付けない。
        """
        friday = dt.date(2022, 1, 7)

        assert earliest_publication(friday) == dt.date(2022, 1, 11)  # 火曜
        assert earliest_publication(friday) > friday

    def test_the_issue_type_stays_a_code_and_not_a_number(self) -> None:
        """区分の符号であって量ではない。"""
        (item,) = parse_margin_interest(self.SAMPLE.read_bytes())

        assert item.issue_type == "2"
        assert item.lending

    def test_a_standard_margin_issue_is_not_read_as_lending(self) -> None:
        payload = b"Date,Code,ShrtVol,IssType\n2022-01-07,86970,1.0,1\n"

        (item,) = parse_margin_interest(payload)

        assert not item.lending


class TestBreakdown:
    """売買内訳。"""

    SAMPLE = FIXTURES / "jquants_breakdown_sample.csv"

    def test_the_distributed_sample_parses(self) -> None:
        items = parse_breakdown(self.SAMPLE.read_bytes())

        assert len(items) == 4
        assert items[0].date == dt.date(2022, 1, 4)

    def test_yen_and_shares_are_kept_apart(self) -> None:
        """**単位の違う値を同じ辞書に入れると、合計が通ってしまう。**

        `LongSellVa` は円、`LongSellVo` は株。名前は末尾2文字しか違わない。
        """
        first = parse_breakdown(self.SAMPLE.read_bytes())[0]

        assert first.values_yen["LongSell"] == 693_280_400.0
        assert first.values_shares["LongSell"] == 275_400.0
        assert set(first.values_yen) == set(first.values_shares)

    def test_the_yen_figures_are_far_larger_than_the_share_figures(self) -> None:
        """取り違えたときに気付ける大きさの差があること。"""
        first = parse_breakdown(self.SAMPLE.read_bytes())[0]

        assert first.values_yen["LongSell"] > first.values_shares["LongSell"] * 100


class TestShortPositions:
    """空売り残高報告。"""

    SAMPLE = FIXTURES / "jquants_short_positions_sample.csv"

    def test_the_distributed_sample_is_cp932(self) -> None:
        """報告者名が日本語なので、`utf-8` 決め打ちだと例外で止まる。"""
        raw = self.SAMPLE.read_bytes()

        assert "野村證券株式会社" in raw.decode("cp932")

    def test_the_distributed_sample_parses(self) -> None:
        items = parse_short_positions(self.SAMPLE.read_bytes())

        assert len(items) == 5
        assert items[0].reporter == "野村證券株式会社"
        assert items[0].disclosed_on == dt.date(2025, 1, 8)
        assert items[0].calculated_on == dt.date(2025, 1, 7)

    def test_the_calculation_date_is_before_the_disclosure(self) -> None:
        """**計算基準日をイベント日に置くと、未公表の日を使う。**"""
        for item in parse_short_positions(self.SAMPLE.read_bytes()):
            assert item.calculated_on is not None
            assert item.calculated_on < item.disclosed_on

    def test_one_row_is_one_reporter_and_not_one_symbol(self) -> None:
        """**先頭を取ると、ヘッジファンド1社の残高が銘柄の残高になる。**

        例外は出ないし、値ももっともらしい。
        """
        day = dt.date(2025, 1, 8)
        rows = [
            ShortPosition("8697", day, None, "甲", 0.006, None),
            ShortPosition("8697", day, None, "乙", 0.004, None),
        ]

        assert rows[0].ratio_to_shares_outstanding == 0.006  # 1社ぶん
        assert total_short_position(rows)[("8697", day)] == 0.01  # 合計

    def test_the_same_reporter_twice_counts_once(self) -> None:
        """訂正が2行に見えることがある。**足すと倍になる。**"""
        day = dt.date(2025, 1, 8)
        rows = [
            ShortPosition("8697", day, None, "甲", 0.006, None),
            ShortPosition("8697", day, None, "甲", 0.007, None),
        ]

        assert total_short_position(rows)[("8697", day)] == 0.007  # 後の値

    def test_symbols_and_days_do_not_bleed_together(self) -> None:
        rows = [
            ShortPosition("8697", dt.date(2025, 1, 8), None, "甲", 0.006, None),
            ShortPosition("8697", dt.date(2025, 1, 9), None, "甲", 0.004, None),
            ShortPosition("7203", dt.date(2025, 1, 8), None, "甲", 0.005, None),
        ]

        totals = total_short_position(rows)

        assert len(totals) == 3

    def test_a_row_without_a_ratio_is_left_out_of_the_total(self) -> None:
        """**`0` として足さない。** 報告が無いことと、残高ゼロは違う。"""
        day = dt.date(2025, 1, 8)
        rows = [
            ShortPosition("8697", day, None, "甲", 0.006, None),
            ShortPosition("8697", day, None, "乙", None, None),
        ]

        assert total_short_position(rows)[("8697", day)] == 0.006
