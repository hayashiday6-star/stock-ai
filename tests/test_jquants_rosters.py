"""保存した一括名簿から、営業日ごとの名簿を取り出す。

**一括ファイル1本の中に、その月の全営業日ぶんが入っている。** 2026-08 の
1本が 88,870 行で、4,441銘柄 × 20営業日である（2026-09-07 に実測）。

いまディスクにある66枚は JSON API を30日刻みで叩いたもので、**同じ5年ぶんが
一括には約1,220枚（全営業日）入っている。** 20年なら約5,000枚。しかも API を
1回も叩かずに取り出せる。
"""

from __future__ import annotations

import csv
import datetime as dt
import gzip
import io
from pathlib import Path

from stock_ai.data.delisted import read_snapshot
from stock_ai.data.jquants_archive import archive
from stock_ai.data.jquants_bulk import BulkFile
from stock_ai.data.jquants_rosters import (
    DAILY_SNAPSHOT_DIR,
    extract,
    rosters_from_payload,
)

TODAY = dt.date(2026, 9, 7)

COLUMNS = [
    # 公式の `EQ_MASTER_COLUMNS_V2` と同じ並び。
    "Date",
    "Code",
    "CoName",
    "CoNameEn",
    "S17",
    "S17Nm",
    "S33",
    "S33Nm",
    "ScaleCat",
    "Mkt",
    "MktNm",
    "Mrgn",
    "MrgnNm",
    "ProdCat",
]


def _row(date: str, code: str, *, s33: str = "7200", name: str | None = None) -> dict[str, str]:
    return {
        "Date": date,
        "Code": code,
        "CoName": name or f"会社{code}",
        "CoNameEn": "X",
        "S17": "16",
        "S17Nm": "金融（除く銀行）",
        "S33": s33,
        "S33Nm": "その他金融業",
        "ScaleCat": "-",
        "Mkt": "0111",
        "MktNm": "プライム",
        "Mrgn": "2",
        "MrgnNm": "貸借",
        "ProdCat": "011",
    }


def _csv(rows: list[dict[str, str]]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _archive(tmp_path: Path, rows: list[dict[str, str]], month: str = "202608") -> None:
    payload = gzip.compress(_csv(rows))
    key = f"equities/master/historical/2026/eq_master_{month}.csv.gz"
    archive(
        [BulkFile(key=key, last_modified="", size=len(payload))],
        lambda _k: payload,
        tmp_path,
        on=TODAY,
    )


class TestSplittingByDate:
    """**1本を1枚の名簿として扱わない。**"""

    def test_one_file_becomes_one_roster_per_trading_day(self) -> None:
        payload = _csv(
            [_row("2026-08-03", "13010"), _row("2026-08-04", "13010"), _row("2026-08-05", "13010")]
        )

        rosters, rows, undated = rosters_from_payload(payload)

        assert sorted(rosters) == [dt.date(2026, 8, day) for day in (3, 4, 5)]
        assert rows == 3
        assert undated == 0

    def test_a_listing_that_starts_mid_month_is_absent_earlier(self) -> None:
        """**ここが要点である。**

        月ぶんをまとめて1枚にすると、月の途中で上場した銘柄が月初から居た
        ことになる。「和集合だと、まだ上場していない銘柄を過去の分位に入れて
        しまう」と既に記録がある——**同じ間違いが、月の中でも起きる。**
        """
        payload = _csv(
            [
                _row("2026-08-03", "13010"),
                _row("2026-08-04", "13010"),
                _row("2026-08-04", "72030"),  # 4日から
            ]
        )

        rosters, _rows, _undated = rosters_from_payload(payload)

        assert {p.symbol for p in rosters[dt.date(2026, 8, 3)]} == {"1301"}
        assert {p.symbol for p in rosters[dt.date(2026, 8, 4)]} == {"1301", "7203"}

    def test_a_delisting_shows_up_on_the_day_not_the_month(self) -> None:
        """30日刻みでは「その月のどこか」しか分からない。"""
        payload = _csv(
            [
                _row("2026-08-03", "13010"),
                _row("2026-08-03", "72030"),
                _row("2026-08-04", "13010"),  # 7203 が消えた
            ]
        )

        rosters, _rows, _undated = rosters_from_payload(payload)

        gone = {p.symbol for p in rosters[dt.date(2026, 8, 3)]} - {
            p.symbol for p in rosters[dt.date(2026, 8, 4)]
        }
        assert gone == {"7203"}

    def test_rows_without_a_date_are_counted_not_guessed(self) -> None:
        """**0 でないなら、列名が変わった疑いがある。** 黙って捨てない。"""
        payload = _csv([_row("", "13010"), _row("2026-08-03", "13010")])

        rosters, rows, undated = rosters_from_payload(payload)

        assert undated == 1
        assert rows == 2
        assert len(rosters) == 1


class TestTheFilterIsNotDuplicated:
    """**絞り込みの規則を2つ持たない。** JSON 経路と同じ関数を通す。"""

    def test_a_fund_is_dropped_the_same_way_as_on_the_json_path(self) -> None:
        """ETF・REIT は業種コードで落ちる。ここで自前の規則を書かない。"""
        payload = _csv([_row("2026-08-03", "13010"), _row("2026-08-03", "13060", s33="9999")])

        rosters, _rows, _undated = rosters_from_payload(payload)

        assert {p.symbol for p in rosters[dt.date(2026, 8, 3)]} == {"1301"}

    def test_a_share_class_code_is_dropped(self) -> None:
        """5桁の末尾が `0` でないものは普通株ではない。"""
        payload = _csv([_row("2026-08-03", "13010"), _row("2026-08-03", "13015")])

        rosters, _rows, _undated = rosters_from_payload(payload)

        assert {p.symbol for p in rosters[dt.date(2026, 8, 3)]} == {"1301"}

    def test_the_lending_class_survives(self) -> None:
        """貸借区分は空売りできるかを決める。**落とさない。**"""
        payload = _csv([_row("2026-08-03", "13010")])

        rosters, _rows, _undated = rosters_from_payload(payload)

        assert rosters[dt.date(2026, 8, 3)][0].lending == "貸借"


class TestExtract:
    """保存 → 取り出し → 書き出しを1本通す。"""

    def test_the_default_directory_is_not_the_api_one(self) -> None:
        """**混ぜない。**

        JSON 経路の名簿と一括の名簿を同じ場所に置くと、絞り込みが食い違った
        とき、境目をまたいだ差が「消えてもいない銘柄が消えた」になる。
        """
        assert DAILY_SNAPSHOT_DIR.name == "universe_daily"
        assert DAILY_SNAPSHOT_DIR.name != "universe_snapshots"

    def test_a_saved_month_becomes_daily_rosters_on_disk(self, tmp_path) -> None:
        out = tmp_path / "out"
        _archive(tmp_path, [_row(f"2026-08-{day:02d}", "13010") for day in (3, 4, 5)])

        report = extract(tmp_path, out)

        assert sorted(path.stem for path in out.glob("*.csv")) == [
            "2026-08-03",
            "2026-08-04",
            "2026-08-05",
        ]
        assert report.files == 1
        assert len(report.written) == 3

    def test_what_is_written_reads_back_through_the_existing_reader(self, tmp_path) -> None:
        """**形式を1つしか持たない。** 既存の読み口でそのまま読めること。"""
        out = tmp_path / "out"
        _archive(tmp_path, [_row("2026-08-03", "13010")])

        extract(tmp_path, out)

        (profile,) = read_snapshot(out / "2026-08-03.csv")
        assert profile.symbol == "1301"
        assert profile.lending == "貸借"

    def test_running_again_does_not_rewrite(self, tmp_path) -> None:
        """途中で止めても安全に再開できる。"""
        out = tmp_path / "out"
        _archive(tmp_path, [_row("2026-08-03", "13010")])

        extract(tmp_path, out)
        report = extract(tmp_path, out)

        assert report.written == []
        assert report.skipped == [dt.date(2026, 8, 3)]

    def test_refetch_writes_again(self, tmp_path) -> None:
        out = tmp_path / "out"
        _archive(tmp_path, [_row("2026-08-03", "13010")])

        extract(tmp_path, out)
        report = extract(tmp_path, out, refetch=True)

        assert report.written == [dt.date(2026, 8, 3)]

    def test_a_day_where_nothing_survives_the_filter_is_not_written(self, tmp_path) -> None:
        """**空の名簿を書かない。**

        書くと、その日に全銘柄が上場廃止したように見える。例外は出ない。
        """
        out = tmp_path / "out"
        _archive(tmp_path, [_row("2026-08-03", "13060", s33="9999")])

        report = extract(tmp_path, out)

        assert report.empty == [dt.date(2026, 8, 3)]
        assert not list(out.glob("*.csv"))

    def test_other_endpoints_in_the_archive_are_left_alone(self, tmp_path) -> None:
        """名簿以外の原本を読みに行かない。"""
        out = tmp_path / "out"
        payload = gzip.compress(b"Date,Code,O\n2026-08-03,13010,100\n")
        archive(
            [BulkFile(key="equities/bars/daily/x.csv.gz", last_modified="", size=len(payload))],
            lambda _k: payload,
            tmp_path,
            on=TODAY,
        )

        report = extract(tmp_path, out)

        assert report.files == 0
        assert not list(out.glob("*.csv"))

    def test_an_unreadable_file_does_not_stop_the_rest(self, tmp_path) -> None:
        out = tmp_path / "out"
        archive(
            [BulkFile(key="equities/master/bad.csv.gz", last_modified="", size=4)],
            lambda _k: b"nope",
            tmp_path,
            on=TODAY,
        )
        _archive(tmp_path, [_row("2026-08-03", "13010")])

        report = extract(tmp_path, out)

        assert len(report.failed) == 1
        assert report.written == [dt.date(2026, 8, 3)]

    def test_an_empty_archive_is_quiet(self, tmp_path) -> None:
        report = extract(tmp_path, tmp_path / "out")

        assert report.files == 0
        assert report.summary()


class TestCompare:
    """2つの経路で作った名簿を突き合わせる。

    **これをやらずに新しい経路へ乗り換えない。** 片方だけを見ているかぎり、
    絞り込みの食い違いは「銘柄数がちょっと違う」としか見えず、それは毎日
    変わる値なので区別が付かない。
    """

    def _write(self, directory: Path, date: str, rows: list[dict[str, str]]) -> None:
        from stock_ai.data.delisted import write_snapshot
        from stock_ai.data.universe import Segment, normalize_listings

        write_snapshot(
            directory, dt.date.fromisoformat(date), normalize_listings(rows, Segment.ALL)
        )

    def test_identical_rosters_come_back_as_the_same(self, tmp_path) -> None:
        from stock_ai.data.jquants_rosters import compare

        rows = [_row("2026-08-03", "13010"), _row("2026-08-03", "72030")]
        for name in ("a", "b"):
            self._write(tmp_path / name, "2026-08-03", rows)

        report = compare(tmp_path / "a", tmp_path / "b")

        assert report.same == [dt.date(2026, 8, 3)]
        assert not report.differing

    def test_a_symbol_in_only_one_of_them_is_reported(self, tmp_path) -> None:
        from stock_ai.data.jquants_rosters import compare

        self._write(tmp_path / "a", "2026-08-03", [_row("2026-08-03", "13010")])
        self._write(
            tmp_path / "b",
            "2026-08-03",
            [_row("2026-08-03", "13010"), _row("2026-08-03", "72030")],
        )

        report = compare(tmp_path / "a", tmp_path / "b")

        assert report.differing == {dt.date(2026, 8, 3): (0, 1)}
        assert report.examples[dt.date(2026, 8, 3)][1] == ["7203"]

    def test_only_the_overlapping_dates_are_compared(self, tmp_path) -> None:
        """**片方にしか無い日付を「食い違い」にしない。**

        30日刻みと営業日ごとでは、重ならない日付のほうが圧倒的に多い。それを
        差として数えると、全部が食い違いになる。
        """
        from stock_ai.data.jquants_rosters import compare

        self._write(tmp_path / "a", "2026-08-03", [_row("2026-08-03", "13010")])
        self._write(tmp_path / "b", "2026-08-03", [_row("2026-08-03", "13010")])
        self._write(tmp_path / "b", "2026-08-04", [_row("2026-08-04", "13010")])

        report = compare(tmp_path / "a", tmp_path / "b")

        assert report.common == [dt.date(2026, 8, 3)]
        assert report.same == [dt.date(2026, 8, 3)]

    def test_the_lending_class_is_compared_too(self, tmp_path) -> None:
        """**銘柄が同じでも、区分が違えば別の結論が出る。**

        貸借区分は空売りできるかを決める。銘柄集合だけ見ていると通ってしまう。
        """
        from stock_ai.data.jquants_rosters import compare

        left = _row("2026-08-03", "13010")
        right = dict(left, MrgnNm="信用", Mrgn="1")
        self._write(tmp_path / "a", "2026-08-03", [left])
        self._write(tmp_path / "b", "2026-08-03", [right])

        report = compare(tmp_path / "a", tmp_path / "b")

        assert report.lending_differs == {dt.date(2026, 8, 3): 1}
        assert report.same == [dt.date(2026, 8, 3)]  # 銘柄は同じ

    def test_no_overlap_says_so_rather_than_claiming_agreement(self, tmp_path) -> None:
        """**重なりが無いことを「一致」と読ませない。**"""
        from stock_ai.data.jquants_rosters import compare

        self._write(tmp_path / "a", "2026-08-03", [_row("2026-08-03", "13010")])
        self._write(tmp_path / "b", "2026-08-04", [_row("2026-08-04", "13010")])

        report = compare(tmp_path / "a", tmp_path / "b")

        assert report.common == []
        assert "突き合わせられない" in report.summary()


class TestExplainMissing:
    """重ならなかった日付を、取引カレンダーに当てる。

    **「たぶん休日だろう」で済ませない。** 30日刻みの日付は休日にも当たり、
    一括には立会日しか無いので重ならない——それは欠けではない。だが立会日
    なのに名簿が無い日が混じっていたら、それは本当の欠けである。**件数では
    区別が付かない。**
    """

    def test_a_holiday_is_not_a_gap(self) -> None:
        from stock_ai.data.jquants_rosters import explain_missing

        trading = {dt.date(2026, 8, 3)}

        holidays, gaps = explain_missing([dt.date(2026, 8, 2)], trading)

        assert holidays == [dt.date(2026, 8, 2)]
        assert gaps == []

    def test_a_trading_day_with_no_roster_is_a_gap(self) -> None:
        """**ここが本当の欠けである。**"""
        from stock_ai.data.jquants_rosters import explain_missing

        trading = {dt.date(2026, 8, 3)}

        holidays, gaps = explain_missing([dt.date(2026, 8, 3)], trading)

        assert holidays == []
        assert gaps == [dt.date(2026, 8, 3)]

    def test_no_calendar_means_nothing_is_explained(self) -> None:
        """**カレンダーが無いことを「全部休日」と読ませない。**

        空集合を返すと「1日も立会が無い」になり、全部が休日として説明された
        ことになってしまう。`None` と空集合を分ける。
        """
        from stock_ai.data.jquants_rosters import explain_missing

        holidays, gaps = explain_missing([dt.date(2026, 8, 3)], None)

        assert holidays == []
        assert gaps == [dt.date(2026, 8, 3)]

    def test_the_calendar_comes_from_the_archive(self, tmp_path) -> None:
        from stock_ai.data.jquants_rosters import trading_days_from_archive

        payload = gzip.compress(b"Date,HolDiv\n2026-08-03,1\n2026-08-02,0\n2026-08-04,2\n")
        archive(
            [BulkFile(key="markets/calendar/calendar.csv.gz", last_modified="", size=len(payload))],
            lambda _k: payload,
            tmp_path,
            on=TODAY,
        )

        found = trading_days_from_archive(tmp_path)

        assert found == {dt.date(2026, 8, 3), dt.date(2026, 8, 4)}  # 半日立会も入る

    def test_no_calendar_in_the_archive_says_none(self, tmp_path) -> None:
        from stock_ai.data.jquants_rosters import trading_days_from_archive

        assert trading_days_from_archive(tmp_path) is None


class TestUntradableMarkets:
    """**買えない銘柄を universe に入れない。**

    2026-09-08 の実測。名簿に出て株価が1本も無い16銘柄は、**全部が TOKYO PRO
    Market** だった。四本値の行はあるのに、**終値が1つも無い**——5年ぶんで
    1,126行あって0件という銘柄もある。売買が成立していない。

    説#1 を閉じた理由がまさにこれだった——「現象は見つかったが、**自分が
    買える銘柄では起きていなかった**」。同じ間違いを universe の側で繰り返さ
    ない。
    """

    def test_a_tokyo_pro_listing_is_left_out(self) -> None:
        from stock_ai.data.universe import Segment, normalize_listings

        rows = [_row("2026-08-03", "13010"), dict(_row("2026-08-03", "72030"), Mkt="0105")]

        profiles = normalize_listings(rows, Segment.ALL)

        assert {p.symbol for p in profiles} == {"1301"}

    def test_the_market_name_is_used_when_the_code_is_missing(self) -> None:
        from stock_ai.data.universe import Segment, normalize_listings

        row = dict(_row("2026-08-03", "72030"), Mkt="", MktNm="TOKYO PRO MARKET")

        assert normalize_listings([row], Segment.ALL) == []

    def test_the_code_wins_over_the_name(self) -> None:
        """**名前で先に見ると、符号と名前が食い違う行を名前のほうで救う。**"""
        from stock_ai.data.universe import Segment, normalize_listings

        row = dict(_row("2026-08-03", "72030"), Mkt="0111", MktNm="TOKYO PRO MARKET")

        assert {p.symbol for p in normalize_listings([row], Segment.ALL)} == {"7203"}

    def test_an_ordinary_market_is_untouched(self) -> None:
        from stock_ai.data.universe import Segment, normalize_listings

        for code in ("0111", "0112", "0113", "0101", "0104"):
            row = dict(_row("2026-08-03", "72030"), Mkt=code)
            assert normalize_listings([row], Segment.ALL), code

    def test_a_record_with_no_market_at_all_is_kept(self) -> None:
        """**分からないものを落とさない。** 落とすと universe が黙って縮む。"""
        from stock_ai.data.universe import Segment, normalize_listings

        row = dict(_row("2026-08-03", "72030"), Mkt="", MktNm="")

        assert {p.symbol for p in normalize_listings([row], Segment.ALL)} == {"7203"}

    def test_the_excluded_code_is_the_official_one(self) -> None:
        """出典: 公式の `market_codes`（`0105` = TOKYO PRO MARKET）。"""
        from stock_ai.data.universe import EXCLUDED_MARKETS

        assert set(EXCLUDED_MARKETS) == {"0105"}


class TestExplainingTheGap:
    """食い違いを「たぶん◯◯だろう」で閉じない。

    **市場は移る。** TOKYO PRO Market に上場してから、数年後にスタンダードや
    グロースへ変わる銘柄がある。

    最初は「最後に見えた姿」を1つだけ持っていた。**それだと、当時 TOKYO PRO
    だった銘柄が「スタンダード」と出る。** 実際にそうなり、説明の付く食い違い
    6件を「説明が付かない」と読んだ（2026-09-08）。

    **その日の値と最新の値を取り違える**——このプロジェクトが繰り返し踏んで
    いる型である。例外は出ない。もっともらしい市場名が出るだけである。
    """

    def test_the_market_is_kept_per_date(self, tmp_path) -> None:
        from stock_ai.data.jquants_rosters import markets_on

        _archive(
            tmp_path,
            [dict(_row("2021-09-06", "72030"), MktNm="TOKYO PRO MARKET")],
            month="202109",
        )
        _archive(tmp_path, [dict(_row("2024-03-21", "72030"), MktNm="グロース")], month="202403")

        found = markets_on(tmp_path, {"7203"})

        assert found["7203"][dt.date(2021, 9, 6)] == "TOKYO PRO MARKET"
        assert found["7203"][dt.date(2024, 3, 21)] == "グロース"

    def test_a_symbol_that_moved_markets_reads_correctly_on_the_old_date(self) -> None:
        """**ここで実際に間違えた。**

        いまはグロースでも、2021年には TOKYO PRO だった。最後の姿で引くと、
        買えない市場を外したことによる食い違いが「説明が付かない」になる。
        """
        from stock_ai.data.jquants_rosters import market_on

        history = {
            dt.date(2021, 9, 6): "TOKYO PRO MARKET",
            dt.date(2024, 3, 21): "グロース",
        }

        assert market_on(history, dt.date(2021, 9, 6)) == "TOKYO PRO MARKET"
        assert market_on(history, dt.date(2026, 9, 7)) == "グロース"

    def test_a_date_with_no_roster_falls_back_to_the_one_before(self) -> None:
        """**名簿は立会日にしかない。**

        30日刻みの日付は休日にも当たる。その日ちょうどを探して見つからない
        ことを「市場が分からない」と読むと、説明の付くものが付かなくなる。
        """
        from stock_ai.data.jquants_rosters import market_on

        history = {dt.date(2021, 9, 3): "TOKYO PRO MARKET"}

        assert market_on(history, dt.date(2021, 9, 6)) == "TOKYO PRO MARKET"

    def test_a_date_before_the_first_roster_is_unknown(self) -> None:
        """**前が無ければ分からない。** 後ろの値で埋めない。"""
        from stock_ai.data.jquants_rosters import market_on

        history = {dt.date(2024, 3, 21): "グロース"}

        assert market_on(history, dt.date(2021, 9, 6)) is None

    def test_no_history_at_all_is_unknown(self) -> None:
        from stock_ai.data.jquants_rosters import market_on

        assert market_on({}, dt.date(2021, 9, 6)) is None

    def test_a_symbol_not_in_the_master_gets_an_empty_history(self, tmp_path) -> None:
        """**「市場不明」を勝手に埋めない。**"""
        from stock_ai.data.jquants_rosters import markets_on

        _archive(tmp_path, [_row("2026-08-03", "13010")])

        assert markets_on(tmp_path, {"7203"}) == {"7203": {}}

    def test_nothing_archived_gives_empty_histories(self, tmp_path) -> None:
        from stock_ai.data.jquants_rosters import markets_on

        assert markets_on(tmp_path, {"7203"}) == {"7203": {}}


class TestReadingTheCalendarWholeRatherThanFiltered:
    """区分ごとに数えたいなら、**立会日だけに畳む前**を読む必要がある。

    `trading_days_from_archive` は集合を返すので、`1` と `2` のどちらだったか
    が消える。**消えたあとで「半日立会は何日あったか」は答えられない。**
    """

    def _calendar(self, tmp_path: Path, body: bytes, month: str = "202612") -> None:
        payload = gzip.compress(body)
        key = f"markets/calendar/historical/2026/calendar_{month}.csv.gz"
        archive(
            [BulkFile(key=key, last_modified="", size=len(payload))],
            lambda _k: payload,
            tmp_path,
            on=TODAY,
        )

    def test_the_divisions_survive(self, tmp_path) -> None:
        from stock_ai.data.jquants_rosters import calendar_from_archive

        self._calendar(tmp_path, b"Date,HolDiv\n2009-12-30,2\n2009-12-31,0\n")

        days = calendar_from_archive(tmp_path)

        assert days is not None
        assert [day.division for day in days] == ["2", "0"]

    def test_a_date_in_two_files_is_counted_once(self, tmp_path) -> None:
        """**同じ日が複数の原本に出うる。** 畳まないと、重なる期間だけ増える。"""
        from stock_ai.data.jquants_rosters import calendar_from_archive

        self._calendar(tmp_path, b"Date,HolDiv\n2026-12-30,1\n", month="202612")
        self._calendar(tmp_path, b"Date,HolDiv\n2026-12-30,1\n", month="202701")

        days = calendar_from_archive(tmp_path)

        assert days is not None
        assert len(days) == 1

    def test_no_calendar_at_all_is_none_not_empty(self, tmp_path) -> None:
        """**空リストと区別する。** 空だと「1日も立会が無い」と読めてしまう。"""
        from stock_ai.data.jquants_rosters import calendar_from_archive

        assert calendar_from_archive(tmp_path) is None

    def test_the_trading_day_helper_still_works_through_it(self, tmp_path) -> None:
        from stock_ai.data.jquants_rosters import trading_days_from_archive

        self._calendar(tmp_path, b"Date,HolDiv\n2009-12-30,2\n2009-12-31,0\n")

        assert trading_days_from_archive(tmp_path) == {dt.date(2009, 12, 30)}
