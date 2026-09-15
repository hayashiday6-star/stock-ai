"""解約してよいかを、感触ではなく数で答える。

Free に落とすと、取引カレンダーを除いて一括が丸ごと止まる。**いま原本として
持っていないものは、再契約するまで取れない。** だから「落とす先で取れなくなり、
かつ手元に1本も無いもの」が1つでもあれば、それが止める理由になる。

ここで押さえるのは2つ。

1. **Free は順序だけでは言い表せない。** `/equities/master` は Free でも API
   では使えるが、一括は使えない。使えることと残せることを混ぜると、取れない
   ものを「取れる」と案内することになる。
2. **0本のものを表から消さない。** 消すと、取り逃したものが見えなくなる。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from stock_ai.data.jquants_archive import ArchivedFile, write_manifest
from stock_ai.data.jquants_plan import (
    MINIMUM_PLAN,
    PLAN_ORDER,
    archivable,
    coverage,
    covers,
    key_period,
)

ENDPOINTS = ("/equities/master", "/fins/details", "/markets/calendar")


def _manifest(directory: Path, keys: dict[str, int]) -> None:
    write_manifest(
        directory,
        {
            key: ArchivedFile(
                key=key,
                size=size,
                bytes_written=size,
                sha256="0" * 64,
                last_modified="",
                fetched_on=dt.date(2026, 9, 15),
            )
            for key, size in keys.items()
        },
    )


class TestWhoCanReachWhat:
    def test_a_higher_plan_reaches_everything_a_lower_one_does(self) -> None:
        for endpoint in MINIMUM_PLAN:
            assert covers("Premium", endpoint)

    def test_free_reaches_only_the_free_endpoints(self) -> None:
        reachable = {endpoint for endpoint in MINIMUM_PLAN if covers("Free", endpoint)}
        assert reachable == {endpoint for endpoint, plan in MINIMUM_PLAN.items() if plan == "Free"}

    def test_an_unknown_plan_reaches_nothing(self) -> None:
        # **広いほうに倒さない。** 広く見積もると、取れないものを「あとで
        # 取れる」と案内して、解約の判断を誤らせる。
        assert not covers("Plus", "/equities/master")
        assert not covers("", "/equities/master")

    def test_an_unknown_endpoint_reaches_nothing(self) -> None:
        assert not covers("Premium", "/equities/something-new")

    def test_every_endpoint_names_a_plan_that_exists(self) -> None:
        assert set(MINIMUM_PLAN.values()) <= set(PLAN_ORDER)


class TestFreeIsNotJustTheBottomOfTheOrder:
    """**API で見えることと、一括で残せることは別である。**"""

    def test_free_can_call_master_but_cannot_archive_it(self) -> None:
        assert covers("Free", "/equities/master")
        assert not archivable("Free", "/equities/master")

    def test_the_trading_calendar_is_the_one_exception(self) -> None:
        assert archivable("Free", "/markets/calendar")

    def test_light_can_archive_what_it_can_reach(self) -> None:
        assert archivable("Light", "/equities/master")
        assert not archivable("Light", "/fins/details")


class TestOrderingKeysByTheDigitsInThem:
    """鍵の形はプランで変わる。**文字列で並べない。**"""

    def test_a_six_digit_month_is_pushed_to_the_first_of_the_month(self) -> None:
        assert key_period("fins/summary/historical/2021/fins_summary_202109.csv.gz") == "20210901"

    def test_an_eight_digit_day_is_kept(self) -> None:
        assert key_period("equities/bars/daily/live/equities_bars_daily_20260914.csv.gz") == (
            "20260914"
        )

    def test_the_premium_path_segment_does_not_reorder_the_oldest_file(self) -> None:
        # 2026-09-15 に実際にずれた。`premium/historical/2008` と
        # `historical/2021` を文字列で並べると、新しいほうが前に来る。
        old = "equities/bars/daily/premium/historical/2008/equities_bars_daily_200805.csv.gz"
        new = "equities/bars/daily/historical/2021/equities_bars_daily_202109.csv.gz"
        assert sorted([new, old])[0] == new
        assert min(key_period(old), key_period(new)) == key_period(old)

    def test_a_key_with_no_digits_sorts_first_rather_than_crashing(self) -> None:
        assert key_period("markets/calendar/calendar.csv.gz") == ""


class TestCountingWhatIsActuallyOnDisk:
    def test_endpoints_with_nothing_stay_in_the_table(self, tmp_path: Path) -> None:
        # **0 を消すと、取り逃したものが表から消えて見えなくなる。**
        _manifest(tmp_path, {"equities/master/historical/2021/equities_master_202109.csv.gz": 10})
        report = coverage("Premium", tmp_path, ENDPOINTS)
        assert [entry.endpoint for entry in report.entries] == list(ENDPOINTS)
        assert [entry.files for entry in report.entries] == [1, 0, 0]

    def test_the_span_comes_from_the_digits_not_the_name(self, tmp_path: Path) -> None:
        _manifest(
            tmp_path,
            {
                "equities/master/premium/historical/2008/equities_master_200805.csv.gz": 10,
                "equities/master/historical/2021/equities_master_202109.csv.gz": 20,
            },
        )
        entry = coverage("Premium", tmp_path, ENDPOINTS).entries[0]
        assert (entry.first, entry.last) == ("20080501", "20210901")
        assert entry.bytes == 30

    def test_a_key_outside_the_known_list_is_counted_not_dropped(self, tmp_path: Path) -> None:
        # **表に無い鍵が出たら、表のほうが古い。** 黙って捨てると在庫が
        # 実際より少なく見える。
        _manifest(tmp_path, {"equities/brand-new/historical/2026/x_202601.csv.gz": 10})
        assert coverage("Premium", tmp_path, ENDPOINTS).unknown_keys == 1

    def test_no_manifest_means_no_files_rather_than_an_error(self, tmp_path: Path) -> None:
        report = coverage("Premium", tmp_path, ENDPOINTS)
        assert all(entry.files == 0 for entry in report.entries)


class TestDecidingWhetherToDowngrade:
    def test_something_reachable_now_but_unstored_blocks_the_downgrade(
        self, tmp_path: Path
    ) -> None:
        _manifest(tmp_path, {"equities/master/historical/2021/equities_master_202109.csv.gz": 10})
        report = coverage("Premium", tmp_path, ENDPOINTS)
        assert [entry.endpoint for entry in report.blockers("Free")] == ["/fins/details"]

    def test_nothing_blocks_once_every_losing_endpoint_has_a_file(self, tmp_path: Path) -> None:
        _manifest(
            tmp_path,
            {
                "equities/master/historical/2021/equities_master_202109.csv.gz": 10,
                "fins/details/historical/2021/fins_details_202109.csv.gz": 10,
            },
        )
        report = coverage("Premium", tmp_path, ENDPOINTS)
        assert report.blockers("Free") == []
        # **失うことと、取り逃すことは別。** 増やせなくなるものは残る。
        assert {entry.endpoint for entry in report.losing("Free")} == {
            "/equities/master",
            "/fins/details",
        }

    def test_what_the_current_plan_cannot_reach_is_not_a_blocker(self, tmp_path: Path) -> None:
        # Light では `/fins/details` がそもそも取れない。無いのは取り逃しでは
        # なく、契約していないからである。**混ぜると、直しようのない警告が
        # 毎回出る。**
        _manifest(tmp_path, {"equities/master/historical/2021/equities_master_202109.csv.gz": 10})
        assert coverage("Light", tmp_path, ENDPOINTS).blockers("Free") == []

    def test_the_trading_calendar_is_never_lost_by_going_free(self, tmp_path: Path) -> None:
        report = coverage("Premium", tmp_path, ENDPOINTS)
        assert "/markets/calendar" not in {entry.endpoint for entry in report.losing("Free")}
