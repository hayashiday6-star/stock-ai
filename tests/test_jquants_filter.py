"""絞り込みが、20年に伸ばしても持つか。

**`S33` が無ければ無条件に「会社」とみなす**、という設計になっている。符号の
名前が変わったときに universe が空になるより ETF が1つ紛れるほうが安い、と
いう判断で、そこは意図したものである。

ここで押さえるのは、**その判断が寄りかかっている前提のほうが、期間を伸ばすと
変わりうる**という点である。起きうる形は2つあり、向きが逆で、どちらも例外は
出ない。
"""

from __future__ import annotations

import csv
import datetime as dt
import gzip
import io
from pathlib import Path

from stock_ai.data.jquants_archive import archive
from stock_ai.data.jquants_bulk import BulkFile
from stock_ai.data.jquants_filter import (
    FilterCensus,
    baseline,
    census,
    census_payload,
    product_separates,
)
from stock_ai.data.universe import FUND, UNTRADABLE

TODAY = dt.date(2026, 9, 7)

COLUMNS = ["Date", "Code", "CoName", "Mkt", "MktNm", "S33", "S33Nm", "ProdCat"]


def _row(
    date: str,
    code: str,
    *,
    s33: str = "3200",
    s33_name: str = "化学",
    product: str = "011",
    market: str = "0111",
) -> dict[str, str]:
    return {
        "Date": date,
        "Code": code,
        "CoName": f"会社{code}",
        "Mkt": market,
        "MktNm": "プライム",
        "S33": s33,
        "S33Nm": s33_name,
        "ProdCat": product,
    }


def _csv(rows: list[dict[str, str]]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _count(rows: list[dict[str, str]]) -> FilterCensus:
    report = FilterCensus()
    census_payload(_csv(rows), report)
    return report


class TestTheTwoWaysTheFilterCanGoWrong:
    """**向きが逆の2つを、同じ数に混ぜない。**"""

    def test_a_missing_sector_is_counted_and_the_row_is_kept(self) -> None:
        """`S33` が空 → ETF・REIT が「会社」として入りうる側。"""
        report = _count([_row("2026-08-03", "13010", s33="")])

        assert report.by_year[2026].no_sector == 1
        assert report.by_year[2026].kept == 1  # **残っている**
        assert report.no_sector_symbols == {"1301"}

    def test_an_unknown_sector_code_is_counted_and_the_row_is_dropped(self) -> None:
        """表に無い符号 → 普通の会社が落ちる側。**こちらのほうが重い。**"""
        report = _count([_row("2026-08-03", "13010", s33="8888", s33_name="新しい区分")])

        assert report.by_year[2026].unknown_sector == 1
        assert report.by_year[2026].kept == 0  # **落ちている**
        assert report.by_year[2026].reasons[FUND] == 1

    def test_the_unknown_code_is_named_so_it_can_be_looked_at(self) -> None:
        """件数だけでは何が落ちたか分からない。**符号と名前を控える。**"""
        report = _count([_row("2026-08-03", "13010", s33="8888", s33_name="新しい区分")])

        assert report.unknown_sector_codes == {"8888": 1}
        assert report.unknown_sector_names["8888"] == "新しい区分"

    def test_the_other_bucket_is_not_an_unknown_code(self) -> None:
        """`9999`（その他）は**表にある。** 表に無い符号と同じ数に入れない。

        どちらも投信として落ちるが、**落ちてよいものと、落ちては困るものが
        混ざる。** 混ぜると、体系が変わった年を件数から見つけられない。
        """
        report = _count([_row("2026-08-03", "99990", s33="9999", s33_name="その他")])

        assert report.by_year[2026].unknown_sector == 0
        assert report.by_year[2026].reasons[FUND] == 1


class TestTheBaselineIsPerYear:
    """**年ごとに出す。** 全期間の割合1つでは、どの年から変わったかが言えない。"""

    def test_rows_are_split_by_year(self) -> None:
        report = _count(
            [
                _row("2021-09-01", "13010"),
                _row("2026-08-03", "13010"),
                _row("2026-08-04", "13010"),
            ]
        )

        assert report.by_year[2021].rows == 1
        assert report.by_year[2026].rows == 2

    def test_the_share_is_relative_to_that_year(self) -> None:
        report = _count(
            [
                _row("2021-09-01", "13010", s33=""),
                _row("2026-08-03", "13010"),
                _row("2026-08-04", "13020"),
            ]
        )

        assert report.by_year[2021].no_sector_share == 1.0
        assert report.by_year[2026].no_sector_share == 0.0

    def test_an_empty_year_does_not_divide_by_zero(self) -> None:
        assert FilterCensus().summary() == "名簿の原本が無い。"
        assert baseline(FilterCensus()) == "基準線なし。"

    def test_the_baseline_carries_the_span_and_the_day_it_was_taken(self) -> None:
        """**いつ測った基準線かが分からないと、比べたときに何も言えない。**"""
        report = _count([_row("2021-09-01", "13010"), _row("2026-08-03", "13010")])

        line = baseline(report)

        assert "2021〜2026" in line
        assert dt.date.today().isoformat() in line


class TestWhetherProductCategoryCanBackItUp:
    """**「たぶん使える」と書かないための検査である。**"""

    def test_disjoint_values_mean_it_can(self) -> None:
        report = _count(
            [
                _row("2026-08-03", "13010", product="011"),
                _row("2026-08-04", "99990", s33="9999", product="031"),
            ]
        )

        separates, overlap = product_separates(report, FUND)

        assert separates
        assert overlap == set()

    def test_one_shared_value_is_enough_to_rule_it_out(self) -> None:
        """重なった値の行は、**どちらとも言えない。** 1つでもあれば受け皿に不可。"""
        report = _count(
            [
                _row("2026-08-03", "13010", product="011"),
                _row("2026-08-04", "99990", s33="9999", product="011"),
            ]
        )

        separates, overlap = product_separates(report, FUND)

        assert not separates
        assert overlap == {"011"}

    def test_nothing_dropped_is_not_the_same_as_separated(self) -> None:
        """**比べていない、であって分けられた、ではない。**"""
        report = _count([_row("2026-08-03", "13010")])

        separates, overlap = product_separates(report, FUND)

        assert not separates
        assert overlap == set()

    def test_each_reason_keeps_its_own_distribution(self) -> None:
        report = _count(
            [
                _row("2026-08-03", "99990", s33="9999", product="031"),
                _row("2026-08-03", "20000", market="0105", product="011"),
            ]
        )

        assert set(report.product_dropped[FUND]) == {"031"}
        assert set(report.product_dropped[UNTRADABLE]) == {"011"}


class TestTheWholeRunEndToEnd:
    """部品だけでなく、**組み立てを1本通す。**"""

    def _archive(self, tmp_path: Path, rows: list[dict[str, str]]) -> None:
        payload = gzip.compress(_csv(rows))
        archive(
            [
                BulkFile(
                    key="equities/master/historical/2026/eq_master_202608.csv.gz",
                    last_modified="",
                    size=len(payload),
                )
            ],
            lambda _k: payload,
            tmp_path,
            on=TODAY,
        )

    def test_it_reads_the_archived_masters(self, tmp_path: Path) -> None:
        self._archive(tmp_path, [_row("2026-08-03", "13010"), _row("2026-08-04", "13010", s33="")])

        report = census(tmp_path)

        assert report.files == 1
        assert report.rows == 2
        assert report.by_year[2026].no_sector == 1

    def test_no_master_at_all_says_so_without_raising(self, tmp_path: Path) -> None:
        report = census(tmp_path)

        assert report.by_year == {}
        assert report.span is None

    def test_other_endpoints_are_left_alone(self, tmp_path: Path) -> None:
        payload = gzip.compress(b"Date,Code,C\n2026-08-03,13010,100\n")
        archive(
            [BulkFile(key="equities/bars/daily/x.csv.gz", last_modified="", size=len(payload))],
            lambda _k: payload,
            tmp_path,
            on=TODAY,
        )

        assert census(tmp_path).rows == 0


class TestTheOfficialTableDecidesWhatCountsAsKnown:
    """**公式に載っている符号は、業種でなくても「知っている」側である。**

    `9999`（その他）は公式の 33業種表に34件目として載っている。業種ではないが
    正式な符号で、投信・ETF がそれを名乗る。**落とす扱いは変えない。** ただ、
    「落としてよいもの」と「落ちては困るもの」を同じ数に混ぜない。

    混ぜると、**符号の体系が変わった年を件数から見つけられなくなる**——
    `9999` が数百件あるところに、未知の符号が数件混じっても見えない。
    """

    def test_the_official_other_code_is_known(self) -> None:
        from stock_ai.data.sectors import TSE33_OTHER, known_tse33

        assert known_tse33(TSE33_OTHER)

    def test_it_is_still_not_a_sector(self) -> None:
        """**「公式に載っている」と「業種である」を分ける。**"""
        from stock_ai.data.sectors import TSE33_OTHER, Sector, from_tse33

        assert from_tse33(TSE33_OTHER) is Sector.OTHER

    def test_a_code_outside_the_official_table_is_not_known(self) -> None:
        from stock_ai.data.sectors import known_tse33

        assert not known_tse33("8888")

    def test_a_real_industry_code_is_known(self) -> None:
        from stock_ai.data.sectors import known_tse33

        assert known_tse33("3200")  # 化学

    def test_nothing_at_all_is_not_known(self) -> None:
        from stock_ai.data.sectors import known_tse33

        assert not known_tse33(None)
        assert not known_tse33("")

    def test_the_official_other_does_not_inflate_the_unknown_count(self) -> None:
        """**ここが本番である。** 投信の `9999` が未知の符号を埋めてしまわない。"""
        report = _count(
            [_row("2026-08-03", f"{9000 + index}0", s33="9999") for index in range(50)]
            + [_row("2026-08-03", "13010", s33="8888", s33_name="新しい区分")]
        )

        assert report.by_year[2026].unknown_sector == 1
        assert report.unknown_sector_codes == {"8888": 1}
