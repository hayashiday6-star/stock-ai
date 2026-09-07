"""保存した原本を読み口に通す、組み立ての1本通し。

**「落とせた」と「読めた」は別である。** このプロジェクトは2日で2回それを
踏んでいる。原本を 1 GB 保存したあとで読み口が繋がっていないと分かるのが、
いちばん高い。

`factor_panel` と同じ形でもある——部品を13個テストしていたのに `build_panel`
自体を一度も呼んでおらず、存在しない引数が本番まで出て行った。**ここでは
保存から数え上げまでを実際に通す。**
"""

from __future__ import annotations

import datetime as dt
import gzip
from pathlib import Path

from stock_ai.data.jquants_archive import archive
from stock_ai.data.jquants_bulk import BulkFile
from stock_ai.data.jquants_read import PARSERS, census, endpoint_of, read_archived

TODAY = dt.date(2026, 9, 15)

FIXTURES = Path(__file__).parent / "fixtures"


def _archive(tmp_path: Path, key: str, fixture: str) -> None:
    """実物の固定データを gzip して、保存の口を通して置く。"""
    payload = gzip.compress((FIXTURES / fixture).read_bytes())
    files = [BulkFile(key=key, last_modified="", size=len(payload))]
    archive(files, lambda _k: payload, tmp_path, on=TODAY)


class TestEndpointOf:
    """`key` からエンドポイントを決める。"""

    def test_a_two_segment_endpoint(self) -> None:
        key = "fins/summary/historical/2021/fins_summary_202109.csv.gz"

        assert endpoint_of(key) == "/fins/summary"

    def test_a_four_segment_endpoint(self) -> None:
        """**区切りの数で決めない。** 数えると片方が必ず外れる。"""
        key = "derivatives/bars/daily/futures/2021/x.csv.gz"

        assert endpoint_of(key) == "/derivatives/bars/daily/futures"

    def test_the_longest_match_wins(self) -> None:
        """`/indices/bars/daily` と `/indices/bars/daily/topix` は前方一致する。

        短いほうを採ると、TOPIX が指数一般として数えられる。
        """
        key = "indices/bars/daily/topix/2021/x.csv.gz"

        assert endpoint_of(key) == "/indices/bars/daily/topix"

    def test_a_leading_slash_does_not_change_the_answer(self) -> None:
        assert endpoint_of("/fins/summary/x.csv.gz") == "/fins/summary"

    def test_an_endpoint_name_alone_is_not_a_match(self) -> None:
        """`fins/summary` という名前のファイルは、そのエンドポイントの中身ではない。"""
        assert endpoint_of("fins/summary") is None

    def test_an_unknown_key_says_so(self) -> None:
        assert endpoint_of("something/else/x.csv.gz") is None


class TestReadArchived:
    """保存した原本を戻す。"""

    def test_a_gzip_file_comes_back_as_the_original_bytes(self, tmp_path) -> None:
        """**ここで戻せることが、展開せずに保存した意味である。**"""
        original = b"Code,Value\n86970,1\n"
        path = tmp_path / "a.csv.gz"
        path.write_bytes(gzip.compress(original))

        assert read_archived(path) == original

    def test_a_plain_file_is_read_as_it_is(self, tmp_path) -> None:
        path = tmp_path / "a.csv"
        path.write_bytes(b"Code\n1\n")

        assert read_archived(path) == b"Code\n1\n"


class TestCensus:
    """保存 → 展開 → 読み口 → 行数。**1本通す。**"""

    def test_a_saved_margin_alert_file_is_read_back_as_rows(self, tmp_path) -> None:
        _archive(tmp_path, "markets/margin-alert/2023/x.csv.gz", "jquants_margin_alert_sample.csv")

        reports = census(tmp_path)

        assert reports["/markets/margin-alert"].files == 1
        assert reports["/markets/margin-alert"].rows == 2
        assert not reports["/markets/margin-alert"].failed

    def test_a_cp932_file_survives_the_whole_round_trip(self, tmp_path) -> None:
        """**保存も展開も読み取りもバイト単位で通ること。**

        文字コードの取り違えは、途中のどこで起きても同じ見た目で失敗する。
        """
        _archive(tmp_path, "fins/details/2022/x.csv.gz", "jquants_details_sample.csv")

        reports = census(tmp_path)

        assert reports["/fins/details"].rows == 4

    def test_every_endpoint_with_a_parser_can_actually_be_read(self, tmp_path) -> None:
        """**読み口を書いただけで、通していない状態を作らない。**"""
        pairs = {
            "/markets/margin-alert": "jquants_margin_alert_sample.csv",
            "/markets/margin-interest": "jquants_margin_interest_sample.csv",
            "/markets/breakdown": "jquants_breakdown_sample.csv",
            "/markets/short-sale-report": "jquants_short_positions_sample.csv",
            "/fins/details": "jquants_details_sample.csv",
            "/fins/dividend": "jquants_dividend_sample.csv",
            "/fins/earnings-date": "jquants_earnings_date_sample.csv",
            "/markets/calendar": "jquants_calendar_sample.csv",
        }
        assert set(pairs) == set(PARSERS)  # 読み口を足したらここも足す

        for endpoint, fixture in pairs.items():
            _archive(tmp_path, f"{endpoint.lstrip('/')}/2022/x.csv.gz", fixture)

        reports = census(tmp_path)

        for endpoint in pairs:
            assert reports[endpoint].rows > 0, endpoint
            assert not reports[endpoint].failed, endpoint

    def test_an_endpoint_without_a_parser_is_counted_not_hidden(self, tmp_path) -> None:
        """**出力から消すと、保存できているのに読まれていないことが分からない。**"""
        _archive(tmp_path, "fins/summary/2021/x.csv.gz", "jquants_margin_alert_sample.csv")

        reports = census(tmp_path)

        assert reports["/fins/summary"].files == 1
        assert reports["/fins/summary"].rows == 0
        assert "読み口が無い" in next(iter(reports["/fins/summary"].failed.values()))

    def test_a_file_that_reads_but_holds_nothing_is_flagged(self, tmp_path) -> None:
        """**0行は例外を出さない。** 読めたことと中身があることは別である。"""
        payload = gzip.compress(b"PubDate,Code,AppDate,PubReason\n")
        key = "markets/margin-alert/2023/x.csv.gz"
        archive(
            [BulkFile(key=key, last_modified="", size=len(payload))],
            lambda _k: payload,
            tmp_path,
            on=TODAY,
        )

        reports = census(tmp_path)

        assert reports["/markets/margin-alert"].rows == 0
        assert reports["/markets/margin-alert"].empty == ["markets/margin-alert/2023/x.csv.gz"]

    def test_one_unreadable_file_does_not_stop_the_rest(self, tmp_path) -> None:
        broken = b"not gzip at all"
        archive(
            [BulkFile(key="fins/details/2022/bad.csv.gz", last_modified="", size=len(broken))],
            lambda _k: broken,
            tmp_path,
            on=TODAY,
        )
        _archive(tmp_path, "fins/details/2022/good.csv.gz", "jquants_details_sample.csv")

        report = census(tmp_path)["/fins/details"]

        assert report.rows == 4
        assert list(report.failed) == ["fins/details/2022/bad.csv.gz"]

    def test_an_empty_archive_is_quiet(self, tmp_path) -> None:
        assert census(tmp_path) == {}


class TestShapeOnRealisticFiles:
    """**配布サンプルで通ったことは、実物で通ったことにならない。**

    サンプルは1〜6行しかない。一括ファイルは月次で全銘柄が入っていて、
    gzip で、cp932 かもしれない。385本を保存したあとで読めないと分かるのが
    いちばん高い。
    """

    def _monthly(self, tmp_path, encoding: str = "cp932", days: int = 20, codes: int = 300):
        import csv
        import io

        rows = [
            {
                "Date": f"2024-03-{day:02d}",
                "Code": f"{code}0",
                "CoName": "日本取引所グループ",
                "S33": "7200",
                "Mkt": "0111",
                "Mrgn": "2",
                "MrgnNm": "貸借",
            }
            for day in range(1, days + 1)
            for code in range(1301, 1301 + codes)
        ]
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(rows[0]), lineterminator="\r\n")
        writer.writeheader()
        writer.writerows(rows)
        payload = gzip.compress(buf.getvalue().encode(encoding))
        key = "equities/master/historical/2024/equities_master_202403.csv.gz"
        archive(
            [BulkFile(key=key, last_modified="", size=len(payload))],
            lambda _k: payload,
            tmp_path,
            on=TODAY,
        )
        return key, len(rows)

    def test_a_monthly_all_symbol_file_reads_back(self, tmp_path) -> None:
        """6,000行・gzip・cp932。**サンプルの1行とは別物である。**"""
        from stock_ai.data.jquants_read import shape_of

        key, expected = self._monthly(tmp_path)

        found = shape_of(tmp_path, key)

        assert found is not None
        assert found.rows == expected
        assert found.encoding == "cp932"
        assert found.columns[:3] == ("Date", "Code", "CoName")

    def test_the_date_range_inside_the_file_is_reported(self, tmp_path) -> None:
        """**1本が1日ぶんか1ヶ月ぶんかで、20年ぶんの本数が20倍変わる。**

        月次なら240本、日次なら5,000本。契約日数の見積もりがそこで決まる。
        """
        from stock_ai.data.jquants_read import shape_of

        key, _ = self._monthly(tmp_path)

        found = shape_of(tmp_path, key)

        assert found is not None
        assert found.first_date == "2024-03-01"
        assert found.last_date == "2024-03-20"

    def test_a_utf8_file_is_reported_as_utf8(self, tmp_path) -> None:
        """**推測した結果を捨てない。** 化けても気付けるようにする。"""
        from stock_ai.data.jquants_read import shape_of

        key, _ = self._monthly(tmp_path, encoding="utf-8")

        found = shape_of(tmp_path, key)

        assert found is not None
        assert found.encoding == "utf-8-sig"

    def test_a_missing_file_says_so_rather_than_raising(self, tmp_path) -> None:
        from stock_ai.data.jquants_read import shape_of

        assert shape_of(tmp_path, "nothing/here.csv.gz") is None

    def test_one_key_is_chosen_for_each_endpoint(self, tmp_path) -> None:
        """**385本を全部開かない。** 形を見るだけなら1本で足りる。"""
        from stock_ai.data.jquants_read import one_per_endpoint

        payload = gzip.compress(b"Date,Code\n2024-01-04,13010\n")
        for month in ("202401", "202402", "202403"):
            key = f"equities/master/historical/2024/equities_master_{month}.csv.gz"
            archive(
                [BulkFile(key=key, last_modified="", size=len(payload))],
                lambda _k: payload,
                tmp_path,
                on=TODAY,
            )

        chosen = one_per_endpoint(tmp_path)

        assert list(chosen) == ["/equities/master"]
        assert chosen["/equities/master"].endswith("202403.csv.gz")  # いちばん新しい

    def test_a_file_with_no_rows_does_not_pretend_to_have_dates(self, tmp_path) -> None:
        from stock_ai.data.jquants_read import shape_of

        payload = gzip.compress(b"Date,Code\n")
        key = "equities/master/2024/x.csv.gz"
        archive(
            [BulkFile(key=key, last_modified="", size=len(payload))],
            lambda _k: payload,
            tmp_path,
            on=TODAY,
        )

        found = shape_of(tmp_path, key)

        assert found is not None
        assert found.rows == 0
        assert found.first_date == ""
