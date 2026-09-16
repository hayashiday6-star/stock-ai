"""月末の PBR だけを抜いて、1つのファイルに落とす。

**暦の月末を探さない。** 月の途中で上場廃止になった銘柄は、その日が最後の
観測である。暦の月末で引くと、**その銘柄がその月から丸ごと消える**——生存
バイアスを入れない、というこのプロジェクトの前提に反する。

**空の `pbr` は 0 で埋めない。** 落とした件数は返すので、どれだけ落ちたかは
見える。
"""

from __future__ import annotations

import datetime as dt
import gzip
from pathlib import Path

from stock_ai.data.jquants_valuation import parse_valuation
from stock_ai.data.valuation_monthly import COLUMNS, build, month_ends, read

HEADER = "Date,Code,EPS,FwdEPS,BPS,ROE,FwdROE,PER,FwdPER,PBR,MktCap"


def _rows(*rows: str) -> bytes:
    return ("\n".join([HEADER, *rows]) + "\n").encode("utf-8")


def _bar(day: str, code: str, pbr: float = 1.25) -> str:
    return f"{day},{code},100.00,,2000.00,8.0000,,25.00,,{pbr:.2f},1.0"


class TestMonthEndIsTheLastObservationNotTheCalendar:
    def test_the_last_day_present_wins(self) -> None:
        frame = parse_valuation(
            _rows(_bar("2024-06-03", "13010"), _bar("2024-06-28", "13010", pbr=2.0))
        )

        kept = month_ends(frame)

        assert len(kept) == 1
        assert kept.iloc[0]["pbr"] == 2.0

    def test_a_symbol_that_vanished_mid_month_is_kept(self) -> None:
        """**暦の月末で引くと、この銘柄がその月から丸ごと消える。**"""
        frame = parse_valuation(
            _rows(_bar("2024-06-28", "13010"), _bar("2024-06-10", "13020", pbr=3.0))
        )

        kept = month_ends(frame)

        assert set(kept["symbol"]) == {"1301", "1302"}
        assert float(kept[kept["symbol"] == "1302"].iloc[0]["pbr"]) == 3.0

    def test_each_month_keeps_its_own_last_day(self) -> None:
        frame = parse_valuation(
            _rows(
                _bar("2024-06-28", "13010", pbr=1.0),
                _bar("2024-07-31", "13010", pbr=2.0),
            )
        )

        kept = month_ends(frame).sort_values("date")

        assert [float(value) for value in kept["pbr"]] == [1.0, 2.0]

    def test_an_empty_frame_comes_back_empty(self) -> None:
        assert month_ends(parse_valuation(_rows())).empty


class TestNotFillingBlanksWithZero:
    def _archive(self, tmp_path: Path, rows: list[str]) -> Path:
        from stock_ai.data.jquants_archive import archive
        from stock_ai.data.jquants_bulk import BulkFile

        payload = gzip.compress(_rows(*rows))
        folder = tmp_path / "arch"
        archive(
            [
                BulkFile(
                    key="equities/valuation/historical/2024/eq_valuation_202406.csv.gz",
                    last_modified="",
                    size=len(payload),
                )
            ],
            lambda _k: payload,
            folder,
            on=dt.date(2026, 9, 15),
        )
        return folder

    def test_a_missing_pbr_is_dropped_and_counted(self, tmp_path: Path) -> None:
        blank = "2024-06-28,13020,100.00,,2000.00,8.0000,,25.00,,,1.0"
        folder = self._archive(tmp_path, [_bar("2024-06-28", "13010"), blank])

        report = build(folder, tmp_path / "out.csv.gz")

        assert report.rows == 1
        assert report.dropped_no_pbr == 1

    def test_a_real_zero_pbr_is_not_dropped(self, tmp_path: Path) -> None:
        """**空と 0 は別である。** 0 は「いちばん割安」として選別に入ってくる。"""
        folder = self._archive(tmp_path, [_bar("2024-06-28", "13010", pbr=0.0)])

        report = build(folder, tmp_path / "out.csv.gz")

        assert report.rows == 1
        assert report.dropped_no_pbr == 0

    def test_the_summary_always_names_what_was_dropped(self, tmp_path: Path) -> None:
        blank = "2024-06-28,13020,100.00,,2000.00,8.0000,,25.00,,,1.0"
        folder = self._archive(tmp_path, [_bar("2024-06-28", "13010"), blank])

        assert "落とした" in build(folder, tmp_path / "out.csv.gz").summary()

    def test_nothing_readable_says_so_rather_than_writing_an_empty_file(
        self, tmp_path: Path
    ) -> None:
        report = build(tmp_path / "nothing", tmp_path / "out.csv.gz")

        assert report.rows == 0
        assert "読めていない" in report.summary()


class TestTheFileCanBeReadBack:
    def _write(self, tmp_path: Path) -> Path:
        from stock_ai.data.jquants_archive import archive
        from stock_ai.data.jquants_bulk import BulkFile

        payload = gzip.compress(_rows(_bar("2024-06-28", "13010"), _bar("2024-06-28", "99840")))
        folder = tmp_path / "arch"
        archive(
            [
                BulkFile(
                    key="equities/valuation/historical/2024/eq_valuation_202406.csv.gz",
                    last_modified="",
                    size=len(payload),
                )
            ],
            lambda _k: payload,
            folder,
            on=dt.date(2026, 9, 15),
        )
        target = tmp_path / "out.csv.gz"
        build(folder, target)
        return target

    def test_the_columns_are_the_ones_promised(self, tmp_path: Path) -> None:
        frame = read(self._write(tmp_path))

        assert list(frame.columns) == list(COLUMNS)

    def test_the_date_comes_back_as_a_date(self, tmp_path: Path) -> None:
        """**文字列のままにしない。** 月で切るたびに変換が要り、どこかで忘れる。"""
        frame = read(self._write(tmp_path))

        assert isinstance(frame.iloc[0]["date"], dt.date)

    def test_a_leading_zero_in_the_code_survives(self, tmp_path: Path) -> None:
        """CSV を往復すると `1301` は数になる。**4桁に戻す。**"""
        frame = read(self._write(tmp_path))

        assert set(frame["symbol"]) == {"1301", "9984"}
        assert all(len(value) == 4 for value in frame["symbol"])

    def test_a_missing_file_reads_as_empty_with_columns(self, tmp_path: Path) -> None:
        frame = read(tmp_path / "absent.csv.gz")

        assert frame.empty
        assert list(frame.columns) == list(COLUMNS)

    def test_building_twice_gives_the_same_content(self, tmp_path: Path) -> None:
        """**生成物なので、回すたびに変わらない。**

        中身で比べる。gzip は書いた時刻を包みに入れるので、**生のバイト列は
        毎回違う。** そこで比べると、変わっていないものを「変わった」と読む。
        """
        first = gzip.decompress(self._write(tmp_path).read_bytes())
        second = gzip.decompress(self._write(tmp_path).read_bytes())

        assert first == second
