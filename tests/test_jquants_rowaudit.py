"""原本から読んだ行が、本当にデータベースに入ったか（項目4）。

**データベースは行ごとの出所を持っていない。** 5年ぶんのときは窓がまるごと
J-Quants の覆う期間だったので困らなかったが、20年に伸ばしたら立花の 2001年
以降と重なった。差が「入らなかった行」なのか「立花の行」なのか、**区別する
手立てが無い。**

立花のマスタは現存銘柄しか返さない。**廃止銘柄だけを見れば、両側が同じものを
数えたことになる。**
"""

from __future__ import annotations

import datetime as dt
import gzip
from pathlib import Path

from stock_ai.data.jquants_rowaudit import (
    audit,
    count_in_archive,
    count_in_database,
    delisted_symbols,
)

HEADER = "Date,Code,O,H,L,C,V"


def _archive(tmp_path: Path, rows: list[str]) -> None:
    from stock_ai.data.jquants_archive import archive
    from stock_ai.data.jquants_bulk import BulkFile

    payload = gzip.compress(("\n".join([HEADER, *rows]) + "\n").encode("utf-8"))
    archive(
        [
            BulkFile(
                key="equities/bars/daily/historical/2024/eq_bars_202406.csv.gz",
                last_modified="",
                size=len(payload),
            )
        ],
        lambda _k: payload,
        tmp_path,
        on=dt.date(2026, 9, 15),
    )


def _bar(day: str, code: str, close: float = 100.0) -> str:
    return f"{day},{code},100,110,90,{close},1000"


class TestDecidingWhoCountsAsDelisted:
    """**DB との差では決めない。** DB は上場一覧ではない。"""

    def test_a_symbol_missing_from_the_newest_roster_is_delisted(self) -> None:
        snapshots = {
            dt.date(2024, 1, 1): {"1301", "1302"},
            dt.date(2024, 6, 1): {"1301"},
        }
        assert delisted_symbols(snapshots) == {"1302"}

    def test_a_symbol_that_came_back_is_not_delisted(self) -> None:
        snapshots = {
            dt.date(2024, 1, 1): {"1301"},
            dt.date(2024, 3, 1): set(),
            dt.date(2024, 6, 1): {"1301"},
        }
        assert delisted_symbols(snapshots) == set()

    def test_one_roster_cannot_decide_anything(self) -> None:
        """**「0件」と「比べていない」を混ぜない。**"""
        assert delisted_symbols({dt.date(2024, 6, 1): {"1301"}}) == set()

    def test_no_rosters_at_all(self) -> None:
        assert delisted_symbols({}) == set()


class TestCountingTheArchiveTheSameWayTheIngestDoes:
    def test_only_the_asked_for_symbols_are_counted(self, tmp_path: Path) -> None:
        _archive(tmp_path, [_bar("2024-06-03", "13010"), _bar("2024-06-03", "13020")])

        rows, files, _dates = count_in_archive(tmp_path, {"1301"})

        assert (rows, files) == (1, 1)

    def test_a_row_without_a_close_is_not_counted(self, tmp_path: Path) -> None:
        """**取り込みも入れていない。** 数えると片側だけ多くなる。"""
        _archive(tmp_path, ["2024-06-03,13010,100,110,90,,1000"])

        assert count_in_archive(tmp_path, {"1301"})[0] == 0

    def test_a_zero_close_is_not_counted_either(self, tmp_path: Path) -> None:
        _archive(tmp_path, [_bar("2024-06-03", "13010", close=0.0)])

        assert count_in_archive(tmp_path, {"1301"})[0] == 0

    def test_the_same_symbol_day_twice_is_counted_once(self, tmp_path: Path) -> None:
        """月次と日次が重なる。**残すと二重に数える。**"""
        _archive(tmp_path, [_bar("2024-06-03", "13010"), _bar("2024-06-03", "13010")])

        assert count_in_archive(tmp_path, {"1301"})[0] == 1

    def test_rows_outside_the_window_are_left_out(self, tmp_path: Path) -> None:
        _archive(tmp_path, [_bar("2024-06-03", "13010"), _bar("2025-06-03", "13010")])

        window = (dt.date(2024, 1, 1), dt.date(2024, 12, 31))

        assert count_in_archive(tmp_path, {"1301"}, window)[0] == 1

    def test_the_dates_that_were_seen_come_back(self, tmp_path: Path) -> None:
        _archive(tmp_path, [_bar("2024-06-03", "13010"), _bar("2024-06-04", "13010")])

        assert count_in_archive(tmp_path, {"1301"})[2] == {
            dt.date(2024, 6, 3),
            dt.date(2024, 6, 4),
        }


class TestCountingBothSidesTheSameWay:
    def _database(self, tmp_path: Path, bars: dict[str, list[dt.date]]):
        from stock_ai.database.engine import Database
        from stock_ai.database.models import PriceBar, Security

        database = Database(f"sqlite:///{tmp_path / 'test.db'}")
        database.create_all()
        with database.session() as session:
            for symbol, days in bars.items():
                security = Security(symbol=symbol, market="JP", name=symbol)
                session.add(security)
                session.flush()
                for day in days:
                    session.add(
                        PriceBar(
                            security_id=security.id,
                            date=day,
                            open=100,
                            high=110,
                            low=90,
                            close=100,
                            adj_close=100,
                            volume=1000,
                        )
                    )
            session.commit()
        return database

    def test_matching_counts_close_the_item(self, tmp_path: Path) -> None:
        _archive(tmp_path / "arch", [_bar("2024-06-03", "13020")])
        database = self._database(tmp_path, {"1302": [dt.date(2024, 6, 3)]})
        snapshots = {
            dt.date(2024, 1, 1): {"1301", "1302"},
            dt.date(2024, 6, 1): {"1301"},
        }

        found = audit(database, tmp_path / "arch", snapshots)

        assert (found.archive_rows, found.database_rows) == (1, 1)
        assert found.difference == 0

    def test_a_row_that_never_landed_shows_as_a_shortfall(self, tmp_path: Path) -> None:
        """**読めたのに入っていない。** ここが項目4 の本来の的である。"""
        _archive(
            tmp_path / "arch",
            [_bar("2024-06-03", "13020"), _bar("2024-06-04", "13020")],
        )
        database = self._database(tmp_path, {"1302": [dt.date(2024, 6, 3)]})
        snapshots = {
            dt.date(2024, 1, 1): {"1301", "1302"},
            dt.date(2024, 6, 1): {"1301"},
        }

        assert audit(database, tmp_path / "arch", snapshots).difference == -1

    def test_a_still_listed_symbol_is_left_out_of_both_sides(self, tmp_path: Path) -> None:
        """**立花が供給しうる銘柄を入れない。** それが 20年で崩れた理由である。"""
        _archive(tmp_path / "arch", [_bar("2024-06-03", "13010")])
        database = self._database(tmp_path, {"1301": [dt.date(2024, 6, 3), dt.date(2024, 6, 4)]})
        snapshots = {
            dt.date(2024, 1, 1): {"1301", "1302"},
            dt.date(2024, 6, 1): {"1301"},
        }

        found = audit(database, tmp_path / "arch", snapshots)

        # 1301 は現存なので両側とも数えない。1302 は原本にも DB にも無い。
        assert (found.archive_rows, found.database_rows) == (0, 0)

    def test_nothing_delisted_says_so_rather_than_reporting_zero_difference(
        self, tmp_path: Path
    ) -> None:
        _archive(tmp_path / "arch", [_bar("2024-06-03", "13010")])
        database = self._database(tmp_path, {"1301": [dt.date(2024, 6, 3)]})
        snapshots = {dt.date(2024, 6, 1): {"1301"}}

        found = audit(database, tmp_path / "arch", snapshots)

        assert found.symbols == 0
        assert "比べていない" in found.summary()

    def test_the_database_side_is_looked_up_by_symbol(self, tmp_path: Path) -> None:
        database = self._database(tmp_path, {"1302": [dt.date(2024, 6, 3)]})

        assert count_in_database(database, {"1302"}) == 1
        assert count_in_database(database, {"1301"}) == 0
        assert count_in_database(database, set()) == 0
