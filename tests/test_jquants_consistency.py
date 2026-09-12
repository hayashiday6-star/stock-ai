"""名簿と四本値を、互いに突き合わせる。

**2本は同じ原本から出ているが、同じ絞り込みを通っていない。** 名簿は
`normalize_listings`（ETF・REIT・TOKYO PRO を落とす）を通り、四本値は
`four_digit_code` だけを通る。だから差は必ず出る。

ここで押さえるのは、**差が出ること**ではなく「差の理由を1件ずつ言えること」
である。「だいたい説明が付く」で済ませて、当時の市場ではなく最新の市場を
見ていた、というのが直前の失敗である。
"""

from __future__ import annotations

import csv
import datetime as dt
import gzip
import io
from pathlib import Path

from stock_ai.data.delisted import write_snapshot
from stock_ai.data.jquants_archive import archive
from stock_ai.data.jquants_bulk import BulkFile
from stock_ai.data.jquants_consistency import (
    NO_ROSTER_ROW,
    ROSTER_DISAGREES,
    bars_by_date,
    check,
    compare_day,
    reasons_by_date,
)
from stock_ai.data.types import SecurityProfile
from stock_ai.data.universe import FUND, UNTRADABLE, normalize_listings, rejection_reason

TODAY = dt.date(2026, 9, 7)
DAY = dt.date(2026, 8, 3)

BAR_COLUMNS = ["Date", "Code", "O", "H", "L", "C", "Vo", "AdjFactor"]
MASTER_COLUMNS = ["Date", "Code", "CoName", "Mkt", "MktNm", "S33", "S33Nm", "MrgnNm"]

#: 東証プライム、化学。**名簿に残る側。**
PRIME, CHEMICALS = "0111", "3200"

#: TOKYO PRO Market。買えないので名簿から落ちる。
PRO = "0105"

#: その他（ETF・REIT がここに来る）。
OTHER_SECTOR = "9999"


def _bar(date: str, code: str, close: str = "100", volume: str = "1000") -> dict[str, str]:
    return {
        "Date": date,
        "Code": code,
        "O": close or "",
        "H": close or "",
        "L": close or "",
        "C": close,
        "Vo": volume,
        "AdjFactor": "1.0",
    }


def _listing(date: str, code: str, market: str = PRIME, sector: str = CHEMICALS) -> dict[str, str]:
    return {
        "Date": date,
        "Code": code,
        "CoName": f"会社{code}",
        "Mkt": market,
        "MktNm": "プライム" if market == PRIME else "TOKYO PRO MARKET",
        "S33": sector,
        "S33Nm": "化学",
        "MrgnNm": "貸借",
    }


def _csv(columns: list[str], rows: list[dict[str, str]]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8")


class TestTheFilterHasOneCopy:
    """**絞り込みの規則を2つ持たない。**

    名簿を作るときと、落ちた理由を言うときで別の判定を書くと、片方だけ直した
    ときに気付けない。ここは「同じ答えを返すか」を直接当てる。
    """

    def _records(self) -> list[dict[str, str]]:
        return [
            _listing("2026-08-03", "13010"),  # 残る
            _listing("2026-08-03", "99990", sector=OTHER_SECTOR),  # 投信・ETF
            _listing("2026-08-03", "20000", market=PRO),  # 買えない市場
            _listing("2026-08-03", "12345"),  # 4桁にならない
        ]

    def test_a_reason_is_given_exactly_when_the_roster_drops_it(self) -> None:
        records = self._records()
        kept = {profile.symbol for profile in normalize_listings(records)}

        for record in records:
            reason = rejection_reason(record)
            code = record["Code"][:4]
            assert (reason is None) == (code in kept), record["Code"]

    def test_the_reasons_name_what_was_dropped(self) -> None:
        reasons = [rejection_reason(record) for record in self._records()]

        assert reasons[0] is None
        assert reasons[1] == FUND
        assert reasons[2] == UNTRADABLE


class TestMissingRowsAndQuietDaysAreDifferent:
    """**「行が無い」と「終値が無い」を同じ数に混ぜない。**

    売買の無かった日は普通にある（実測で 161,397 行、すべて出来高0）。行その
    ものが無いのは、それとは別の話である。混ぜると、警告が普通の日に埋もれる。
    """

    def test_a_roster_symbol_with_no_row_is_a_warning(self) -> None:
        day = compare_day(DAY, {"1301"}, {}, {})

        assert day.no_bar_row == ["1301"]
        assert day.quiet == 0

    def test_a_row_with_no_close_and_no_volume_is_just_a_quiet_day(self) -> None:
        day = compare_day(DAY, {"1301"}, {"1301": (False, 0.0)}, {})

        assert day.quiet == 1
        assert day.no_bar_row == []
        assert day.traded_no_close == []

    def test_volume_without_a_close_is_a_warning(self) -> None:
        """**売買があったのに終値が無い。** 「休んだ日」では説明が付かない。"""
        day = compare_day(DAY, {"1301"}, {"1301": (False, 5000.0)}, {})

        assert day.traded_no_close == ["1301"]
        assert day.quiet == 0

    def test_a_close_counts_as_matched(self) -> None:
        day = compare_day(DAY, {"1301"}, {"1301": (True, 1000.0)}, {})

        assert day.matched == 1


class TestEveryExtraSymbolGetsAReason:
    """四本値にあって名簿にいない銘柄は、**1件ずつ理由が付く。**"""

    def test_a_fund_is_explained(self) -> None:
        day = compare_day(DAY, set(), {"9999": (True, 10.0)}, {"9999": FUND})

        assert day.price_only == {"9999": FUND}

    def test_a_market_we_cannot_trade_is_explained(self) -> None:
        day = compare_day(DAY, set(), {"2000": (True, 10.0)}, {"2000": UNTRADABLE})

        assert day.price_only == {"2000": UNTRADABLE}

    def test_no_row_in_the_master_is_not_explained(self) -> None:
        """原本に行が無いなら、絞り込みでは説明が付かない。**本当の食い違い。**"""
        day = compare_day(DAY, set(), {"1301": (True, 10.0)}, {})

        assert day.price_only == {"1301": NO_ROSTER_ROW}

    def test_a_row_that_should_have_been_kept_is_not_explained(self) -> None:
        """**原本では残るはずなのに、保存された名簿に無い。** 取り出しの取りこぼし。

        空文字（＝落ちていない）を「鍵が無い」と同じに扱うと、これが
        「原本に行が無い」に化けて、直す場所を間違える。
        """
        day = compare_day(DAY, set(), {"1301": (True, 10.0)}, {"1301": ""})

        assert day.price_only == {"1301": ROSTER_DISAGREES}

    def test_a_row_without_a_close_is_not_chased(self) -> None:
        """値の付かない行が名簿に無いのは、確かめるまでもない。"""
        day = compare_day(DAY, set(), {"2000": (False, 0.0)}, {})

        assert day.price_only == {}


class TestReadingTheRawFiles:
    def test_bars_keep_the_row_even_when_the_close_is_blank(self) -> None:
        """**終値の無い行を落として渡さない。** 落とすと「行が無い」に化ける。"""
        payload = _csv(BAR_COLUMNS, [_bar("2026-08-03", "13010", close="", volume="0")])

        by_date = bars_by_date(payload)

        assert by_date[DAY]["1301"] == (False, 0.0)

    def test_a_zero_close_is_not_a_price(self) -> None:
        payload = _csv(BAR_COLUMNS, [_bar("2026-08-03", "13010", close="0")])

        assert bars_by_date(payload)[DAY]["1301"][0] is False

    def test_kept_rows_are_marked_with_an_empty_reason(self) -> None:
        payload = _csv(MASTER_COLUMNS, [_listing("2026-08-03", "13010")])

        assert reasons_by_date(payload)[DAY] == {"1301": ""}

    def test_dropped_rows_carry_their_reason(self) -> None:
        payload = _csv(MASTER_COLUMNS, [_listing("2026-08-03", "20000", market=PRO)])

        assert reasons_by_date(payload)[DAY] == {"2000": UNTRADABLE}


class TestTheWholeRunEndToEnd:
    """部品だけでなく、**組み立てを1本通す。**"""

    def _archive(self, tmp_path: Path) -> None:
        bars = gzip.compress(
            _csv(
                BAR_COLUMNS,
                [
                    _bar("2026-08-03", "13010"),  # 名簿にいる。一致。
                    _bar("2026-08-03", "13020", close="", volume="0"),  # 休んだ日
                    _bar("2026-08-03", "20000"),  # TOKYO PRO。名簿に無い。
                    _bar("2026-08-03", "99990"),  # ETF。名簿に無い。
                ],
            )
        )
        master = gzip.compress(
            _csv(
                MASTER_COLUMNS,
                [
                    _listing("2026-08-03", "13010"),
                    _listing("2026-08-03", "13020"),
                    _listing("2026-08-03", "20000", market=PRO),
                    _listing("2026-08-03", "99990", sector=OTHER_SECTOR),
                ],
            )
        )
        archive(
            [
                BulkFile(
                    key="equities/bars/daily/historical/2026/eq_bars_202608.csv.gz",
                    last_modified="",
                    size=len(bars),
                ),
                BulkFile(
                    key="equities/master/historical/2026/eq_master_202608.csv.gz",
                    last_modified="",
                    size=len(master),
                ),
            ],
            lambda key: bars if "bars" in key else master,
            tmp_path,
            on=TODAY,
        )

    def _rosters(self, tmp_path: Path) -> Path:
        out = tmp_path / "rosters"
        write_snapshot(
            out,
            DAY,
            [
                SecurityProfile(symbol="1301", market="JP", name="会社13010"),
                SecurityProfile(symbol="1302", market="JP", name="会社13020"),
            ],
        )
        return out

    def test_every_difference_is_explained(self, tmp_path: Path) -> None:
        self._archive(tmp_path)

        report = check(tmp_path, self._rosters(tmp_path))

        assert report.days == 1
        assert report.matched == 1
        assert report.quiet == 1
        assert not report.no_bar_row
        assert not report.traded_no_close
        assert report.reasons == {UNTRADABLE: 1, FUND: 1}
        assert not report.unexplained  # **ここが 0 であることが結論である**

    def test_a_day_without_a_roster_is_not_counted_as_agreement(self, tmp_path: Path) -> None:
        """**比べていない日を「一致した」に入れない。**"""
        self._archive(tmp_path)

        report = check(tmp_path, tmp_path / "empty")

        assert report.days == 0
        assert report.missing_roster == [DAY]
