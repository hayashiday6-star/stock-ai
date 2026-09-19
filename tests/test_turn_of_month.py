"""#13「月替わり効果」— 月末から月初の数日と、それ以外の日の差。

ここで押さえるのは6つ。

1. **式が事前登録どおりであること。** 窓の中の合計 − 日数を揃えた窓の外の平均
2. **窓は4営業日で、祝日では減らない。** 事前登録に「祝日で欠ける」と書いたが
   **間違いだった**——営業日で数えているので、祝日は暦の幅を伸ばすだけである
3. **窓と窓の外に、重なりも隙間も無い。** どの営業日もちょうど1つに属する
4. **先読みを入れない。** 窓の内外は暦だけで決まる
5. **IS と OOS が1日も重ならない。** 窓の終わりまで見る
6. **効くものが在れば拾い、無ければ拾わない。** 落ちない検査を「合格」と読まない
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from stock_ai.backtest.rehearsal import placebo_windows
from stock_ai.backtest.turn_of_month import (
    USABLE_FROM,
    WINDOW_AFTER,
    WINDOW_BEFORE,
    WINDOW_DAYS,
    TurnOfMonthSeries,
    build_series,
    daily_returns,
    series_from_prices,
)
from stock_ai.data.schema import ADJ_CLOSE, CLOSE, HIGH, LOW, OPEN, VOLUME

_INDEX = pd.bdate_range("2009-01-01", periods=520, name="date")
_DATES = [stamp.date() for stamp in _INDEX]


def _month_ends(index: pd.DatetimeIndex = _INDEX) -> list[int]:
    """月の最終営業日の位置。**日次リターンの並びに合わせて1つ詰める。**"""
    from stock_ai.backtest.lowvol_census import formation_dates

    return [position - 1 for position in formation_dates(index) if position >= 1]


class TestTheFormulaIsWhatThePreregSays:
    """``窓の中の合計 − 窓の中の日数 × 窓の外の平均``。"""

    def test_a_flat_series_gives_zero(self) -> None:
        returns = [0.0] * (len(_DATES) - 1)
        found = build_series(returns, _DATES[1:], _month_ends())

        assert found.episodes
        assert all(value == pytest.approx(0.0) for value in found.episodes)

    def test_a_lift_inside_the_window_shows_up(self) -> None:
        """**在るものを拾えること。** 落ちない検査は何も守らない。"""
        ends = _month_ends()
        returns = [0.0] * (len(_DATES) - 1)
        for position in range(ends[3], ends[3] + WINDOW_DAYS):
            returns[position] = 0.01

        found = build_series(returns, _DATES[1:], ends)
        lifted = [
            value
            for month, value in zip(found.months, found.episodes, strict=True)
            if month == _DATES[1:][ends[3]]
        ]

        assert lifted[0] == pytest.approx(0.04)

    def test_the_outside_mean_is_subtracted_with_the_days_matched(self) -> None:
        """**窓の外が上がっていれば、差はその分だけ小さくなる。**"""
        ends = _month_ends()
        returns = [0.0] * (len(_DATES) - 1)
        for position in range(ends[3], ends[3] + WINDOW_DAYS):
            returns[position] = 0.01
        # 直前の窓の外を、すべて +0.5% にする。
        for position in range(ends[2] + WINDOW_DAYS, ends[3]):
            returns[position] = 0.005

        found = build_series(returns, _DATES[1:], ends)
        value = next(
            episode
            for month, episode in zip(found.months, found.episodes, strict=True)
            if month == _DATES[1:][ends[3]]
        )

        assert value == pytest.approx(0.04 - 4 * 0.005)

    def test_the_daily_returns_drop_the_first_day(self) -> None:
        """**最初の日はリターンを作れない。** 日付も揃えて落とす。"""
        assert daily_returns([100.0, 110.0, 121.0]) == [
            pytest.approx(0.1),
            pytest.approx(0.1),
        ]

    def test_a_zero_price_becomes_nan_not_a_return(self) -> None:
        found = daily_returns([100.0, 0.0, 110.0])

        assert np.isnan(found[0])
        assert np.isnan(found[1])


class TestTheWindowIsFourTradingDays:
    """**祝日では減らない。** 事前登録の「祝日で欠ける」は間違いだった。"""

    def test_the_length_is_the_one_the_prereg_fixed(self) -> None:
        assert (WINDOW_BEFORE, WINDOW_AFTER, WINDOW_DAYS) == (1, 3, 4)

    def test_every_window_is_four_days(self) -> None:
        returns = [0.0] * (len(_DATES) - 1)

        found = build_series(returns, _DATES[1:], _month_ends())

        assert set(found.window_days) == {WINDOW_DAYS}

    def test_a_calendar_with_gaps_still_gives_four(self) -> None:
        """**暦に穴を開けても、営業日の数は変わらない。**

        営業日で数えていることを、実際に穴を開けて確かめる。
        """
        index = _INDEX.delete([40, 41, 60, 61, 62])
        dates = [stamp.date() for stamp in index]
        returns = [0.0] * (len(dates) - 1)

        found = build_series(returns, dates[1:], _month_ends(index))

        assert set(found.window_days) == {WINDOW_DAYS}
        assert not any("営業日でない" in line for line in found.warnings())


class TestNoOverlapAndNoGap:
    """**どの営業日も、ちょうど1つの窓か1つの窓外に属する。**"""

    def test_the_outside_block_sits_between_the_windows(self) -> None:
        ends = _month_ends()
        returns = [0.0] * (len(_DATES) - 1)

        found = build_series(returns, _DATES[1:], ends)

        for window, outside in zip(found.window_days, found.outside_days, strict=True):
            assert window == WINDOW_DAYS
            assert outside > 0

    def test_the_day_counts_add_up_to_the_month_gap(self) -> None:
        """**足して合わないなら、どこかを二重に数えているか落としている。**"""
        ends = _month_ends()
        returns = [0.0] * (len(_DATES) - 1)

        found = build_series(returns, _DATES[1:], ends)
        usable = [position for position in ends if _DATES[1:][position] >= USABLE_FROM]

        for index, (window, outside) in enumerate(
            zip(found.window_days, found.outside_days, strict=True), start=1
        ):
            here = usable.index(next(p for p in usable if _DATES[1:][p] == found.months[index - 1]))
            gap = usable[here] - usable[here - 1]
            assert window + outside == gap, f"{found.months[index - 1]}"


class TestTheWindowsCannotSeeTheFuture:
    def test_the_first_episode_is_skipped(self) -> None:
        """**前の窓が無いと、窓の外を切り出せない。**"""
        ends = _month_ends()
        returns = [0.0] * (len(_DATES) - 1)

        found = build_series(returns, _DATES[1:], ends)

        assert _DATES[1:][ends[0]] not in found.months

    def test_nothing_after_the_cut_is_read(self) -> None:
        """**窓の終わりまで見る。** 月末だけ見ると、窓が OOS にはみ出す。"""
        ends = _month_ends()
        returns = [0.0] * (len(_DATES) - 1)
        cut = _DATES[1:][ends[5]]

        found = build_series(returns, _DATES[1:], ends, end=cut)

        # 最後の月替わりの窓の終わりが、cut を越えていない。
        last = found.months[-1]
        position = next(p for p in ends if _DATES[1:][p] == last)
        assert _DATES[1:][position + WINDOW_DAYS - 1] <= cut

    def test_the_floor_is_respected(self) -> None:
        returns = [0.0] * (len(_DATES) - 1)

        found = build_series(returns, _DATES[1:], _month_ends())

        assert found.months[0] >= USABLE_FROM


class TestItRefusesWhatItCannotDo:
    def test_mismatched_lengths_are_refused(self) -> None:
        with pytest.raises(ValueError, match="長さが違う"):
            build_series([0.0, 0.0], _DATES[:3], _month_ends())

    def test_too_few_month_ends_are_refused(self) -> None:
        with pytest.raises(ValueError, match="月末"):
            build_series([0.0] * (len(_DATES) - 1), _DATES[1:], [10])

    def test_a_windows_list_of_the_wrong_size_is_refused(self) -> None:
        """**この検査が落ちる条件を、実際に1つ作る。**"""
        ends = _month_ends()
        with pytest.raises(ValueError, match="windows"):
            build_series([0.0] * (len(_DATES) - 1), _DATES[1:], ends, windows=[(0, 4)])

    def test_an_empty_frame_is_refused(self) -> None:
        with pytest.raises(ValueError, match="価格が無い"):
            series_from_prices(pd.DataFrame(), "1306")


class TestTheWholeThingRunsFromPrices:
    """**部品だけでなく、組み立てを1本通す。**"""

    @staticmethod
    def _frame(returns: list[float]) -> pd.DataFrame:
        close = [100.0]
        for value in returns:
            close.append(close[-1] * (1.0 + value))
        index = _INDEX[: len(close)]
        return pd.DataFrame(
            {
                OPEN: close,
                HIGH: close,
                LOW: close,
                CLOSE: close,
                ADJ_CLOSE: close,
                VOLUME: [1_000_000.0] * len(close),
            },
            index=index,
        )

    def test_it_produces_episodes(self) -> None:
        found = series_from_prices(self._frame([0.0] * 400), "1306")

        assert found.episodes
        assert found.source == "1306"
        assert len(found.months) == len(found.episodes)

    def test_the_summary_names_what_it_dropped(self) -> None:
        found = series_from_prices(self._frame([0.0] * 400), "1306")

        assert "窓の外が無くて捨てた" in found.summary()
        assert "窓が短くて捨てた" in found.summary()


class TestThePlaceboMovesOnlyTheLabel:
    """**リターンは本物のまま、窓の位置だけを乱数にする。**"""

    def test_the_fake_window_never_overlaps_the_real_one(self) -> None:
        """**重ねると本物の効果が漏れ込む。**"""
        ends = _month_ends()

        drawn = placebo_windows(ends, WINDOW_DAYS, seed=5)

        for index in range(1, len(ends)):
            begin, length = drawn[index]
            assert begin + length - 1 < ends[index], f"{index} 番目が本物に重なっている"
            assert begin > ends[index - 1] + WINDOW_DAYS - 1, f"{index} 番目が前の窓に重なる"

    def test_it_reproduces_from_its_seed(self) -> None:
        ends = _month_ends()

        assert placebo_windows(ends, WINDOW_DAYS, seed=5) == placebo_windows(
            ends, WINDOW_DAYS, seed=5
        )

    def test_a_different_seed_draws_differently(self) -> None:
        ends = _month_ends()

        assert placebo_windows(ends, WINDOW_DAYS, seed=5) != placebo_windows(
            ends, WINDOW_DAYS, seed=6
        )

    def test_the_placebo_does_not_see_the_real_lift(self) -> None:
        """**本物の窓に効果を置いても、偽の窓では拾わない。**

        拾ってしまうなら、対照が対照になっていない。
        """
        ends = _month_ends()
        returns = [0.0] * (len(_DATES) - 1)
        for end in ends:
            for position in range(end, min(end + WINDOW_DAYS, len(returns))):
                returns[position] = 0.01

        real = build_series(returns, _DATES[1:], ends)
        fake = build_series(
            returns,
            _DATES[1:],
            ends,
            windows=placebo_windows(ends, WINDOW_DAYS, seed=5),
        )

        assert float(np.mean(real.episodes)) > 0.03
        assert float(np.mean(fake.episodes)) < 0.0

    def test_a_zero_length_window_is_refused(self) -> None:
        with pytest.raises(ValueError, match="length"):
            placebo_windows(_month_ends(), 0)


class TestTheTailIsReported:
    @staticmethod
    def _series(episodes: list[float]) -> TurnOfMonthSeries:
        return TurnOfMonthSeries(
            source="test",
            months=[dt.date(2010, 1, 29)] * len(episodes),
            episodes=episodes,
            window_days=[WINDOW_DAYS] * len(episodes),
            outside_days=[16] * len(episodes),
            skipped_no_outside=0,
            skipped_short_window=0,
        )

    def test_the_worst_month_is_the_minimum(self) -> None:
        assert self._series([0.02, -0.09, 0.01]).worst_month() == pytest.approx(-0.09)

    def test_the_left_tail_averages_a_band(self) -> None:
        found = self._series([-0.10, -0.06, 0.01, 0.02, 0.03]).left_tail(share=0.4)

        assert found == pytest.approx(-0.08)

    def test_the_hit_rate_counts_positive_months(self) -> None:
        assert self._series([0.01, -0.01, 0.02, 0.03]).hit_rate() == pytest.approx(0.75)

    def test_an_empty_series_says_nan_rather_than_zero(self) -> None:
        empty = self._series([])

        assert np.isnan(empty.worst_month())
        assert np.isnan(empty.hit_rate())
        assert empty.warnings() == ["**月替わりを1回も作れなかった。**"]
