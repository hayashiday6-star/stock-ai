"""名簿に在って価格が無い銘柄（`stock_ai.data.price_coverage`）。

**穴は、黙って観測を消す。** 陰性対照で、引いた 800,000 件のうち
**16,677 件（2.1%）が「価格が1本も無い銘柄」に当たっていた**（2026-09-18）。

引いているのは `list_securities` が返す銘柄で、**説の側の候補もそこから
出る。** そこに足が1本も無ければ、**そのイベントは判定に入らないまま
消える**——そして消えたことは、リターンの側からは見えない。
"""

from __future__ import annotations

import pandas as pd
import pytest

from stock_ai.data.price_coverage import THIN_BARS, Coverage, survey
from stock_ai.data.schema import ADJ_CLOSE, CLOSE, HIGH, LOW, OPEN, VOLUME
from stock_ai.database.engine import Database
from stock_ai.database.repository import PriceRepository, get_or_create_security

_INDEX = pd.bdate_range("2020-01-06", periods=40, name="date")


def _database(bars: dict[str, int], *, listed_only: tuple[str, ...] = ()) -> Database:
    """``bars`` 本ずつ入れた保存先。``listed_only`` は名簿だけ作って価格を入れない。"""
    database = Database("sqlite:///:memory:")
    database.create_all()
    with database.session() as session:
        repo = PriceRepository(session)
        for symbol, count in bars.items():
            close = [100.0] * count
            repo.upsert_prices(
                symbol,
                pd.DataFrame(
                    {
                        OPEN: close,
                        HIGH: close,
                        LOW: close,
                        CLOSE: close,
                        ADJ_CLOSE: close,
                        VOLUME: [1_000_000.0] * count,
                    },
                    index=_INDEX[:count],
                ),
                market="JP",
            )
        for symbol in listed_only:
            get_or_create_security(session, symbol, market="JP", name=f"{symbol} のなまえ")
        session.commit()
    return database


class TestAHoleIsCountedAsAHole:
    def test_a_symbol_with_no_bars_at_all_is_found(self) -> None:
        found = survey(_database({"1301": 40}, listed_only=("9999",)))

        assert found.listed == 2
        assert found.with_prices == 1
        assert [symbol for symbol, _name in found.empty] == ["9999"]

    def test_the_name_comes_with_it(self) -> None:
        """**コードだけ出しても、誰も判断できない。**"""
        found = survey(_database({"1301": 40}, listed_only=("9999",)))

        assert found.empty[0][1] == "9999 のなまえ"

    def test_a_full_series_is_not_a_hole(self) -> None:
        found = survey(_database({"1301": 40, "1302": 40}))

        assert found.empty == ()
        assert found.warnings() == []

    def test_the_share_is_reported_not_just_the_count(self) -> None:
        """**件数ではなく割合で見る**（`CLAUDE.md`）。"""
        found = survey(_database({"1301": 40}, listed_only=("9998", "9999")))

        assert found.empty_share == pytest.approx(2 / 3)


class TestPresentButUnusableIsItsOwnRow:
    """**足が在っても、窓を1つも開けられないなら使えない。**

    穴と混ぜると、直しようの違うものが1つの数になる。
    """

    def test_a_short_series_is_thin_not_empty(self) -> None:
        found = survey(_database({"1301": 5}))

        assert found.empty == ()
        assert [symbol for symbol, _name, _bars in found.thin] == ["1301"]

    def test_the_bar_count_comes_with_it(self) -> None:
        found = survey(_database({"1301": 5}))

        assert found.thin[0][2] == 5

    def test_the_threshold_comes_from_the_window_not_a_guess(self) -> None:
        """**22 は 20営業日の窓から出した値である。** 推測ではない。"""
        assert THIN_BARS == 22
        # 21 本では窓が開かない。22 本なら1つだけ開く。
        assert survey(_database({"1301": 21})).thin
        assert not survey(_database({"1301": 22})).thin

    def test_the_window_can_be_changed_from_outside(self) -> None:
        """**窓を変えれば一緒に変わる。** 決め打ちにしない。"""
        assert not survey(_database({"1301": 30}), thin_bars=10).thin
        assert survey(_database({"1301": 30}), thin_bars=40).thin

    def test_a_threshold_below_one_is_refused(self) -> None:
        with pytest.raises(ValueError, match="thin_bars"):
            survey(_database({"1301": 40}), thin_bars=0)


class TestTheTwoReadingsCanBeCompared:
    """**別の切り口から同じ数を出して、一致するか見る**（`CLAUDE.md`）。

    一様に銘柄を引けば、使えない割合がそのまま捨てられる。陰性対照は
    「穴」を 2.1% と出していた。**噛み合わなければ、どちらかが違うものを
    数えている。**
    """

    def test_the_unusable_share_adds_both_kinds(self) -> None:
        found = survey(_database({"1301": 40, "1302": 5}, listed_only=("9999",)))

        assert found.empty_share == pytest.approx(1 / 3)
        assert found.thin_share == pytest.approx(1 / 3)
        assert found.unusable_share == pytest.approx(2 / 3)

    def test_both_kinds_raise_their_own_warning(self) -> None:
        """**1つの警告で2つを守らない**——片方が通ったときに黙る。"""
        found = survey(_database({"1302": 5}, listed_only=("9999",)))

        lines = found.warnings()
        assert any("足が1本も無い" in line for line in lines)
        assert any("本未満" in line for line in lines)

    def test_an_empty_roster_says_so_rather_than_reporting_zero(self) -> None:
        """**0 を「異常なし」と読まない。**"""
        empty = Coverage(
            market="JP", listed=0, with_prices=0, thin_bars=THIN_BARS, empty=(), thin=()
        )

        assert empty.warnings() == ["**JP の銘柄が1件も無い。**"]
        assert empty.unusable_share == 0.0


class TestOnlyTheMarketAskedFor:
    def test_another_market_is_not_counted(self) -> None:
        database = _database({"1301": 40})
        with database.session() as session:
            get_or_create_security(session, "AAPL", market="US", name="Apple")
            session.commit()

        found = survey(database, market="JP")

        assert found.listed == 1
        assert found.empty == ()
