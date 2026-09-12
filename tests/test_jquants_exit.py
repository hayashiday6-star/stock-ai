"""解約前の棚卸しが、作り直せないものを取りこぼさずに数えるか。

この棚卸しの誤りは1方向にしか出ない。**「あることになっている」**である。
無いものを無いと言うのは安全側だが、あると言って実は無いと、解約後に
気付くことになる。
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from stock_ai.data.jquants_exit import CANCELLATION, audit
from stock_ai.data.schema import ADJ_CLOSE, CLOSE, HIGH, LOW, OPEN, VOLUME
from stock_ai.data.types import FinancialReport
from stock_ai.database.engine import Database
from stock_ai.database.repository import FinancialStatementRepository, PriceRepository


def _database() -> Database:
    database = Database("sqlite:///:memory:")
    database.create_all()
    return database


def _frame(bars: int = 5):
    index = pd.bdate_range("2024-01-01", periods=bars, name="date")
    return pd.DataFrame(
        {OPEN: 100.0, HIGH: 100.0, LOW: 100.0, CLOSE: 100.0, ADJ_CLOSE: 100.0, VOLUME: 1_000.0},
        index=index,
    )


def test_an_empty_database_reports_nothing_rather_than_failing() -> None:
    coverage = audit(_database())
    assert coverage.symbols_with_prices == 0
    assert coverage.statements == 0
    assert coverage.price_first is None


def test_prices_are_counted_by_symbol_and_span() -> None:
    database = _database()
    with database.session() as session:
        PriceRepository(session).upsert_prices("7203", _frame(), market="JP")
        PriceRepository(session).upsert_prices("6758", _frame(), market="JP")

    coverage = audit(database)

    assert coverage.symbols_with_prices == 2
    assert coverage.price_first == dt.date(2024, 1, 1)
    assert coverage.price_last == dt.date(2024, 1, 5)


def test_us_symbols_do_not_inflate_the_japanese_count() -> None:
    """解約で困るのは日本株だけ。米株を混ぜると「足りている」に見える。"""
    database = _database()
    with database.session() as session:
        PriceRepository(session).upsert_prices("7203", _frame(), market="JP")
        PriceRepository(session).upsert_prices("AAPL", _frame(), market="US")

    assert audit(database).symbols_with_prices == 1


def test_a_forecast_in_any_one_column_counts_as_present() -> None:
    """**4列のどれか1つでも入っていれば「ある」。**

    純利益だけ埋まって売上が空、という取れ方を実際にする。1列だけ数えると
    過小に出て、取り直しが要ると誤解する。
    """
    database = _database()
    with database.session() as session:
        FinancialStatementRepository(session).upsert_reports(
            "7203",
            [
                FinancialReport(
                    symbol="7203",
                    fiscal_year=2024,
                    disclosed_on=dt.date(2024, 5, 10),
                    forecast_net_income=1.0,
                ),
                FinancialReport(symbol="7203", fiscal_year=2023, disclosed_on=dt.date(2023, 5, 10)),
            ],
            market="JP",
        )

    coverage = audit(database)

    assert coverage.statements == 2
    assert coverage.with_forecast == 1
    assert coverage.with_disclosed_at == 0  # 時刻は入れていない


def test_disclosure_times_are_counted_separately_from_dates() -> None:
    """日付だけでは場中と引け後を分けられない。時刻の有無を別に数える。"""
    database = _database()
    with database.session() as session:
        FinancialStatementRepository(session).upsert_reports(
            "7203",
            [
                FinancialReport(
                    symbol="7203",
                    fiscal_year=2024,
                    disclosed_on=dt.date(2024, 5, 10),
                    disclosed_at=dt.time(15, 0),
                )
            ],
            market="JP",
        )

    assert audit(database).with_disclosed_at == 1


def test_the_roster_gap_is_what_survivorship_bias_still_costs() -> None:
    """名簿にあって株価が無い銘柄が、直っていないぶんである。"""
    database = _database()
    with database.session() as session:
        PriceRepository(session).upsert_prices("7203", _frame(), market="JP")
    snapshots = {
        dt.date(2024, 1, 1): {"7203", "1352"},
        dt.date(2024, 2, 1): {"7203"},  # 1352 は消えた
    }

    coverage = audit(database, snapshots)

    assert coverage.snapshots == 2
    assert coverage.roster_symbols == 2
    assert coverage.roster_without_prices == 1  # 1352 の株価がまだ無い


def test_a_symbol_row_without_bars_does_not_count_as_having_prices() -> None:
    """**銘柄が登録されていることと、株価があることは別。**

    delisted-harvest は先に銘柄を作ってから株価を取る。途中で止まると
    「銘柄はあるが株価は無い」状態になり、そこを取り違えると取り込み済みに
    見えてしまう。
    """
    database = _database()
    with database.session() as session:
        PriceRepository(session).upsert_prices("7203", _frame(), market="JP")
        FinancialStatementRepository(session).upsert_reports(
            "1352",
            [FinancialReport(symbol="1352", fiscal_year=2022)],
            market="JP",
        )

    coverage = audit(database, {dt.date(2024, 1, 1): {"7203", "1352"}})

    assert coverage.securities == 2
    assert coverage.symbols_with_prices == 1
    assert coverage.roster_without_prices == 1


def test_days_left_counts_down_to_the_cancellation() -> None:
    coverage = audit(_database())
    assert coverage.days_left(CANCELLATION - dt.timedelta(days=19)) == 19
    assert coverage.days_left(CANCELLATION + dt.timedelta(days=1)) == -1


class TestNamingWhatIsMissing:
    """**件数だけでは追えない。**

    「名簿にあって株価が無い16銘柄」は、一括で株価を入れる前も後も 16 のまま
    だった。+438銘柄ぶん増えたのに、その16件だけ動かない。**数字が動かない
    ことは分かっても、なぜ動かないかは分からない。**

    名前が並べば、全部が同じ性質か（同じ日に廃止した、同じ市場、同じ桁数）が
    一目で分かる。
    """

    def test_the_missing_symbols_are_listed_not_just_counted(self, tmp_path) -> None:
        database = Database("sqlite:///:memory:")
        database.create_all()
        frame = pd.DataFrame(
            {
                "open": [1.0],
                "high": [1.0],
                "low": [1.0],
                "close": [1.0],
                "adj_close": [1.0],
                "volume": [1],
            },
            index=pd.DatetimeIndex([pd.Timestamp("2024-01-04")], name="date"),
        )
        with database.session() as session:
            PriceRepository(session).upsert_prices("1301", frame, market="JP")
            session.commit()

        coverage = audit(database, snapshots={dt.date(2024, 1, 4): {"1301", "7203", "6758"}})

        assert coverage.roster_without_prices == 2
        assert coverage.missing_priced == ("6758", "7203")

    def test_nothing_missing_gives_an_empty_list_not_a_placeholder(self, tmp_path) -> None:
        """**空を「まだ数えていない」と読ませない。**"""
        database = Database("sqlite:///:memory:")
        database.create_all()

        coverage = audit(database, snapshots={})

        assert coverage.missing_priced == ()
        assert coverage.roster_without_prices == 0


class TestCountingRowsNotJustSymbols:
    """**銘柄数だけでは、書けたことにならない。**

    一括取り込みは「4,996,413行を書いた」と言うが、それは `upsert` に渡した
    数である。**渡したことと入ったことは別で、入らなくても例外は出ない。**

    DB には立花の2001年以降も入っているので、全体の行数と取り込みの報告は
    そもそも一致しない。**期間を切って初めて比べられる。**
    """

    def _database(self):
        database = Database("sqlite:///:memory:")
        database.create_all()
        return database

    def _frame(self, days: list[str]) -> pd.DataFrame:
        return pd.DataFrame(
            {
                OPEN: [1.0] * len(days),
                HIGH: [1.0] * len(days),
                LOW: [1.0] * len(days),
                CLOSE: [1.0] * len(days),
                ADJ_CLOSE: [1.0] * len(days),
                VOLUME: [1] * len(days),
            },
            index=pd.DatetimeIndex([pd.Timestamp(day) for day in days], name="date"),
        )

    def test_the_rows_are_counted_not_only_the_symbols(self) -> None:
        database = self._database()
        with database.session() as session:
            PriceRepository(session).upsert_prices(
                "1301", self._frame(["2024-01-04", "2024-01-05"]), market="JP"
            )
            session.commit()

        coverage = audit(database)

        assert coverage.symbols_with_prices == 1
        assert coverage.price_rows == 2

    def test_the_window_is_counted_separately(self) -> None:
        """**期間を切らないと、取り込みの報告と比べられない。**"""
        database = self._database()
        with database.session() as session:
            PriceRepository(session).upsert_prices(
                "1301",
                self._frame(["2001-01-04", "2024-01-04", "2024-01-05"]),
                market="JP",
            )
            session.commit()

        coverage = audit(database, window=(dt.date(2024, 1, 1), dt.date(2024, 12, 31)))

        assert coverage.price_rows == 3  # 立花の古い1行を含む
        assert coverage.price_rows_in_window == 2  # 原本の期間だけ

    def test_no_window_means_no_windowed_count(self) -> None:
        """**0 を「窓の中に1行も無い」と読ませない。** 渡していないだけである。"""
        database = self._database()
        with database.session() as session:
            PriceRepository(session).upsert_prices("1301", self._frame(["2024-01-04"]), market="JP")
            session.commit()

        coverage = audit(database)

        assert coverage.price_rows == 1
        assert coverage.price_rows_in_window == 0
