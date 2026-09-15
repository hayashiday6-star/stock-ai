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
    census,
    from_archive,
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
    def test_the_span_comes_from_the_data(self) -> None:
        report = census(parse_topix(SAMPLE.read_bytes()))

        assert report.first == dt.date(2022, 1, 4)
        assert report.last == dt.date(2022, 1, 7)
        assert report.rows == 4

    def test_a_long_weekend_is_not_a_gap(self) -> None:
        """**連休を穴と呼ぶと、毎年の連休が並んで本当の欠けが埋もれる。**"""
        payload = b"Date,O,H,L,C\n2022-01-04,1,2,3,100\n2022-01-09,1,2,3,100\n"

        assert census(parse_topix(payload)).gaps == []

    def test_a_real_hole_shows_up(self) -> None:
        payload = b"Date,O,H,L,C\n2022-01-04,1,2,3,100\n2022-03-04,1,2,3,100\n"

        ((when, span),) = census(parse_topix(payload)).gaps

        assert when == dt.date(2022, 3, 4)
        assert span == 59

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
