"""1株あたりの指標と時価総額を読む。

**時価総額を自前で組み立てない**ための読み口である。株価 × 発行済株式数 は、
分割を跨ぐと尺度が変わる——このプロジェクトが繰り返し踏んでいる形そのもの。

ここで押さえるのは3つ。

1. **空を 0 にしない。** PBR が空の銘柄と PBR が 0 の銘柄は別のもので、0 を
   入れると「いちばん割安」な顔をして選別に入ってくる。
2. **実績と会社予想を混ぜない。** 混ぜれば、発表前から予想を知っていたことに
   なる。
3. **このファイルだけで中身を確かめられる。** `PER × EPS` と `PBR × BPS` は
   どちらも終値を指す。合わなければ列の意味が想像と違う。
"""

from __future__ import annotations

import datetime as dt
import gzip
from pathlib import Path

from stock_ai.data.jquants_valuation import (
    IDENTITY_FLOOR,
    census,
    from_archive,
    identity_check,
    parse_valuation,
    unknown_columns,
)
from stock_ai.data.schema import DATE

HEADER = "Date,Code,EPS,FwdEPS,BPS,ROE,FwdROE,PER,FwdPER,PBR,MktCap"


def _csv(*rows: str) -> bytes:
    return ("\n".join([HEADER, *rows]) + "\n").encode("utf-8")


#: 終値 2,500 円をちょうど指す行。`PER × EPS` も `PBR × BPS` も 2,500。
CONSISTENT = "2026-08-03,13010,100,120,2000,5.0,6.0,25.0,20.8,1.25,250000000000"


class TestReadingTheElevenColumns:
    """列は原本から数えた11。**想像で足さない。**"""

    def test_the_actual_and_forecast_columns_land_in_separate_places(self) -> None:
        frame = parse_valuation(_csv(CONSISTENT))

        row = frame.iloc[0]
        assert row["eps"] == 100
        assert row["forward_eps"] == 120
        assert row["per"] == 25.0
        assert row["forward_per"] == 20.8

    def test_the_code_becomes_the_four_digit_symbol(self) -> None:
        assert parse_valuation(_csv(CONSISTENT)).iloc[0]["symbol"] == "1301"

    def test_the_date_is_a_date(self) -> None:
        assert parse_valuation(_csv(CONSISTENT)).iloc[0][DATE] == dt.date(2026, 8, 3)

    def test_the_market_cap_is_read_as_given(self) -> None:
        # **単位を推測して割らない。** 原本の値をそのまま持つ。
        assert parse_valuation(_csv(CONSISTENT)).iloc[0]["market_cap"] == 250_000_000_000


class TestEmptyIsNotZero:
    """**空の PBR を 0 にすると、いちばん割安な顔をして選別に入ってくる。**"""

    def test_a_blank_stays_missing(self) -> None:
        blank = "2008-06-02,13010,,,,,,,,,"
        frame = parse_valuation(_csv(blank))

        assert frame.iloc[0]["pbr"] is None or frame.iloc[0]["pbr"] != frame.iloc[0]["pbr"]
        assert frame.iloc[0]["market_cap"] != 0

    def test_a_real_zero_is_kept(self) -> None:
        zero = "2026-08-03,13010,0,0,0,0,0,0,0,0,0"
        assert parse_valuation(_csv(zero)).iloc[0]["pbr"] == 0


class TestRowsThatCannotBeKeyed:
    def test_a_preferred_share_code_is_dropped(self) -> None:
        # 5桁で末尾が 0 でないものは普通株ではない。判断は `four_digit_code`
        # に借りている——**符号の規則を2つ持たない。**
        odd = "2026-08-03,13015,100,120,2000,5.0,6.0,25.0,20.8,1.25,1"
        assert parse_valuation(_csv(odd)).empty

    def test_a_row_without_a_date_is_dropped(self) -> None:
        undated = ",13010,100,120,2000,5.0,6.0,25.0,20.8,1.25,1"
        assert parse_valuation(_csv(undated)).empty

    def test_an_empty_file_gives_the_columns_anyway(self) -> None:
        # **空の表でも列があること。** 無いと、呼ぶ側が KeyError で落ちる。
        frame = parse_valuation(_csv())
        assert "market_cap" in frame.columns
        assert frame.empty


class TestNoticingANewColumn:
    """**向こうが列を増やしても例外は出ない。** 黙って読み飛ばす。"""

    def test_an_unknown_column_is_named(self) -> None:
        payload = (HEADER + ",EV\n2026-08-03,13010,1,1,1,1,1,1,1,1,1,9\n").encode("utf-8")
        assert unknown_columns(payload) == ["EV"]

    def test_the_known_eleven_raise_nothing(self) -> None:
        assert unknown_columns(_csv(CONSISTENT)) == []


class TestCheckingTheFileAgainstItself:
    """``PER × EPS`` と ``PBR × BPS`` は、どちらも終値を指す。"""

    def test_a_consistent_row_agrees(self) -> None:
        report = identity_check(parse_valuation(_csv(CONSISTENT)))

        assert report.checked == 1
        assert report.agreed == 1
        assert report.rate == 1.0

    def test_a_row_read_wrong_does_not_agree(self) -> None:
        # PBR だけ倍にすると、2つの掛け算が別の終値を指す。
        broken = "2026-08-03,13010,100,120,2000,5.0,6.0,25.0,20.8,2.50,250000000000"
        report = identity_check(parse_valuation(_csv(broken)))

        assert report.checked == 1
        assert report.agreed == 0
        assert report.worst and report.worst[0][1] == "1301"

    def test_a_blank_row_is_not_counted_as_a_disagreement(self) -> None:
        # **「合わない」と「判定できない」を分ける。** 分けないと、2008年頃の
        # 空の多さがそのまま不一致率として出て、読み違いと見分けが付かない。
        blank = "2008-06-02,13010,,,,,,,,,"
        report = identity_check(parse_valuation(_csv(blank)))

        assert report.checked == 0
        assert report.skipped_missing == 1
        assert report.rate == 0.0

    def test_a_near_zero_eps_is_not_counted_either(self) -> None:
        # EPS が 0 近傍だと、PER の丸め誤差が何倍にもなって出てくる。
        tiny = f"2026-08-03,13010,{IDENTITY_FLOOR / 100},1,2000,5.0,6.0,25.0,20.8,1.25,1"
        report = identity_check(parse_valuation(_csv(tiny)))

        assert report.checked == 0
        assert report.skipped_small == 1

    def test_rounding_alone_still_agrees(self) -> None:
        # 終値は円で丸められ、EPS・BPS は小数である。ぴったりは一致しない。
        rounded = "2026-08-03,13010,100,120,1999,5.0,6.0,25.0,20.8,1.2506,1"
        assert identity_check(parse_valuation(_csv(rounded))).agreed == 1

    def test_an_empty_frame_reports_nothing_rather_than_dividing_by_zero(self) -> None:
        assert identity_check(parse_valuation(_csv())).rate == 0.0


class TestAProductThatComesOutZero:
    """**1,588万行で落ちた形。** fixture が小さすぎて出なかった。

    `pbr` が 0 なら、`bps` がどれだけ大きくても `pbr × bps` は 0 になる。
    割り算の分母になるので、そこを `pd.NA` で塞いだら列が object 型になり、
    並べ替えが `TypeError` で落ちた（2026-09-15）。

    **コメントには「片方が 0 になる行は判定から外れているはず」と書いてあった。**
    書いたが、確かめていなかった。積に床を当てれば前提そのものが要らない。
    """

    def _zero_pbr(self) -> str:
        # bps は十分大きいが、pbr が 0。積は 0 になる。
        return "2026-08-03,13020,100,120,2000,5.0,6.0,25.0,20.8,0,1"

    def test_it_does_not_raise(self) -> None:
        frame = parse_valuation(_csv(CONSISTENT, self._zero_pbr()))

        identity_check(frame)  # 落ちないこと自体が主張である

    def test_a_zero_product_is_not_counted_as_a_disagreement(self) -> None:
        """**「合わない」と「判定できない」を分ける。** 0 は判定できない側。"""
        report = identity_check(parse_valuation(_csv(CONSISTENT, self._zero_pbr())))

        assert report.checked == 1
        assert report.agreed == 1
        assert report.skipped_small == 1
        assert report.rate == 1.0

    def test_the_worst_list_still_sorts_when_a_zero_row_is_present(self) -> None:
        """並べ替えの列が数のままであること。**ここが落ちた箇所である。**"""
        broken = "2026-08-04,13030,100,120,2000,5.0,6.0,25.0,20.8,2.50,1"
        report = identity_check(parse_valuation(_csv(CONSISTENT, self._zero_pbr(), broken)))

        assert report.agreed == 1
        assert [symbol for _, symbol, _, _ in report.worst] == ["1303"]

    def test_a_zero_eps_row_lands_in_the_same_bucket(self) -> None:
        zero_eps = "2026-08-05,13040,0,120,2000,5.0,6.0,25.0,20.8,1.25,1"
        report = identity_check(parse_valuation(_csv(CONSISTENT, zero_eps)))

        assert report.skipped_small == 1
        assert report.checked == 1


class TestAColumnThatIsEmptyInOneWholeFile:
    """**2度落ちた形。** fixture が1ファイル分しか無かったので通らなかった。

    2008年のファイルは `EPS` と `PER` が1件も埋まっていない（census で 0%）。
    全部 `None` の列は **object 型**になり、`concat` すると他のファイルの
    float 列まで引きずられる。

    **掛け算も割り算も例外を出さない。** 並べ替えのところで初めて落ちる。
    型を読み口で決めれば、どのファイルから来ても同じになる。
    """

    def test_a_column_with_nothing_in_it_is_still_numeric(self) -> None:
        frame = parse_valuation(_csv("2008-07-08,13010,,,2000,,,,,1.25,1"))

        assert frame["eps"].dtype.kind == "f", frame["eps"].dtype

    def test_every_numeric_column_is_numeric_even_when_all_empty(self) -> None:
        frame = parse_valuation(_csv("2008-07-08,13010,,,,,,,,,"))

        for column in ("eps", "bps", "roe", "per", "pbr", "market_cap"):
            assert frame[column].dtype.kind == "f", (column, frame[column].dtype)

    def test_concatenating_two_files_keeps_the_type(self) -> None:
        import pandas as pd

        empty = parse_valuation(_csv("2008-07-08,13010,,,2000,,,,,1.25,1"))
        full = parse_valuation(_csv(CONSISTENT))

        assert pd.concat([empty, full])["eps"].dtype.kind == "f"


class TestReadingAnArchiveThatMixesEmptyAndFullFiles:
    """**実データそのものの形。** 2008年のファイルと、近年のファイルが並ぶ。

    ここを通していれば、1,588万行で落ちる前に落ちていた。`from_archive` を
    呼ぶテストが `worst` まで届いていなかった。
    """

    def _archive(self, tmp_path: Path, files: dict[str, list[str]]) -> None:
        import datetime as dt

        from stock_ai.data.jquants_archive import archive
        from stock_ai.data.jquants_bulk import BulkFile

        bodies = {key: gzip.compress(_csv(*rows)) for key, rows in files.items()}
        archive(
            [BulkFile(key=key, last_modified="", size=len(body)) for key, body in bodies.items()],
            lambda key: bodies[key],
            tmp_path,
            on=dt.date(2026, 9, 15),
        )

    def _both(self, tmp_path: Path) -> None:
        self._archive(
            tmp_path,
            {
                # 2008: EPS も PER も1件も無い
                "equities/valuation/historical/2008/eq_valuation_200807.csv.gz": [
                    "2008-07-08,13010,,,2000,,,,,1.25,1",
                    "2008-07-09,13020,,,2000,,,,,1.25,1",
                ],
                # 近年: 埋まっていて、しかも食い違いが1件ある
                "equities/valuation/historical/2026/eq_valuation_202608.csv.gz": [
                    CONSISTENT,
                    "2026-08-04,13030,100,120,2000,5.0,6.0,25.0,20.8,2.50,1",
                ],
            },
        )

    def test_the_columns_stay_numeric_across_files(self, tmp_path: Path) -> None:
        self._both(tmp_path)

        assert from_archive(tmp_path)["eps"].dtype.kind == "f"

    def test_the_check_runs_to_the_end(self, tmp_path: Path) -> None:
        """**落ちないこと自体が主張である。** ここが2度落ちた。"""
        self._both(tmp_path)

        report = identity_check(from_archive(tmp_path))

        assert report.checked == 2
        assert report.agreed == 1
        assert [symbol for _, symbol, _, _ in report.worst] == ["1303"]

    def test_the_empty_year_shows_as_empty_rather_than_as_a_disagreement(
        self, tmp_path: Path
    ) -> None:
        self._both(tmp_path)
        found = census(from_archive(tmp_path))

        assert found.share(2008, "eps") == 0.0
        assert found.thin_years("eps") == [2008]


class TestCountingWhatIsEmptyByYear:
    """**公式の注意書きを引き写さない。** 手元のファイルが答える。"""

    def test_the_thin_years_are_named(self) -> None:
        frame = parse_valuation(
            _csv(
                "2008-06-02,13010,,,,,,,,,",
                "2008-06-03,13020,,,,,,,,,",
                "2026-08-03,13010,100,120,2000,5.0,6.0,25.0,20.8,1.25,1",
            )
        )
        found = census(frame)

        assert found.rows_by_year == {2008: 2, 2026: 1}
        assert found.share(2008, "market_cap") == 0.0
        assert found.share(2026, "market_cap") == 1.0
        assert found.thin_years("market_cap") == [2008]

    def test_the_span_and_symbol_count_come_out(self) -> None:
        frame = parse_valuation(
            _csv(
                "2008-06-02,13010,1,1,1,1,1,1,1,1,1",
                "2026-08-03,13020,1,1,1,1,1,1,1,1,1",
            )
        )
        found = census(frame)

        assert (found.first, found.last) == (dt.date(2008, 6, 2), dt.date(2026, 8, 3))
        assert found.symbols == 2

    def test_an_empty_frame_counts_nothing(self) -> None:
        assert census(parse_valuation(_csv())).rows_by_year == {}


class TestReadingTheArchive:
    def _archive(self, tmp_path: Path, keys: dict[str, bytes]) -> None:
        import datetime as dt

        from stock_ai.data.jquants_archive import archive
        from stock_ai.data.jquants_bulk import BulkFile

        archive(
            [BulkFile(key=key, last_modified="", size=len(body)) for key, body in keys.items()],
            lambda key: keys[key],
            tmp_path,
            on=dt.date(2026, 9, 15),
        )

    def test_the_same_day_in_two_files_is_counted_once(self, tmp_path: Path) -> None:
        # 月次の `historical` と日次の `live` は重なる。**残すと同じ銘柄日が
        # 2度数えられる。**
        body = gzip.compress(_csv(CONSISTENT))
        self._archive(
            tmp_path,
            {
                "equities/valuation/historical/2026/eq_valuation_202608.csv.gz": body,
                "equities/valuation/live/eq_valuation_20260803.csv.gz": body,
            },
        )

        assert len(from_archive(tmp_path)) == 1

    def test_nothing_archived_gives_an_empty_frame_with_columns(self, tmp_path: Path) -> None:
        frame = from_archive(tmp_path)
        assert frame.empty
        assert "market_cap" in frame.columns
