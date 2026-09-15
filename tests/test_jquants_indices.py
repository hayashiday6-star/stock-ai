"""TOPIX の指数そのものを読む。

ベンチマークに 1306（TOPIX 連動 ETF）を使っているのは、**指数が手元に無かった
から**である。Premium で `/indices/bars/daily/topix` が開き、2008-05 からの
18年ぶんが取れるようになった。

ここで押さえるのは、**ETF と指数を同じものとして扱わないこと**である。信託
報酬と追跡のずれは、18年ぶん積み上がると小さくない——**ただしそれは見込み
であって、測った値ではない。** 引き算をする道具を置く。
"""

from __future__ import annotations

import datetime as dt
import gzip
from pathlib import Path

import pandas as pd

from stock_ai.data.jquants_archive import archive
from stock_ai.data.jquants_bulk import BulkFile
from stock_ai.data.jquants_indices import (
    GapTrail,
    TrackingGap,
    census,
    from_archive,
    gap_trail,
    parse_topix,
    tracking_gap,
)
from stock_ai.data.schema import CLOSE, HIGH, LOW, OPEN

TODAY = dt.date(2026, 9, 15)
FIXTURES = Path(__file__).parent / "fixtures"

#: 配布サンプル。**実物から作った fixture である。**
SAMPLE = FIXTURES / "jquants_topix_sample.csv"


class TestReadingTheRealSample:
    """**fixture は実物から作る。** 列名を想像で書かない。"""

    def test_the_four_columns_are_read(self) -> None:
        frame = parse_topix(SAMPLE.read_bytes())

        row = frame.iloc[0]
        assert row[OPEN] == 2015.61
        assert row[HIGH] == 2031.65
        assert row[LOW] == 2005.45
        assert row[CLOSE] == 2030.22

    def test_the_index_is_the_date(self) -> None:
        frame = parse_topix(SAMPLE.read_bytes())

        assert frame.index[0] == pd.Timestamp(2022, 1, 4)
        assert frame.index.is_monotonic_increasing

    def test_no_volume_column_is_invented(self) -> None:
        """**0 を入れない。** 「売買が無かった日」と区別が付かなくなる。"""
        frame = parse_topix(SAMPLE.read_bytes())

        assert "volume" not in frame.columns

    def test_every_sample_row_survives(self) -> None:
        assert len(parse_topix(SAMPLE.read_bytes())) == 4


class TestRowsThatShouldNotBeThere:
    def test_a_row_without_a_close_is_dropped(self) -> None:
        """指数に「売買が無かった日」は無い。**読み違いか欠けである。**"""
        payload = b"Date,O,H,L,C\n2022-01-04,1,2,3,\n2022-01-05,1,2,3,2039.27\n"

        assert len(parse_topix(payload)) == 1

    def test_a_zero_close_is_not_a_level(self) -> None:
        payload = b"Date,O,H,L,C\n2022-01-04,1,2,3,0\n"

        assert parse_topix(payload).empty

    def test_a_row_without_a_date_is_dropped(self) -> None:
        payload = b"Date,O,H,L,C\n,1,2,3,100\n"

        assert parse_topix(payload).empty

    def test_a_missing_open_falls_back_to_the_close(self) -> None:
        payload = b"Date,O,H,L,C\n2022-01-04,,,,2030.22\n"

        row = parse_topix(payload).iloc[0]

        assert row[OPEN] == 2030.22
        assert row[HIGH] == 2030.22

    def test_an_empty_payload_gives_an_empty_frame_with_columns(self) -> None:
        """**空でも列は保つ。** 呼ぶ側が列の有無で分岐せずに済む。"""
        frame = parse_topix(b"")

        assert frame.empty
        assert list(frame.columns) == [OPEN, HIGH, LOW, CLOSE]


class TestReadingTheArchive:
    def _archive(self, tmp_path: Path, body: bytes, name: str = "topix_202201") -> None:
        payload = gzip.compress(body)
        archive(
            [
                BulkFile(
                    key=f"indices/bars/daily/topix/historical/2022/{name}.csv.gz",
                    last_modified="",
                    size=len(payload),
                )
            ],
            lambda _k: payload,
            tmp_path,
            on=TODAY,
        )

    def test_it_reads_what_was_saved(self, tmp_path: Path) -> None:
        self._archive(tmp_path, SAMPLE.read_bytes())

        assert len(from_archive(tmp_path)) == 4

    def test_a_date_in_two_files_is_kept_once(self, tmp_path: Path) -> None:
        """**月ごとの原本と `live` が重なる期間がある。** 数が増えないこと。"""
        self._archive(tmp_path, b"Date,O,H,L,C\n2022-01-04,1,2,3,100\n", "topix_202201")
        self._archive(tmp_path, b"Date,O,H,L,C\n2022-01-04,1,2,3,999\n", "topix_live")

        frame = from_archive(tmp_path)

        assert len(frame) == 1

    def test_other_endpoints_are_left_alone(self, tmp_path: Path) -> None:
        payload = gzip.compress(b"Date,Code,C\n2022-01-04,13010,100\n")
        archive(
            [BulkFile(key="equities/bars/daily/x.csv.gz", last_modified="", size=len(payload))],
            lambda _k: payload,
            tmp_path,
            on=TODAY,
        )

        assert from_archive(tmp_path).empty

    def test_nothing_archived_is_empty_not_an_error(self, tmp_path: Path) -> None:
        assert from_archive(tmp_path).empty


class TestCountingTheShape:
    """**穴は当て推量で数えない。**

    最初は「暦で5日を超えて空いたら穴」と数えていた。2026-09-15 の実測で21件
    出て、**21件とも年末年始・ゴールデンウィーク・シルバーウィークだった。**

    「連休は5日まで」は日本の休場を調べずに書いたもので、**しかも取引カレン
    ダーは手元にあった。** 持っている答えを使わずに経験則で代用していた。
    """

    def test_the_span_comes_from_the_data(self) -> None:
        report = census(parse_topix(SAMPLE.read_bytes()))

        assert report.first == dt.date(2022, 1, 4)
        assert report.last == dt.date(2022, 1, 7)
        assert report.rows == 4

    def test_without_a_calendar_no_hole_is_claimed(self) -> None:
        """**0 と報告しない。** 渡し忘れた実行が「穴なし」に見える。"""
        report = census(parse_topix(SAMPLE.read_bytes()))

        assert report.checked is False
        assert "見ていない" in report.summary()

    def test_a_new_year_break_is_not_a_hole(self) -> None:
        """大納会から大発会まで6日空く。**暦の日数で数えると毎年ここが並ぶ。**"""
        payload = b"Date,O,H,L,C\n2008-12-30,1,2,3,100\n2009-01-05,1,2,3,100\n"
        trading = {dt.date(2008, 12, 30), dt.date(2009, 1, 5)}

        report = census(parse_topix(payload), trading=trading)

        assert report.missing == []
        assert report.checked is True

    def test_a_trading_day_with_no_index_is_a_hole(self) -> None:
        payload = b"Date,O,H,L,C\n2022-01-04,1,2,3,100\n2022-01-06,1,2,3,100\n"
        trading = {dt.date(2022, 1, 4), dt.date(2022, 1, 5), dt.date(2022, 1, 6)}

        report = census(parse_topix(payload), trading=trading)

        assert report.missing == [dt.date(2022, 1, 5)]

    def test_an_index_day_the_calendar_denies_is_reported(self) -> None:
        """**カレンダーのほうが疑わしい向きである。** 指数はその日の実物。"""
        payload = b"Date,O,H,L,C\n2022-01-04,1,2,3,100\n2022-01-05,1,2,3,100\n"
        trading = {dt.date(2022, 1, 4)}

        report = census(parse_topix(payload), trading=trading)

        assert report.extra == [dt.date(2022, 1, 5)]

    def test_calendar_days_outside_the_span_are_not_holes(self) -> None:
        """**カレンダーは指数より長い。** 全期間で取ると何千日も出る。"""
        payload = b"Date,O,H,L,C\n2022-01-04,1,2,3,100\n2022-01-05,1,2,3,100\n"
        trading = {dt.date(2008, 5, 7), dt.date(2022, 1, 4), dt.date(2022, 1, 5)}

        report = census(parse_topix(payload), trading=trading)

        assert report.missing == []

    def test_duplicates_are_reported_not_hidden(self) -> None:
        frame = parse_topix(SAMPLE.read_bytes())

        assert census(frame, raw_rows=6).duplicates == 2

    def test_an_empty_frame_says_so(self) -> None:
        assert "原本が無い" in census(parse_topix(b"")).summary()


class TestTheGapBetweenTheEtfAndTheIndex:
    """**ETF と指数を同じものとして扱わない。**

    信託報酬は毎日引かれ、追跡のずれが乗る。18年ぶん積み上がると小さくない
    ——**が、それは見込みであって測った値ではない。** 引き算をする。
    """

    def _index(self, levels: list[float]) -> pd.DataFrame:
        dates = pd.date_range("2022-01-04", periods=len(levels), freq="D")
        return pd.DataFrame({OPEN: levels, HIGH: levels, LOW: levels, CLOSE: levels}, index=dates)

    def _etf(self, levels: list[float], offset: int = 0) -> pd.Series:
        dates = pd.date_range("2022-01-04", periods=len(levels), freq="D")[offset:]
        return pd.Series(levels[offset:], index=dates)

    def test_a_perfect_tracker_shows_no_gap(self) -> None:
        gap = tracking_gap(self._index([100.0, 110.0]), self._etf([100.0, 110.0]))

        assert gap.total == 0.0
        assert gap.index_return == gap.etf_return

    def test_the_etf_lagging_shows_as_a_negative_gap(self) -> None:
        """信託報酬は ETF 側だけを削る。**差は負に出る。**"""
        gap = tracking_gap(self._index([100.0, 110.0]), self._etf([100.0, 109.0]))

        assert gap.total < 0
        assert round(gap.index_return, 6) == 0.10
        assert round(gap.etf_return, 6) == 0.09

    def test_only_the_shared_days_are_used(self) -> None:
        """**片方にしかない日を差に数えない。** 上場の前後で重なりが短くなる。"""
        index = self._index([100.0, 105.0, 110.0])
        gap = tracking_gap(index, self._etf([100.0, 105.0, 110.0], offset=1))

        assert gap.days == 2
        assert gap.first == dt.date(2022, 1, 5)

    def test_one_shared_day_is_not_enough(self) -> None:
        """1日では収益率が作れない。**0 と報告しない。**"""
        index = self._index([100.0, 105.0])
        gap = tracking_gap(index, self._etf([100.0, 105.0], offset=1))

        assert gap.days == 0
        assert "比べられない" in gap.summary()

    def test_nothing_to_compare_says_so(self) -> None:
        empty = pd.Series(dtype=float)

        assert "比べられない" in tracking_gap(self._index([100.0]), empty).summary()

    def test_the_annual_figure_spreads_the_gap_over_the_years(self) -> None:
        """**18年ぶんの差をそのまま「年あたり」と呼ばない。**"""
        levels = [100.0 + index for index in range(490)]  # 2年ぶん（245営業日 × 2）
        etf = [value * 0.98 ** (index / 489) for index, value in enumerate(levels)]
        gap = tracking_gap(self._index(levels), self._etf(etf))

        assert gap.total < 0
        assert gap.annual > gap.total  # 年あたりは全体より小さい幅になる


class TestWhenTheGapAppeared:
    """**端点だけでは分からない。**

    実測で 18年ぶん +5.8%（年 +0.31%）と出た。ETF が指数を上回る向きは、
    信託報酬が ETF を削る側である以上、そもそも説明が付かない（2026-09-16）。

    なだらかな積み重ねなら毎日効く要因、1日で飛んでいるなら分割の調整が
    片方で抜けた継ぎ目である。**次にやることが正反対になる。**
    """

    def _series(self, count: int = 500):
        import numpy as np

        days = pd.bdate_range("2020-01-01", periods=count)
        rng = np.random.default_rng(0)
        level = 1000 * np.cumprod(1 + rng.normal(0, 0.01, count))
        return days, pd.Series(level, index=days)

    def test_a_steady_drift_is_not_called_concentrated(self) -> None:
        import numpy as np

        days, index = self._series()
        etf = index * np.cumprod(np.full(len(days), 1 + 0.003 / 252))

        trail = gap_trail(pd.DataFrame({CLOSE: index}), etf)

        assert not trail.concentrated
        assert trail.largest_day < 0.01

    def test_a_steady_drift_shows_the_same_amount_every_year(self) -> None:
        import numpy as np

        days, index = self._series(count=800)
        etf = index * np.cumprod(np.full(len(days), 1 + 0.003 / 252))

        by_year = gap_trail(pd.DataFrame({CLOSE: index}), etf).by_year

        assert len(by_year) >= 3
        assert max(by_year.values()) - min(by_year.values()) < 0.005

    def test_one_unadjusted_step_is_caught(self) -> None:
        """**分割の調整が片方で抜けると、この形になる。**"""
        import numpy as np

        days, index = self._series()
        step = np.ones(len(days))
        step[300] = 1.06
        etf = index * np.cumprod(step)

        trail = gap_trail(pd.DataFrame({CLOSE: index}), etf)

        assert trail.concentrated
        assert trail.worst[0][0] == days[300].date()

    def test_the_step_lands_in_the_year_it_happened(self) -> None:
        import numpy as np

        days, index = self._series()
        step = np.ones(len(days))
        step[300] = 1.06
        etf = index * np.cumprod(step)

        trail = gap_trail(pd.DataFrame({CLOSE: index}), etf)
        hit = days[300].year

        assert trail.by_year[hit] > 0.05
        assert all(abs(value) < 0.01 for year, value in trail.by_year.items() if year != hit)

    def test_returns_are_compared_day_by_day_not_by_level(self) -> None:
        """**水準で比べない。** 最初の1日のずれが最後まで効き続ける。"""

        days, index = self._series()
        # 初日だけ 5% 高い水準から始まり、あとは完全に同じ動き
        etf = index * 1.05

        trail = gap_trail(pd.DataFrame({CLOSE: index}), etf)

        assert trail.largest_day < 1e-9
        assert all(abs(value) < 1e-9 for value in trail.by_year.values())

    def test_too_few_shared_days_report_nothing(self) -> None:
        days = pd.bdate_range("2020-01-01", periods=2)
        index = pd.DataFrame({CLOSE: [1.0, 2.0]}, index=days)

        assert gap_trail(index, pd.Series([1.0, 2.0], index=days)).by_year == {}

    def test_an_empty_side_does_not_raise(self) -> None:
        assert gap_trail(pd.DataFrame({CLOSE: []}), pd.Series(dtype=float)).by_year == {}


class TestReturnsAreNotSubtracted:
    """**収益率どうしを引き算しない。**

    指数 +191.3% / ETF +197.1% を引くと +5.8% になる。だが水準は 2.913倍と
    2.971倍で、**比は +2.0% である。差を 2.9倍に水増ししていた**
    （2026-09-16）。

    引き算が合うのは収益率が小さいときだけで、18年ぶんはそうではない。
    """

    def _gap(self, index_return: float, etf_return: float) -> TrackingGap:
        return TrackingGap(
            days=4491,
            first=dt.date(2008, 5, 7),
            last=dt.date(2026, 9, 14),
            index_return=index_return,
            etf_return=etf_return,
        )

    def test_the_real_pair_comes_out_at_two_percent(self) -> None:
        assert abs(self._gap(1.913, 1.971).total - 0.0199) < 0.0005

    def test_subtracting_would_have_said_five_point_eight(self) -> None:
        """**踏んだ間違いを固定しておく。** 戻ったら、この差で気付ける。"""
        naive = 1.971 - 1.913

        assert abs(naive - 0.058) < 0.0005
        assert self._gap(1.913, 1.971).total < naive / 2

    def test_small_returns_make_the_two_nearly_agree(self) -> None:
        """引き算が合うのは、収益率が小さいときだけである。"""
        gap = self._gap(0.01, 0.02)

        assert abs(gap.total - 0.01) < 0.0002

    def test_the_annual_figure_follows_the_corrected_total(self) -> None:
        assert abs(self._gap(1.913, 1.971).annual - 0.0011) < 0.0003

    def test_an_index_that_lost_everything_does_not_divide_by_zero(self) -> None:
        assert self._gap(-1.0, 0.5).total == 0.0


class TestSayingWhenTheYearlyMeanCannotBeTold:
    """**散らばりを見ずに平均だけ出さない。**

    年ごとの差は −1.38% から +0.62% まで振れている。平均 +0.045% はその中に
    埋もれており、**0 も、信託報酬ぶんの負の値も、95% の幅の中にある。**
    """

    REAL = [
        0.0024,
        -0.0138,
        0.0011,
        -0.0015,
        0.0024,
        0.0025,
        0.0043,
        0.0025,
        -0.0023,
        0.0041,
        0.0062,
        0.0007,
        0.0022,
        0.0015,
        0.0023,
        0.0020,
        -0.0027,
        0.0013,
        -0.0067,
    ]

    def _trail(self, values: list[float]) -> GapTrail:
        return GapTrail(
            by_year={2008 + index: value for index, value in enumerate(values)},
            worst=[],
            daily_median=0.0008,
        )

    def test_the_measured_years_cannot_be_told_from_zero(self) -> None:
        trail = self._trail(self.REAL)

        assert not trail.distinguishable
        low, high = trail.interval
        assert low < 0.0 < high

    def test_the_mean_is_much_smaller_than_the_headline_suggested(self) -> None:
        assert abs(self._trail(self.REAL).mean_year) < 0.001

    def test_a_steady_one_sided_drift_is_distinguishable(self) -> None:
        """**幅が狭ければ、小さな差でも言える。** 区別できないのは散らばりのせい。

        毎年ぴったり同じ値にはしない。**散らばりが 0 だと、何年ぶんでも幅が
        点になる**——それは現実のデータには無い形である。実測と同じくらいの
        揺れを乗せたうえで、向きが揃っていれば言えることを見る。
        """
        drift = [0.003 + (0.0004 if index % 2 else -0.0004) for index in range(19)]
        trail = self._trail(drift)

        assert trail.distinguishable
        low, _high = trail.interval
        assert low > 0.0

    def test_one_year_cannot_have_a_spread(self) -> None:
        trail = self._trail([0.003])

        assert trail.stderr_year == 0.0
        assert not trail.distinguishable

    def test_no_years_at_all(self) -> None:
        trail = GapTrail({}, [], 0.0)

        assert trail.mean_year == 0.0
        assert not trail.distinguishable
