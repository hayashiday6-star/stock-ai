"""日次自動化が走らなかった日を数える（`stock_ai.core.log_gaps`）。

**出力からは気付けなかった。** 2026-09-19 にユーザーが `logs/` を手で数えて
分かった——`logs/daily` に 4日、`logs/accumulation` に 2日の穴があった。

**「土日だから」ではない。** 09-12(土)・09-13(日)・09-19(土) は走っている。
そして **09-16 は同じ日の 07:02 に accumulation が走っている**ので、PC は
動いていた。06:00 の daily だけがログを1行も残していない。

**`Get-ScheduledTaskInfo` は両方 `LastTaskResult: 0` と出る。** それは
**最後に走った回**の話で、走らなかった回は数に入らない。**「異常なし」の顔を
している。**

ここで押さえるのは4つ。

1. **`daily` と `accumulation` を独立に数える。** 片方が埋まっていると、
   もう片方の穴が「異常なし」に化ける（#5 で踏んだ形）
2. **0 を「読めた」と読まない。** ファイルが無い日と、在るが空の日は別
3. **自動化を入れる前を穴と呼ばない。** 最初のログより前は数えない
4. **今日を数えない。** まだ走っていない時刻かもしれない
"""

from __future__ import annotations

import datetime as dt
import pathlib

import pytest

from stock_ai.core.log_gaps import DEFAULT_WINDOW_DAYS, WATCHED, Coverage, survey, survey_all

_TODAY = dt.date(2026, 9, 19)


def _logs(tmp_path: pathlib.Path, **folders: dict[str, str]) -> pathlib.Path:
    """``{フォルダ名: {日付: 中身}}`` からログの木を作る。"""
    root = tmp_path / "logs"
    for name, days in folders.items():
        where = root / name
        where.mkdir(parents=True, exist_ok=True)
        for day, body in days.items():
            (where / f"{day}.log").write_text(body, encoding="utf-8")
    return root


def _days(first: str, last: str, drop: tuple[str, ...] = ()) -> dict[str, str]:
    """``first`` から ``last`` まで毎日、``drop`` の日だけ抜く。"""
    begin, end = dt.date.fromisoformat(first), dt.date.fromisoformat(last)
    found = {}
    day = begin
    while day <= end:
        key = day.isoformat()
        if key not in drop:
            found[key] = "ran\n"
        day += dt.timedelta(days=1)
    return found


class TestAGapIsFound:
    def test_nothing_missing_says_so(self, tmp_path: pathlib.Path) -> None:
        root = _logs(tmp_path, daily=_days("2026-08-21", "2026-09-18"))

        found = survey(root, "daily", today=_TODAY)

        assert found.missing == ()
        assert found.warnings() == []

    def test_the_real_gaps_are_the_ones_reported(self, tmp_path: pathlib.Path) -> None:
        """**ユーザーが手で数えた穴**をそのまま置いて、同じ答えが出るか。"""
        gaps = ("2026-08-29", "2026-09-10", "2026-09-11", "2026-09-16")
        root = _logs(tmp_path, daily=_days("2026-08-21", "2026-09-18", drop=gaps))

        found = survey(root, "daily", today=_TODAY)

        assert [day.isoformat() for day in found.missing] == list(gaps)

    def test_the_warning_names_the_days(self, tmp_path: pathlib.Path) -> None:
        """**表の1行にしない。** 気付かなくても目に入るのが警告である。"""
        root = _logs(tmp_path, daily=_days("2026-08-21", "2026-09-18", drop=("2026-09-16",)))

        lines = survey(root, "daily", today=_TODAY).warnings()

        assert any("2026-09-16" in line for line in lines)
        assert any("daily" in line for line in lines)


class TestEachFolderIsCountedOnItsOwn:
    """**片方が埋まっていると、もう片方の穴が「異常なし」に化ける。**

    #5 で踏んだ形そのもの——会計年度末が全件読めていないのに、会社予想の列
    しか見ていなかったので警告が鳴らなかった。
    """

    def test_a_full_folder_does_not_hide_an_empty_one(self, tmp_path: pathlib.Path) -> None:
        root = _logs(
            tmp_path,
            daily=_days("2026-08-21", "2026-09-18", drop=("2026-09-16",)),
            accumulation=_days("2026-08-21", "2026-09-18"),
        )

        found = {row.name: row for row in survey_all(root, today=_TODAY)}

        assert [day.isoformat() for day in found["daily"].missing] == ["2026-09-16"]
        assert found["accumulation"].missing == ()

    def test_both_folders_are_looked_at(self, tmp_path: pathlib.Path) -> None:
        root = _logs(tmp_path, daily=_days("2026-09-01", "2026-09-18"))

        names = [row.name for row in survey_all(root, today=_TODAY)]

        assert names == list(WATCHED)


class TestAnEmptyLogIsNotARun:
    """**0 を「読めた」と読まない。** 在るが空の日は、別に数える。"""

    def test_an_empty_file_is_its_own_bucket(self, tmp_path: pathlib.Path) -> None:
        days = _days("2026-09-01", "2026-09-18")
        days["2026-09-16"] = ""
        root = _logs(tmp_path, daily=days)

        found = survey(root, "daily", today=_TODAY)

        assert found.missing == ()
        assert [day.isoformat() for day in found.empty] == ["2026-09-16"]

    def test_whitespace_only_counts_as_empty(self, tmp_path: pathlib.Path) -> None:
        days = _days("2026-09-01", "2026-09-18")
        days["2026-09-16"] = "   \n\n"
        root = _logs(tmp_path, daily=days)

        assert survey(root, "daily", today=_TODAY).empty

    def test_the_two_buckets_raise_their_own_warnings(self, tmp_path: pathlib.Path) -> None:
        """**1つの警告で2つを守らない。**"""
        days = _days("2026-09-01", "2026-09-18", drop=("2026-09-15",))
        days["2026-09-16"] = ""
        root = _logs(tmp_path, daily=days)

        lines = survey(root, "daily", today=_TODAY).warnings()

        assert any("2026-09-15" in line for line in lines)
        assert any("2026-09-16" in line and "空" in line for line in lines)


class TestTheWindowIsHonest:
    def test_before_the_first_log_is_not_a_gap(self, tmp_path: pathlib.Path) -> None:
        """**自動化を入れる前を穴と呼ばない。**"""
        root = _logs(tmp_path, daily=_days("2026-09-15", "2026-09-18"))

        found = survey(root, "daily", today=_TODAY)

        assert found.missing == ()
        assert found.window_from == dt.date(2026, 9, 15)

    def test_today_is_not_counted(self, tmp_path: pathlib.Path) -> None:
        """**まだ走っていない時刻かもしれない。**"""
        root = _logs(tmp_path, daily=_days("2026-09-01", "2026-09-18"))

        found = survey(root, "daily", today=_TODAY)

        assert found.window_to == dt.date(2026, 9, 18)
        assert _TODAY not in found.missing

    def test_the_window_does_not_reach_further_than_asked(self, tmp_path: pathlib.Path) -> None:
        root = _logs(tmp_path, daily=_days("2026-01-01", "2026-09-18"))

        found = survey(root, "daily", today=_TODAY, window_days=7)

        assert found.window_from == dt.date(2026, 9, 12)

    def test_a_folder_with_no_logs_at_all_says_so(self, tmp_path: pathlib.Path) -> None:
        """**1件も無いことを、穴 0 と読まない。**"""
        root = _logs(tmp_path, daily={})

        found = survey(root, "daily", today=_TODAY)

        assert found.first_log is None
        assert found.missing == ()
        assert any("1件も無い" in line for line in found.warnings())

    def test_a_missing_folder_is_the_same_as_an_empty_one(self, tmp_path: pathlib.Path) -> None:
        root = _logs(tmp_path, daily=_days("2026-09-01", "2026-09-18"))

        found = survey(root, "accumulation", today=_TODAY)

        assert found.first_log is None

    def test_a_window_of_zero_is_refused(self, tmp_path: pathlib.Path) -> None:
        with pytest.raises(ValueError, match="window_days"):
            survey(_logs(tmp_path, daily={}), "daily", today=_TODAY, window_days=0)


class TestTheDefaultsAreTheOnesTheReportUses:
    def test_the_window_is_thirty_days(self) -> None:
        assert DEFAULT_WINDOW_DAYS == 30

    def test_both_jobs_are_watched(self) -> None:
        assert WATCHED == ("daily", "accumulation")

    def test_a_clean_run_reports_in_one_line(self, tmp_path: pathlib.Path) -> None:
        """**穴が無いときは1行で終える。** 毎日刷る散文は要らない。"""
        root = _logs(tmp_path, daily=_days("2026-08-21", "2026-09-18"))

        found = survey(root, "daily", today=_TODAY)

        assert found.warnings() == []
        assert "穴なし" in found.summary()


class TestItRefusesToPretend:
    def test_an_unparsable_name_is_ignored_not_counted(self, tmp_path: pathlib.Path) -> None:
        root = _logs(tmp_path, daily=_days("2026-09-01", "2026-09-18"))
        (root / "daily" / "notes.txt").write_text("x", encoding="utf-8")
        (root / "daily" / "2026-13-99.log").write_text("x", encoding="utf-8")

        found = survey(root, "daily", today=_TODAY)

        assert found.missing == ()

    def test_the_summary_says_which_folder(self, tmp_path: pathlib.Path) -> None:
        root = _logs(tmp_path, daily=_days("2026-09-01", "2026-09-18"))

        assert "daily" in survey(root, "daily", today=_TODAY).summary()

    def test_a_coverage_built_by_hand_still_warns(self) -> None:
        found = Coverage(
            name="daily",
            window_from=dt.date(2026, 9, 1),
            window_to=dt.date(2026, 9, 18),
            missing=(dt.date(2026, 9, 16),),
            empty=(),
            seen=17,
            first_log=dt.date(2026, 9, 1),
        )

        assert any("2026-09-16" in line for line in found.warnings())
