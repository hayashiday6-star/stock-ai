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


class TestCalendar:
    """取引カレンダー。

    固定データは配布サンプルの `Trading Calendar.csv`（2022年、365日）。
    """

    SAMPLE = FIXTURES / "jquants_calendar_sample.csv"

    def test_the_distributed_sample_parses(self) -> None:
        from stock_ai.data.jquants_markets import parse_calendar, trading_days

        days = parse_calendar(self.SAMPLE.read_bytes())

        assert len(days) == 365
        assert len(trading_days(days)) == 244

    def test_the_2022_sample_has_no_half_day(self) -> None:
        """**だから見落とす。**

        20年ぶんに広げると、半日立会のあった年が入ってくる。近い年のデータだけ
        で「`1` かどうか」の判定を作ると、古い年に入った時点で静かに欠ける。
        """
        from stock_ai.data.jquants_markets import parse_calendar

        assert not any(day.half_day for day in parse_calendar(self.SAMPLE.read_bytes()))

    def test_a_half_day_still_counts_as_a_trading_day(self) -> None:
        """**`1` だけで判定すると、半日立会が非営業日に落ちる。**

        落とすと、その日を挟んだ「N営業日後」が1日ずれる。年に数日なので、
        ずれたことに件数からは気付けない。
        """
        from stock_ai.data.jquants_markets import parse_calendar, trading_days

        days = parse_calendar(b"Date,HolDiv\n2009-12-30,2\n2010-01-01,0\n")

        assert days[0].trading
        assert days[0].half_day
        assert trading_days(days) == [dt.date(2009, 12, 30)]

    def test_a_holiday_with_trading_is_not_a_trading_day(self) -> None:
        """`3`（非営業日・祝日取引あり）はデリバティブの話で、立会ではない。"""
        from stock_ai.data.jquants_markets import parse_calendar

        (day,) = parse_calendar(b"Date,HolDiv\n2022-01-03,3\n")

        assert not day.trading

    def test_the_division_stays_a_code(self) -> None:
        from stock_ai.data.jquants_markets import HOLIDAY_DIVISION, parse_calendar

        for day in parse_calendar(self.SAMPLE.read_bytes()):
            assert day.division in HOLIDAY_DIVISION


class TestCountingWhatTheCalendarActuallyHas:
    """**書いてあることと、手元のデータにそれが在ることは別である。**

    コードには「`1` だけで絞ると半日立会が落ちる」と書いてある。備えとしては
    正しいが、**その区分が1日も無ければ効いていない。** 数えて確かめる。
    """

    def _days(self):
        from stock_ai.data.jquants_markets import parse_calendar

        return parse_calendar(
            b"Date,HolDiv\n2009-12-29,1\n2009-12-30,2\n2009-12-31,0\n2010-01-04,1\n2010-01-11,3\n"
        )

    def test_each_division_is_counted_separately(self) -> None:
        """**「営業日 N 日」だけにしない。** どちらで数えたか後から分からない。"""
        from stock_ai.data.jquants_markets import census

        report = census(self._days())

        assert report.by_division == {"1": 2, "2": 1, "0": 1, "3": 1}

    def test_the_span_comes_from_the_data(self) -> None:
        import datetime as dt

        from stock_ai.data.jquants_markets import census

        report = census(self._days())

        assert (report.first, report.last) == (dt.date(2009, 12, 29), dt.date(2010, 1, 11))

    def test_a_half_day_is_a_trading_day_in_the_count(self) -> None:
        from stock_ai.data.jquants_markets import census

        report = census(self._days())

        assert report.trading == 3  # `1` が2日 + `2` が1日

    def test_the_price_of_the_guard_is_the_number_of_half_days(self) -> None:
        """**`1` だけで絞ったら何日落ちるか。** それが備えの値段である。"""
        from stock_ai.data.jquants_markets import census

        assert census(self._days()).lost_if_only_one == 1

    def test_an_unlisted_division_is_flagged(self) -> None:
        """区分が増えたら気付けること。**黙って非営業日に落とさない。**"""
        from stock_ai.data.jquants_markets import census, parse_calendar

        report = census(parse_calendar(b"Date,HolDiv\n2026-01-05,9\n"))

        assert report.unknown == {"9": 1}

    def test_half_days_are_grouped_by_year(self) -> None:
        import datetime as dt

        from stock_ai.data.jquants_markets import half_days_by_year

        assert half_days_by_year(self._days()) == {2009: [dt.date(2009, 12, 30)]}

    def test_an_empty_calendar_says_so_without_raising(self) -> None:
        from stock_ai.data.jquants_markets import census

        report = census([])

        assert report.first is None
        assert "原本が無い" in report.summary()


class TestTheCalendarAgainstTheRosters:
    """カレンダーは「立会がある」と言っているだけである。

    **本当にその日のデータが在るかは、別のファイルが知っている。** 名簿は
    `/equities/master` から出ていて、カレンダーとは別の原本である。
    """

    def _days(self):
        from stock_ai.data.jquants_markets import parse_calendar

        return parse_calendar(
            b"Date,HolDiv\n"
            b"2009-12-30,2\n"  # 名簿よりずっと前
            b"2026-09-01,1\n"
            b"2026-09-02,1\n"
            b"2026-09-03,0\n"
        )

    def test_days_before_the_rosters_begin_are_not_counted_as_missing(self) -> None:
        """**全期間で取ると数千日出る。** それは欠けではなく、名簿が遡っていない。"""
        import datetime as dt

        from stock_ai.data.jquants_markets import agreement

        report = agreement(self._days(), {dt.date(2026, 9, 1), dt.date(2026, 9, 2)})

        assert report.trading_without_roster == []
        assert report.agree == 2
        assert report.days == 2

    def test_a_trading_day_without_a_roster_inside_the_window_shows_up(self) -> None:
        import datetime as dt

        from stock_ai.data.jquants_markets import agreement

        report = agreement(self._days(), {dt.date(2026, 9, 1), dt.date(2026, 9, 5)})

        assert report.trading_without_roster == [dt.date(2026, 9, 2)]

    def test_a_roster_on_a_non_trading_day_shows_up(self) -> None:
        """**カレンダーのほうが疑わしい向きである。** 名簿はその日の実物である。"""
        import datetime as dt

        from stock_ai.data.jquants_markets import agreement

        report = agreement(
            self._days(),
            {dt.date(2026, 9, 1), dt.date(2026, 9, 2), dt.date(2026, 9, 3)},
        )

        assert report.roster_without_trading == [dt.date(2026, 9, 3)]

    def test_half_days_outside_the_window_are_not_counted(self) -> None:
        import datetime as dt

        from stock_ai.data.jquants_markets import agreement

        report = agreement(self._days(), {dt.date(2026, 9, 1), dt.date(2026, 9, 2)})

        assert report.half_days == 0  # 2009-12-30 は名簿の期間の外

    def test_nothing_to_compare_says_so(self) -> None:
        from stock_ai.data.jquants_markets import agreement

        assert "突き合わせられない" in agreement(self._days(), set()).summary()
