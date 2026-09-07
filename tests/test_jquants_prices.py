"""保存した一括四本値を DB に入れる。

**銘柄ごとに叩かない。** `BulkIngester` は1銘柄1リクエストで、この経路は
2回止まっている（84銘柄・3,700銘柄、どちらも 429）。20年 × 約4,400銘柄は
1週間に収まらない。

ここで押さえるのは、**例外を出さずに間違う形**である。いちばん重いのは
生値と調整値の取り違えで、このプロジェクトが繰り返し踏んでいる型である。
"""

from __future__ import annotations

import csv
import datetime as dt
import gzip
import io
from pathlib import Path

import pandas as pd

from stock_ai.data.jquants_archive import archive
from stock_ai.data.jquants_bulk import BulkFile
from stock_ai.data.jquants_prices import COLUMN_MAP, frames_from_payload, ingest
from stock_ai.data.schema import ADJ_CLOSE, CLOSE, HIGH, LOW, OPEN, VOLUME, split_adjusted

TODAY = dt.date(2026, 9, 7)

COLUMNS = [
    "Date",
    "Code",
    "O",
    "H",
    "L",
    "C",
    "UL",
    "LL",
    "Vo",
    "Va",
    "AdjFactor",
    "AdjO",
    "AdjH",
    "AdjL",
    "AdjC",
    "AdjVo",
    "MktCap",
]


def _row(date: str, code: str, close: float, adj_close: float | None = None) -> dict[str, str]:
    adjusted = close if adj_close is None else adj_close
    factor = adjusted / close if close else 1.0
    return {
        "Date": date,
        "Code": code,
        "O": f"{close - 1}",
        "H": f"{close + 2}",
        "L": f"{close - 3}",
        "C": f"{close}",
        "UL": "0",
        "LL": "0",
        "Vo": "1000",
        "Va": f"{close * 1000}",
        "AdjFactor": f"{factor}",
        "AdjO": f"{(close - 1) * factor}",
        "AdjH": f"{(close + 2) * factor}",
        "AdjL": f"{(close - 3) * factor}",
        "AdjC": f"{adjusted}",
        "AdjVo": "1000",
        "MktCap": "1000000",
    }


def _csv(rows: list[dict[str, str]]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


class TestRawAndAdjustedStayInTheirLanes:
    """**いちばん重い取り違えである。**

    `split_adjusted` は `adj_close / close` を分割比として全ての足に掛ける。
    `AdjO` を `open` に入れると比が意味を失い、**例外は出ないまま、すべての
    足がずれる。**
    """

    def test_close_is_the_traded_price_and_adj_close_is_the_adjusted_one(self) -> None:
        frames, _report = frames_from_payload(_csv([_row("2026-08-03", "13010", 200.0, 100.0)]))

        row = frames["1301"].iloc[0]

        assert row[CLOSE] == 200.0  # 実際に売買された値
        assert row[ADJ_CLOSE] == 100.0  # 調整後

    def test_open_high_low_are_the_raw_ones(self) -> None:
        """**`AdjO` を `open` に入れない。**"""
        frames, _report = frames_from_payload(_csv([_row("2026-08-03", "13010", 200.0, 100.0)]))

        row = frames["1301"].iloc[0]

        assert row[OPEN] == 199.0  # 生値。調整後なら 99.5 になる
        assert row[HIGH] == 202.0
        assert row[LOW] == 197.0

    def test_the_column_map_never_pairs_a_raw_column_with_an_adjusted_one(self) -> None:
        """調整後を取るのは `adj_close` だけである。"""
        adjusted = {source for source in COLUMN_MAP if source.startswith("Adj")}

        assert adjusted == {"AdjC"}
        assert COLUMN_MAP["AdjC"] == ADJ_CLOSE

    def test_a_split_reads_as_continuous_after_adjustment(self) -> None:
        """**分割日を挟んで、調整後の系列が跳ばないこと。**

        生の終値は半分になるが、調整後は連続する。`split_adjusted` を通した
        結果で確かめる——部品ではなく、組み立てで見る。
        """
        rows = [
            _row("2026-08-03", "13010", 200.0, 100.0),  # 分割前（調整後は半分）
            _row("2026-08-04", "13010", 100.0, 100.0),  # 分割後
        ]
        frames, _report = frames_from_payload(_csv(rows))

        raw = frames["1301"][CLOSE]
        adjusted = split_adjusted(frames["1301"])[CLOSE]

        assert raw.iloc[1] / raw.iloc[0] == 0.5  # 生値は跳ぶ
        assert adjusted.iloc[1] / adjusted.iloc[0] == 1.0  # 調整後は跳ばない


class TestFiltering:
    """名簿とは絞り込みが違う。"""

    def test_an_etf_is_kept_because_the_benchmark_is_one(self) -> None:
        """**投信・ETF を落とさない。**

        名簿は「どの会社が上場していたか」なので投信を除くが、株価は指数の
        代わりにも使う。このプロジェクトは `1306` をベンチマークにしている
        ——落とすと比較対象が消える。
        """
        frames, _report = frames_from_payload(_csv([_row("2026-08-03", "13060", 2000.0)]))

        assert "1306" in frames

    def test_a_share_class_code_is_dropped(self) -> None:
        """5桁の末尾が `0` でないものは普通株ではない。"""
        frames, report = frames_from_payload(_csv([_row("2026-08-03", "13015", 100.0)]))

        assert frames == {}
        assert report.skipped_code == 1

    def test_a_row_without_a_close_is_dropped_and_counted(self) -> None:
        """**`0` を入れない。**

        売買が無かった日と、値が欠けた日は別である。`0` にすると「値がゼロに
        なった日」として並び、指標も収益率も通ってしまう。
        """
        rows = [_row("2026-08-03", "13010", 100.0), _row("2026-08-04", "13010", 100.0)]
        rows[1]["C"] = ""
        frames, report = frames_from_payload(_csv(rows))

        assert len(frames["1301"]) == 1
        assert report.skipped_no_close == 1

    def test_a_zero_close_is_treated_as_missing(self) -> None:
        rows = [_row("2026-08-03", "13010", 100.0)]
        rows[0]["C"] = "0"
        frames, report = frames_from_payload(_csv(rows))

        assert frames == {}
        assert report.skipped_no_close == 1

    def test_a_missing_adjusted_close_falls_back_to_the_raw_one(self) -> None:
        """**`0` にしない。**

        `split_adjusted` は `adj_close / close` を掛けるので、0 は全ての足を
        0 にする。1倍（調整なし）のほうが、間違いとして軽い。
        """
        rows = [_row("2026-08-03", "13010", 100.0)]
        rows[0]["AdjC"] = ""
        frames, _report = frames_from_payload(_csv(rows))

        assert frames["1301"].iloc[0][ADJ_CLOSE] == 100.0


class TestFrameShape:
    """`upsert_prices` が受け取れる形であること。"""

    def test_the_index_is_dates_named_date_and_sorted(self) -> None:
        rows = [_row("2026-08-04", "13010", 101.0), _row("2026-08-03", "13010", 100.0)]
        frames, _report = frames_from_payload(_csv(rows))

        frame = frames["1301"]

        assert frame.index.name == "date"
        assert isinstance(frame.index, pd.DatetimeIndex)
        assert list(frame.index) == sorted(frame.index)

    def test_every_column_the_repository_writes_is_present(self) -> None:
        frames, _report = frames_from_payload(_csv([_row("2026-08-03", "13010", 100.0)]))

        assert set(frames["1301"].columns) == {OPEN, HIGH, LOW, CLOSE, ADJ_CLOSE, VOLUME}

    def test_one_file_holds_many_symbols_and_many_days(self) -> None:
        rows = [
            _row(f"2026-08-{day:02d}", code, 100.0) for day in (3, 4) for code in ("13010", "72030")
        ]
        frames, report = frames_from_payload(_csv(rows))

        assert sorted(frames) == ["1301", "7203"]
        assert len(frames["1301"]) == 2
        assert report.rows == 4


class TestIngest:
    """保存 → 読み出し → 書き込みを1本通す。"""

    def _archive(self, tmp_path: Path, rows: list[dict[str, str]], month: str = "202608") -> None:
        payload = gzip.compress(_csv(rows))
        key = f"equities/bars/daily/historical/2026/eq_bars_{month}.csv.gz"
        archive(
            [BulkFile(key=key, last_modified="", size=len(payload))],
            lambda _k: payload,
            tmp_path,
            on=TODAY,
        )

    def test_each_symbol_is_written_once_per_file(self, tmp_path) -> None:
        written: list[tuple[str, int]] = []
        self._archive(
            tmp_path,
            [
                _row("2026-08-03", "13010", 100.0),
                _row("2026-08-04", "13010", 101.0),
                _row("2026-08-03", "72030", 200.0),
            ],
        )

        report = ingest(tmp_path, lambda s, f: (written.append((s, len(f))), len(f))[1])

        assert sorted(written) == [("1301", 2), ("7203", 1)]
        assert report.written == 3
        assert report.files == 1

    def test_other_endpoints_are_left_alone(self, tmp_path) -> None:
        payload = gzip.compress(b"Date,Code,CoName\n2026-08-03,13010,x\n")
        archive(
            [BulkFile(key="equities/master/x.csv.gz", last_modified="", size=len(payload))],
            lambda _k: payload,
            tmp_path,
            on=TODAY,
        )

        report = ingest(tmp_path, lambda _s, _f: 0)

        assert report.files == 0

    def test_the_limit_lets_a_small_run_come_first(self, tmp_path) -> None:
        """**まず少数で試す。** この2日で2回、そこで問題が出ている。"""
        for month in ("202606", "202607", "202608"):
            self._archive(tmp_path, [_row("2026-08-03", "13010", 100.0)], month)

        report = ingest(tmp_path, lambda _s, _f: 1, limit=1)

        assert report.files == 1

    def test_one_unreadable_file_does_not_stop_the_rest(self, tmp_path) -> None:
        archive(
            [BulkFile(key="equities/bars/daily/bad.csv.gz", last_modified="", size=4)],
            lambda _k: b"nope",
            tmp_path,
            on=TODAY,
        )
        self._archive(tmp_path, [_row("2026-08-03", "13010", 100.0)])

        report = ingest(tmp_path, lambda _s, _f: 1)

        assert len(report.failed) == 1
        assert report.files == 1

    def test_an_empty_archive_is_quiet(self, tmp_path) -> None:
        report = ingest(tmp_path, lambda _s, _f: 1)

        assert report.files == 0
        assert report.summary()


class TestAgainstTheRealRepository:
    """**偽の呼び出しで通ったことは、本物で通ったことにならない。**

    `factor_panel` は部品を13個テストしていたのに `build_panel` 自体を一度も
    呼んでおらず、存在しない引数が本番まで出て行った。**ここでは本物の DB に
    書いて、読み直す。**
    """

    def _database(self):
        from stock_ai.database.engine import Database

        database = Database("sqlite:///:memory:")
        database.create_all()
        return database

    def test_the_frame_is_accepted_and_reads_back(self, tmp_path) -> None:
        from stock_ai.database.repository import PriceRepository

        payload = gzip.compress(
            _csv([_row("2026-08-03", "13010", 200.0, 100.0), _row("2026-08-04", "13010", 100.0)])
        )
        archive(
            [
                BulkFile(
                    key="equities/bars/daily/historical/2026/x.csv.gz",
                    last_modified="",
                    size=len(payload),
                )
            ],
            lambda _k: payload,
            tmp_path,
            on=TODAY,
        )
        database = self._database()

        with database.session() as session:
            repo = PriceRepository(session)
            report = ingest(tmp_path, lambda s, f: repo.upsert_prices(s, f, market="JP"))
            session.commit()

        assert report.written == 2

        with database.session() as session:
            raw = PriceRepository(session).get_raw_prices("1301")

        # **保存するのは生値である。** `get_prices` は読み出しのときに
        # `adj_close / close` を掛けるので、取り込み側で調整すると二重に
        # 掛かる。ここで見るのは書いたそのままの値。
        assert len(raw) == 2
        assert raw[CLOSE].iloc[0] == 200.0
        assert raw[ADJ_CLOSE].iloc[0] == 100.0

    def test_writing_twice_does_not_double_the_rows(self, tmp_path) -> None:
        """**何度実行しても同じ結果になること。** 途中で止めても安全に再開できる。"""
        from stock_ai.database.repository import PriceRepository

        payload = gzip.compress(_csv([_row("2026-08-03", "13010", 100.0)]))
        archive(
            [BulkFile(key="equities/bars/daily/x.csv.gz", last_modified="", size=len(payload))],
            lambda _k: payload,
            tmp_path,
            on=TODAY,
        )
        database = self._database()

        def run() -> None:
            with database.session() as session:
                repo = PriceRepository(session)
                ingest(tmp_path, lambda s, f: repo.upsert_prices(s, f, market="JP"))
                session.commit()

        run()
        run()

        with database.session() as session:
            stored = PriceRepository(session).get_prices("1301")

        assert len(stored) == 1

    def test_the_stored_series_survives_a_split_end_to_end(self, tmp_path) -> None:
        """保存 → 読み出し → 書き込み → 読み直し。**全部通す。**

        `get_prices` が読み出しのときに調整するので、**こちらで調整し直さない。**
        取り込み側が生値を入れているからこそ、ここで正しく効く。
        """
        from stock_ai.database.repository import PriceRepository

        payload = gzip.compress(
            _csv([_row("2026-08-03", "13010", 200.0, 100.0), _row("2026-08-04", "13010", 100.0)])
        )
        archive(
            [BulkFile(key="equities/bars/daily/x.csv.gz", last_modified="", size=len(payload))],
            lambda _k: payload,
            tmp_path,
            on=TODAY,
        )
        database = self._database()

        with database.session() as session:
            repo = PriceRepository(session)
            ingest(tmp_path, lambda s, f: repo.upsert_prices(s, f, market="JP"))
            session.commit()

        with database.session() as session:
            adjusted = PriceRepository(session).get_prices("1301")[CLOSE]
            raw = PriceRepository(session).get_raw_prices("1301")[CLOSE]

        assert raw.iloc[1] / raw.iloc[0] == 0.5  # 生値は跳ぶ
        assert adjusted.iloc[1] / adjusted.iloc[0] == 1.0  # 調整後は跳ばない


class TestExplainingTheDroppedRows:
    """**落とした行を「取引が無かった日」で説明できるか。**

    2026-09-07 の1本で 82,610行中 2,113行（2.6%）が終値なしだった。約4,100
    銘柄・20営業日なので、1日あたり約105銘柄。**もっともらしい数だが、
    もっともらしいだけでは足りない。**

    売買が無ければ終値も出来高も無い。**出来高だけある行があれば、それは
    説明が付かない**——読み方か、向こうの列の意味のどちらかが違う。
    """

    def test_a_row_with_no_trade_at_all_is_explained(self) -> None:
        rows = [_row("2026-08-03", "13010", 100.0)]
        rows[0]["C"] = ""
        rows[0]["Vo"] = "0"

        _frames, report = frames_from_payload(_csv(rows))

        assert report.skipped_no_close == 1
        assert report.no_close_but_traded == 0

    def test_a_row_with_volume_but_no_close_is_flagged(self) -> None:
        """**ここが 0 でなければ、落とした行を説明できない。**"""
        rows = [_row("2026-08-03", "13010", 100.0)]
        rows[0]["C"] = ""
        rows[0]["Vo"] = "5000"

        _frames, report = frames_from_payload(_csv(rows))

        assert report.skipped_no_close == 1
        assert report.no_close_but_traded == 1

    def test_the_unexplained_count_reaches_the_summary(self) -> None:
        """**数えても出さなければ、数えていないのと同じ。**"""
        rows = [_row("2026-08-03", "13010", 100.0)]
        rows[0]["C"] = ""
        rows[0]["Vo"] = "5000"

        _frames, report = frames_from_payload(_csv(rows))

        assert "出来高あり" in report.summary()

    def test_a_clean_run_does_not_mention_it(self) -> None:
        """**説明の付いた落とし方で、警告を出さない。** 毎回出ると読まれなくなる。"""
        _frames, report = frames_from_payload(_csv([_row("2026-08-03", "13010", 100.0)]))

        assert "出来高あり" not in report.summary()


class TestJoinReturns:
    """出所の違う株価が継ぎ目でぶつかっていないか。

    **DB には立花（2001年〜）と一括の原本（2021-09〜）が入りうる。** どちらも
    「最新の分割を基準にした調整後」を出しているはずだが、**はず**である。

    基準が違えば、継ぎ目の1日だけ分割比ぶんの収益率が立つ。**例外は出ない。**
    収益率の表も指標もそのまま通り、「その日に大きく動いた銘柄が沢山あった」
    にしか見えない。
    """

    def _frame(self, values: dict[str, float]) -> pd.DataFrame:
        index = pd.DatetimeIndex([pd.Timestamp(day) for day in values], name="date")
        return pd.DataFrame({CLOSE: list(values.values())}, index=index)

    def test_a_clean_join_looks_like_an_ordinary_day(self) -> None:
        from stock_ai.data.jquants_prices import join_returns

        frames = {"1301": self._frame({"2021-08-31": 100.0, "2021-09-01": 101.0})}

        (found,) = join_returns(frames.__getitem__, ["1301"], dt.date(2021, 9, 1))

        assert found[0] == "1301"
        assert abs(found[1]) < 0.05

    def test_a_basis_mismatch_shows_up_as_the_split_ratio(self) -> None:
        """**片方が分割調整済み、もう片方が別基準なら、その1日に比が立つ。**"""
        from stock_ai.data.jquants_prices import join_returns

        frames = {"1301": self._frame({"2021-08-31": 100.0, "2021-09-01": 50.0})}

        (found,) = join_returns(frames.__getitem__, ["1301"], dt.date(2021, 9, 1))

        assert found[1] == -0.5

    def test_a_symbol_with_data_on_only_one_side_is_left_out(self) -> None:
        """**継ぎ目をまたいでいない銘柄で、継ぎ目は測れない。**"""
        from stock_ai.data.jquants_prices import join_returns

        frames = {"1301": self._frame({"2021-09-01": 100.0, "2021-09-02": 101.0})}

        assert join_returns(frames.__getitem__, ["1301"], dt.date(2021, 9, 1)) == []

    def test_an_empty_series_is_skipped_rather_than_raising(self) -> None:
        from stock_ai.data.jquants_prices import join_returns

        assert join_returns(lambda _s: pd.DataFrame(), ["1301"], dt.date(2021, 9, 1)) == []
