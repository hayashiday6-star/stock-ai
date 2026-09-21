"""原本の列そのものを数える。

ここで押さえるのは4つ。

1. **読み口を通さない。** 読み口が捨てている列は、読み口からは見えない
2. **列が在ることと、値が埋まっていることは別。** 候補6 の `IV` で踏んだ
3. **年は、どの列で数えたかを言う。** 見つからなければ年で数えない
4. **ファイルごとに形が違いうる。** 1ファイルで確かめて終わりにしない
"""

from __future__ import annotations

import csv
import gzip
import io

import pytest

from stock_ai.data.jquants_archive import MANIFEST, MANIFEST_COLUMNS
from stock_ai.data.jquants_columns import DATE_COLUMNS, column_census

ENDPOINT = "/equities/valuation"


def _body(rows: list[dict[str, str]]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def _archive(tmp_path, files: dict[str, list[dict[str, str]]]):
    entries = []
    for stem, rows in files.items():
        key = f"equities/valuation/{stem}.csv.gz"
        target = tmp_path / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(gzip.compress(_body(rows).encode("utf-8")))
        entries.append(f"/{key},1,1,x,,2026-09-21")
    (tmp_path / MANIFEST).write_text(
        ",".join(MANIFEST_COLUMNS) + "\n" + "\n".join(entries) + "\n", encoding="utf-8"
    )
    return tmp_path


def _row(date: str, **changed) -> dict[str, str]:
    base = {"Date": date, "Code": "13060", "EPS": "10", "MktCap": "100", "DivYield": ""}
    return {**base, **changed}


class TestItCountsTheOriginalsOwnColumns:
    def test_it_finds_every_column(self, tmp_path) -> None:
        """**読み口が採らない列も出る。** そこに答えが在るかもしれない。"""
        found = column_census(_archive(tmp_path, {"a": [_row("2016-01-04")]}), ENDPOINT)

        assert [column.name for column in found.columns] == [
            "Date",
            "Code",
            "EPS",
            "MktCap",
            "DivYield",
        ]
        assert found.rows == 1
        assert found.files == 1

    def test_the_order_is_the_originals(self, tmp_path) -> None:
        """**原本の順のまま。** 並べ替えると、原本と突き合わせにくい。"""
        found = column_census(_archive(tmp_path, {"a": [_row("2016-01-04")]}), ENDPOINT)

        assert found.columns[0].name == "Date"
        assert found.columns[-1].name == "DivYield"

    def test_another_endpoint_is_not_mixed_in(self, tmp_path) -> None:
        found = column_census(_archive(tmp_path, {"a": [_row("2016-01-04")]}), "/fins/summary")

        assert found.rows == 0
        assert any("1行も読めなかった" in line for line in found.warnings())


class TestBeingThereIsNotBeingFilled:
    """**候補6 の `IV` で踏んだ形。** 列は在るのに、全行で空だった。"""

    def test_an_always_empty_column_is_named(self, tmp_path) -> None:
        found = column_census(
            _archive(tmp_path, {"a": [_row("2016-01-04"), _row("2016-01-05")]}), ENDPOINT
        )

        assert found.empty_columns == ("DivYield",)
        assert any("1行も埋まっていない" in line for line in found.warnings())

    def test_a_filled_column_is_not_named(self, tmp_path) -> None:
        """**両向きに置く。** 全部を空に倒しても緑にならないように。"""
        found = column_census(
            _archive(tmp_path, {"a": [_row("2016-01-04", DivYield="2.1")]}), ENDPOINT
        )

        assert not found.empty_columns
        assert found.find("DivYield").filled == 1

    def test_the_share_is_out_of_all_rows(self, tmp_path) -> None:
        """**件数ではなく割合を見る**（`CLAUDE.md`）。"""
        rows = [_row("2016-01-04", DivYield="2.1"), _row("2016-01-05"), _row("2016-01-06")]

        found = column_census(_archive(tmp_path, {"a": rows}), ENDPOINT)

        assert found.find("DivYield").share(found.rows) == pytest.approx(1 / 3)

    def test_whitespace_is_not_filled(self, tmp_path) -> None:
        blank = _archive(tmp_path, {"a": [_row("2016-01-04", DivYield="   ")]})

        found = column_census(blank, ENDPOINT)

        assert found.find("DivYield").filled == 0

    def test_a_missing_column_is_not_found(self, tmp_path) -> None:
        """**在らないことも答えである。** 候補3 はこれで決まる。"""
        found = column_census(_archive(tmp_path, {"a": [_row("2016-01-04")]}), ENDPOINT)

        assert found.find("DivYield") is not None
        assert found.find("配当利回り") is None


class TestItSaysFromWhenTheValuesAreThere:
    """**`IV` は 2016-07 から。** それはここにしか出ない。"""

    def test_it_reports_the_first_and_last_year(self, tmp_path) -> None:
        rows = [
            _row("2008-05-01"),
            _row("2016-07-19", DivYield="2.1"),
            _row("2026-01-05", DivYield="2.3"),
        ]

        found = column_census(_archive(tmp_path, {"a": rows}), ENDPOINT)

        assert found.find("DivYield").first_year == 2016
        assert found.find("DivYield").last_year == 2026
        # **列そのものは 2008 から在る。** 在ることと埋まることは別。
        assert found.find("Code").first_year == 2008

    def test_it_counts_the_rows_by_year(self, tmp_path) -> None:
        rows = [_row("2008-05-01"), _row("2016-07-19"), _row("2016-07-20")]

        found = column_census(_archive(tmp_path, {"a": rows}), ENDPOINT)

        assert found.years == {2008: 1, 2016: 2}

    def test_it_says_which_column_gave_the_year(self, tmp_path) -> None:
        found = column_census(_archive(tmp_path, {"a": [_row("2016-01-04")]}), ENDPOINT)

        assert found.dated_by == "Date"

    def test_without_a_date_column_it_does_not_guess(self, tmp_path) -> None:
        """**黙って全部を1年に押し込めない。**"""
        rows = [{"Code": "13060", "EPS": "10"}]

        found = column_census(_archive(tmp_path, {"a": rows}), ENDPOINT)

        assert found.dated_by is None
        assert not found.years
        assert any("年で数えていない" in line for line in found.warnings())

    def test_the_date_columns_are_tried_in_order(self) -> None:
        """**「いつ時点か」を、「いつ知れたか」より先に当てる。**"""
        assert DATE_COLUMNS.index("Date") < DATE_COLUMNS.index("PubDate")
        assert DATE_COLUMNS.index("ExDate") < DATE_COLUMNS.index("DisclosedDate")


class TestTheShapeCanChangeBetweenFiles:
    """**1ファイルで確かめて終わりにしない。** `identity_check` と同じ形。"""

    def test_a_column_only_some_files_have_is_named(self, tmp_path) -> None:
        old = [{"Date": "2008-05-01", "Code": "13060"}]
        new = [{"Date": "2016-07-19", "Code": "13060", "DivYield": "2.1"}]

        found = column_census(_archive(tmp_path, {"a": old, "b": new}), ENDPOINT)

        assert found.files == 2
        assert found.find("DivYield").files == 1
        assert found.find("Code").files == 2
        assert any("一部のファイルにしか無い" in line for line in found.warnings())

    def test_a_uniform_shape_says_nothing(self, tmp_path) -> None:
        """**常に点く旗は、何も区別しない。**"""
        found = column_census(
            _archive(tmp_path, {"a": [_row("2008-05-01")], "b": [_row("2016-07-19")]}), ENDPOINT
        )

        assert not any("一部のファイルにしか無い" in line for line in found.warnings())


class TestItCarriesRowsToLookAt:
    """**表の数だけでは、単位の取り違えは見つからない**（`MktCap` の百万円）。"""

    def test_it_returns_a_few_rows(self, tmp_path) -> None:
        rows = [_row(f"2016-01-{day:02d}") for day in range(4, 12)]

        found = column_census(_archive(tmp_path, {"a": rows}), ENDPOINT, sample_rows=3)

        assert len(found.sample) == 3
        assert found.sample[0]["MktCap"] == "100"

    def test_it_does_not_hold_the_whole_original(self, tmp_path) -> None:
        rows = [_row(f"2016-01-{day:02d}") for day in range(4, 12)]

        found = column_census(_archive(tmp_path, {"a": rows}), ENDPOINT)

        assert len(found.sample) < found.rows


class TestTheOutputStaysSmall:
    """**貼られる前提で作る**（`CLAUDE.md`「出力を小さく保つ」）。

    実データで `/fins/summary` が **111 列**だった（2026-09-21）。全部刷ると
    4つのエンドポイントで 450 行を超え、**ユーザーが先頭だけ貼ることに
    なった**——見たかった配当の列は1つも出ていない。

    **`MAX_SKIPPED_PER_REASON` と同じ形である**——件数の多いものが、
    見たいものを押し出す。
    """

    @staticmethod
    def _printed(**kwargs) -> str:
        import io

        from rich.console import Console

        from stock_ai import cli

        buffer = io.StringIO()
        original = cli.console
        cli.console = Console(file=buffer, width=100, force_terminal=False)
        try:
            cli.column_census_command(**kwargs)
        finally:
            cli.console = original
        return buffer.getvalue()

    @staticmethod
    def _wide(tmp_path):
        """**111 列の盤面。** 実データがそうだった。"""
        row = {"Date": "2016-01-04", "Code": "13060"}
        row.update({f"Col{index}": str(index) for index in range(60)})
        row.update({f"FDiv{index}": str(index) for index in range(49)})
        return _archive(tmp_path, {"a": [row]})

    def test_the_defaults_do_not_print_every_column(self, tmp_path) -> None:
        """**貼られたときの長さで見る。** 列を数えるのでは足りない。

        最初この assert は `printed.count("Col")` で書いてあった。**標本が
        同じ列をもう一度出すので、上限が効いていても2倍に数える**——
        `CLAUDE.md`「テストの名前が主張していることと、assert が守って
        いることを突き合わせる」。**痛いのは行数のほうである。**
        """
        from stock_ai.cli import MAX_COLUMNS_SHOWN

        printed = self._printed(
            endpoint=ENDPOINT,
            directory=str(self._wide(tmp_path)),
            show_empty=False,
            match="",
            show_all=False,
        )

        # 表は1列1行。**111 列なら 111 行**——枠と標本を足しても、上限の
        # 2倍には届かないこと。
        assert len(printed.splitlines()) < MAX_COLUMNS_SHOWN * 2
        assert "出していない" in printed, "**切ったことを言っていない。**"

    def test_without_the_cap_it_would_be_long(self, tmp_path) -> None:
        """**この検査が落ちる条件を、実際に1つ作る。**"""
        from stock_ai.cli import MAX_COLUMNS_SHOWN

        printed = self._printed(
            endpoint=ENDPOINT,
            directory=str(self._wide(tmp_path)),
            show_empty=False,
            match="",
            show_all=True,
        )

        assert len(printed.splitlines()) > MAX_COLUMNS_SHOWN * 2

    def test_a_spelling_narrows_it(self, tmp_path) -> None:
        printed = self._printed(
            endpoint=ENDPOINT,
            directory=str(self._wide(tmp_path)),
            show_empty=False,
            match="fdiv",
            show_all=False,
        )

        assert "FDiv0" in printed
        assert "Col0 " not in printed, "**絞ったのに関係ない列が出ている。**"

    def test_a_spelling_that_matches_nothing_says_so(self, tmp_path) -> None:
        """**無いことと、絞り込みで消えたことは別である。**"""
        printed = self._printed(
            endpoint=ENDPOINT,
            directory=str(self._wide(tmp_path)),
            show_empty=False,
            match="そんな列は無い",
            show_all=False,
        )

        assert "1つも無い" in printed
        assert "--all" in printed, "**全部出す方法を言っていない。**"

    def test_all_prints_the_lot(self, tmp_path) -> None:
        """**両向きに置く。** 常に切る形でも緑にならないように。"""
        printed = self._printed(
            endpoint=ENDPOINT,
            directory=str(self._wide(tmp_path)),
            show_empty=False,
            match="",
            show_all=True,
        )

        assert "出していない" not in printed
        assert "Col59" in printed

    def test_the_title_says_how_many_columns_there_are(self, tmp_path) -> None:
        """**切った後の数だけ出すと、原本の広さが見えなくなる。**"""
        printed = self._printed(
            endpoint=ENDPOINT,
            directory=str(self._wide(tmp_path)),
            show_empty=False,
            match="",
            show_all=False,
        )

        assert "111 列" in printed


class TestTheDefaultsNameWhatTheyLookFor:
    """**探している綴りを、コマンドの中に埋めない。** 表に出す。"""

    def test_the_wide_endpoint_has_a_default_filter(self) -> None:
        from stock_ai.cli import NEXT_MATERIALS

        found = {name: patterns for name, _why, patterns in NEXT_MATERIALS}

        assert found["/fins/summary"], "**111 列の口に、絞り込みが無い。**"

    def test_the_narrow_endpoints_do_not(self) -> None:
        """**列の少ない口を絞ると、在るものが見えなくなる。**"""
        from stock_ai.cli import NEXT_MATERIALS

        found = {name: patterns for name, _why, patterns in NEXT_MATERIALS}

        assert not found["/markets/short-ratio"]
        assert not found["/equities/valuation"]
