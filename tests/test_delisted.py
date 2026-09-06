"""名簿の保存と、そこから universe を組み直す部分の検証。

ここで守りたいのは1つだけ。**和集合を universe に使わせないこと。** 生存
バイアスを直す作業が、そのまま先読みの持ち込みになるのが一番まずい形なので、
``universe_as_of`` が未来の名簿を混ぜないことを明示的に確かめる。
"""

from __future__ import annotations

import datetime as dt

import pytest

from stock_ai.data.delisted import (
    DEFAULT_SNAPSHOT_DIR,
    TACHIBANA_SNAPSHOT_DIR,
    all_profiles,
    covered_from,
    delistings,
    harvest_snapshots,
    membership,
    monthly_membership,
    monthly_snapshot,
    read_snapshot,
    snapshot_dates,
    snapshot_path,
    universe_as_of,
    write_snapshot,
)
from stock_ai.data.types import SecurityProfile


def _profile(symbol: str, name: str = "テスト") -> SecurityProfile:
    return SecurityProfile(symbol=symbol, market="JP", name=name, sector="Industrials")


def test_snapshot_dates_always_includes_the_end() -> None:
    """刻み幅に乗らなくても最終日を落とさない。"""
    dates = snapshot_dates(dt.date(2024, 1, 1), dt.date(2024, 3, 5), step_days=30)
    assert dates == [
        dt.date(2024, 1, 1),
        dt.date(2024, 1, 31),
        dt.date(2024, 3, 1),
        dt.date(2024, 3, 5),
    ]


def test_snapshot_dates_rejects_a_backwards_range() -> None:
    with pytest.raises(ValueError, match="before start"):
        snapshot_dates(dt.date(2024, 3, 1), dt.date(2024, 1, 1))


def test_snapshot_roundtrip_keeps_commas_in_a_name(tmp_path) -> None:
    """会社名にカンマが入っても列がずれない（社名は provider 由来の生値）。"""
    path = write_snapshot(tmp_path, dt.date(2024, 1, 1), [_profile("7203", "トヨタ, 株式会社")])
    back = read_snapshot(path)
    assert [profile.name for profile in back] == ["トヨタ, 株式会社"]
    assert back[0].market == "JP"


def test_missing_fields_come_back_as_none(tmp_path) -> None:
    """空文字ではなく ``None`` に戻す。空文字は「不明」ではなく「空という値」。"""
    write_snapshot(tmp_path, dt.date(2024, 1, 1), [SecurityProfile(symbol="1301", market="JP")])
    back = read_snapshot(snapshot_path(tmp_path, dt.date(2024, 1, 1)))
    assert back[0].name is None
    assert back[0].industry is None


def test_harvest_reuses_a_file_instead_of_refetching(tmp_path) -> None:
    """再開が安いこと。締切が動かない以上、これは性能ではなく要件。"""
    asked: list[dt.date] = []

    def fetch(on: dt.date) -> list[SecurityProfile]:
        asked.append(on)
        return [_profile("7203")]

    dates = [dt.date(2024, 1, 1), dt.date(2024, 2, 1)]
    harvest_snapshots(tmp_path, dates, fetch)
    assert asked == dates

    asked.clear()
    again = harvest_snapshots(tmp_path, dates, fetch)
    assert asked == []
    assert again.reused == dates
    # 再利用でも銘柄は集まる。ここが空だと2回目の実行が株価を取りに行かない。
    assert again.union == {"7203"}


def test_harvest_records_a_refusal_and_keeps_going(tmp_path) -> None:
    """5年窓の外は必ず断られる。1日の失敗で残りを落とさない。"""

    def fetch(on: dt.date) -> list[SecurityProfile]:
        if on == dt.date(2021, 1, 1):
            raise RuntimeError("400 outside the subscribed window")
        return [_profile("7203")]

    report = harvest_snapshots(tmp_path, [dt.date(2021, 1, 1), dt.date(2024, 1, 1)], fetch)
    assert list(report.refused) == [dt.date(2021, 1, 1)]
    assert "400" in report.refused[dt.date(2021, 1, 1)]
    assert report.written == [dt.date(2024, 1, 1)]


def test_an_empty_response_is_a_refusal_not_a_snapshot(tmp_path) -> None:
    """空を書くと「その日は誰も上場していなかった」という名簿になる。"""
    report = harvest_snapshots(tmp_path, [dt.date(2024, 1, 1)], lambda on: [])
    assert list(report.refused) == [dt.date(2024, 1, 1)]
    assert not snapshot_path(tmp_path, dt.date(2024, 1, 1)).exists()


def test_delistings_are_snapshot_differences(tmp_path) -> None:
    """DB との差ではなく名簿同士の差。DB は上場一覧ではない。"""
    write_snapshot(tmp_path, dt.date(2024, 1, 1), [_profile("1301"), _profile("7203")])
    write_snapshot(tmp_path, dt.date(2024, 2, 1), [_profile("7203"), _profile("9999")])
    stored = membership(tmp_path)
    assert delistings(stored) == [(dt.date(2024, 1, 1), dt.date(2024, 2, 1), ["1301"])]


def test_universe_as_of_never_reaches_into_the_future(tmp_path) -> None:
    """**生存バイアスを直す作業が先読みにならないこと。**

    2024-02 に新規上場した 9999 が 2024-01 の universe に現れたら、直した
    つもりで壊している。
    """
    write_snapshot(tmp_path, dt.date(2024, 1, 1), [_profile("1301"), _profile("7203")])
    write_snapshot(tmp_path, dt.date(2024, 2, 1), [_profile("7203"), _profile("9999")])
    stored = membership(tmp_path)

    assert universe_as_of(stored, dt.date(2024, 1, 15)) == {"1301", "7203"}
    assert universe_as_of(stored, dt.date(2024, 2, 15)) == {"7203", "9999"}
    # 名簿より前の日付には universe が無い。空を返すのが正しく、直近の名簿を
    # 流用してはいけない（それが和集合と同じ罠）。
    assert universe_as_of(stored, dt.date(2023, 12, 1)) == set()


def test_unparseable_filenames_are_ignored(tmp_path) -> None:
    """置かれたメモ書き1つで、取り直せないデータの取得を止めない。"""
    (tmp_path / "メモ.csv").write_text("symbol\n7203\n", encoding="utf-8")
    write_snapshot(tmp_path, dt.date(2024, 1, 1), [_profile("7203")])
    assert list(membership(tmp_path)) == [dt.date(2024, 1, 1)]


def test_the_covered_range_is_read_from_the_refusal() -> None:
    """**境界は API が知っている。刻み幅から逆算しない。**

    実測（2026-09-04）で 2021-09-01 を頼むとこの文面で断られた。30日刻みでは
    最初の名簿が 2021-10-01 になったが、境界は 2021-09-04 で、逆算していたら
    4週間ぶん取り逃していた。窓は毎日後ろへ動くので取り返せない。
    """
    message = (
        "J-Quants listed/info returned 400 for 2021-09-01: Your subscription "
        "covers the following dates: 2021-09-04 ~ . If you want more data, "
        "please check other plans:https://jpx-jquants.com/#dataset"
    )
    assert covered_from(message) == dt.date(2021, 9, 4)


def test_an_unreadable_refusal_returns_none_rather_than_raising() -> None:
    """文面が変わっただけで、取り直せないデータの取得を止めない。"""
    assert covered_from("429 Too Many Requests") is None
    assert covered_from("covers the following dates: nonsense ~") is None


def test_snapshots_are_written_with_lf_endings(tmp_path) -> None:
    """**取り直せないファイルを pre-commit フックに触らせない。**

    `csv.writer` の既定は CRLF だが、`.gitattributes` は LF に正規化し、
    `mixed-line-ending` フックも `--fix=lf` である。CRLF で書くと、コミットの
    たびにフックが63ファイルを書き換えてコミットが失敗する（実際に起きた）。
    """
    path = write_snapshot(tmp_path, dt.date(2024, 1, 1), [_profile("7203"), _profile("6758")])
    raw = path.read_bytes()

    assert b"\r\n" not in raw
    assert raw.count(b"\n") == 3  # ヘッダ + 2行


def test_all_profiles_sees_rosters_the_run_did_not_ask_for(tmp_path) -> None:
    """**`HarvestReport.union` と混同しない。**

    あちらは「今回要求した日付」の範囲でしか集めない。境界の名簿や前日に
    取った名簿は翌日の実行では要求されないので、その分だけ少なく数える。
    実測で 4,097 と報告し、ディスク上の実際は 4,124 だった。差の27銘柄は
    株価を取りに行く対象から漏れていた。
    """
    write_snapshot(tmp_path, dt.date(2024, 1, 1), [_profile("1301"), _profile("7203")])
    write_snapshot(tmp_path, dt.date(2024, 2, 1), [_profile("7203"), _profile("9999")])

    # 2月ぶんだけを要求する実行。1月にしか無い 1301 は report に入らない。
    report = harvest_snapshots(tmp_path, [dt.date(2024, 2, 1)], lambda on: [])

    assert report.union == {"7203", "9999"}
    assert set(all_profiles(tmp_path)) == {"1301", "7203", "9999"}


def test_all_profiles_keeps_the_most_recent_name(tmp_path) -> None:
    """社名が変わった銘柄は、最後に出た名簿のものを使う。"""
    write_snapshot(tmp_path, dt.date(2024, 1, 1), [_profile("7203", "旧社名")])
    write_snapshot(tmp_path, dt.date(2024, 2, 1), [_profile("7203", "新社名")])

    assert all_profiles(tmp_path)["7203"].name == "新社名"


def test_all_profiles_is_empty_without_rosters(tmp_path) -> None:
    assert all_profiles(tmp_path) == {}


def test_a_month_is_written_once_and_only_once(tmp_path) -> None:
    """**毎日呼んでよいこと。** 覚えておく必要のある運用は、いずれ忘れられる。"""
    first = monthly_snapshot(tmp_path, [_profile("7203")], on=dt.date(2026, 10, 5))
    again = monthly_snapshot(
        tmp_path, [_profile("7203"), _profile("6758")], on=dt.date(2026, 10, 20)
    )

    assert first is not None
    assert first.name == "2026-10.csv"
    assert again is None  # 同じ月なので書かない
    assert len(read_snapshot(first)) == 1  # 最初の内容のまま


def test_force_rewrites_the_month(tmp_path) -> None:
    monthly_snapshot(tmp_path, [_profile("7203")], on=dt.date(2026, 10, 5))
    again = monthly_snapshot(
        tmp_path, [_profile("7203"), _profile("6758")], on=dt.date(2026, 10, 20), force=True
    )
    assert again is not None
    assert len(read_snapshot(again)) == 2


def test_consecutive_months_differ_into_delistings(tmp_path) -> None:
    """**立花のマスタでも、毎月残せば差分が「その月に消えた銘柄」になる。**

    マスタ自体は現存銘柄しか返さないが、それは1枚だけ見た場合の話である。
    """
    monthly_snapshot(tmp_path, [_profile("1301"), _profile("7203")], on=dt.date(2026, 10, 1))
    monthly_snapshot(tmp_path, [_profile("7203")], on=dt.date(2026, 11, 1))

    stored = monthly_membership(tmp_path)

    assert list(stored) == ["2026-10", "2026-11"]
    assert stored["2026-10"] - stored["2026-11"] == {"1301"}


def test_monthly_snapshots_are_kept_apart_from_the_jquants_rosters() -> None:
    """**混ぜない。** 除外の仕方が違うので、境目をまたぐ差は嘘になる。

    J-Quants は投信・指数を396件除き、立花は上場区分で絞る。同じフォルダに
    置くと、消えてもいない銘柄が「消えた」ことになる。
    """
    assert TACHIBANA_SNAPSHOT_DIR != DEFAULT_SNAPSHOT_DIR


def test_a_daily_file_is_not_read_as_a_month(tmp_path) -> None:
    """日次の名簿が紛れ込んでも、月次の一覧には入れない。"""
    write_snapshot(tmp_path, dt.date(2026, 10, 5), [_profile("7203")])
    monthly_snapshot(tmp_path, [_profile("7203")], on=dt.date(2026, 10, 5))

    assert list(monthly_membership(tmp_path)) == ["2026-10"]


# --- 貸借の区分（2026-09-06 に足した列） ----------------------------------


def test_the_monthly_roster_keeps_the_lending_class(tmp_path) -> None:
    """**現在値を月次で残すことで、1年後に過去へ当てられる値になる。**

    立花のマスタは「いまどうなっているか」しか返さない。#7 で ``sSinyouC`` が
    現在値だと分かったとき、その場では使えなかった。
    """
    profile = SecurityProfile(symbol="7203", market="JP", name="トヨタ", lending="1")

    path = monthly_snapshot(tmp_path, [profile], on=dt.date(2026, 10, 5))

    assert path is not None
    (back,) = read_snapshot(path)
    assert back.lending == "1"


def test_a_roster_written_before_the_column_existed_still_reads(tmp_path) -> None:
    """**古いファイルには列が無い。落ちてはいけない。**

    2026-09 以前の月次の名簿は4列で書かれている。読めなくなると、取り直せない
    データが読めなくなる。
    """
    path = tmp_path / "2026-08.csv"
    path.write_text(
        "symbol,name,sector,industry\n7203,トヨタ,Consumer Discretionary,輸送用機器\n",
        encoding="utf-8",
    )

    (back,) = read_snapshot(path)

    assert back.symbol == "7203"
    assert back.lending is None


def test_the_jquants_roster_carries_the_lending_class_too(tmp_path) -> None:
    """**J-Quants の名簿も5列である。**

    最初は「立花の月次だけに足す」と書いた。**前提が間違っていた**——
    J-Quants の ``equities/master`` は ``Mrgn``/``MrgnNm`` を返しており、
    しかも日付を取る。**過去のある日にどうだったかを引けるのはこちらだけ**で、
    立花のマスタは現在値しか返さない。

    2つの名簿を別のフォルダに置くのは、**除外の仕方が違うので差を取ると
    消えてもいない銘柄が消えたことになる**ためで、列の話ではなかった。
    """
    from stock_ai.data.delisted import COLUMNS

    assert COLUMNS == ("symbol", "name", "sector", "industry", "lending")

    profile = SecurityProfile(symbol="7203", market="JP", name="トヨタ", lending="貸借")
    path = write_snapshot(tmp_path, dt.date(2026, 1, 5), [profile])

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == ",".join(COLUMNS)
    (back,) = read_snapshot(path)
    assert back.lending == "貸借"


def test_lending_coverage_separates_taken_from_landed(tmp_path) -> None:
    """**「63件取れた」と「列が入った」は別である。**

    取得が成功していても列が空、という形は例外を出さない。応答の項目名が
    想定と違えば ``row.get`` が静かに ``None`` を返すだけになる。
    """
    from stock_ai.data.delisted import lending_coverage

    # 列を足す前に保存したもの（4列）。
    (tmp_path / "2026-01-01.csv").write_text(
        "symbol,name,sector,industry\n7203,トヨタ,Consumer Discretionary,輸送用機器\n",
        encoding="utf-8",
    )
    # 列はあるが空（取得は成功、値は入っていない）。
    (tmp_path / "2026-02-01.csv").write_text(
        "symbol,name,sector,industry,lending\n7203,トヨタ,Consumer Discretionary,輸送用機器,\n",
        encoding="utf-8",
    )
    # 入っている。
    (tmp_path / "2026-03-01.csv").write_text(
        "symbol,name,sector,industry,lending\n7203,トヨタ,Consumer Discretionary,輸送用機器,貸借\n",
        encoding="utf-8",
    )

    assert lending_coverage(tmp_path) == (3, 1, 1)


def test_lending_coverage_on_an_empty_directory_is_zero(tmp_path) -> None:
    """名簿が1件も無くても落ちない。"""
    from stock_ai.data.delisted import lending_coverage

    assert lending_coverage(tmp_path / "無い") == (0, 0, 0)


def test_dates_without_lending_finds_the_ones_the_grid_would_miss(tmp_path) -> None:
    """**取り直す対象を日付グリッドで決めない。**

    日次で書かれる名簿は30日刻みに乗らないので、グリッドで回すと取り残される。
    実際、63件を取り直したあとに直近3日ぶんだけが残った。
    """
    from stock_ai.data.delisted import dates_without_lending

    (tmp_path / "2026-08-06.csv").write_text(
        "symbol,name,sector,industry,lending\n7203,トヨタ,Industrials,輸送用機器,貸借\n",
        encoding="utf-8",
    )
    # グリッド（30日刻み）に乗らない、日次で書かれたもの。
    (tmp_path / "2026-09-05.csv").write_text(
        "symbol,name,sector,industry\n7203,トヨタ,Industrials,輸送用機器\n",
        encoding="utf-8",
    )
    (tmp_path / "2026-09-06.csv").write_text(
        "symbol,name,sector,industry,lending\n7203,トヨタ,Industrials,輸送用機器,\n",
        encoding="utf-8",
    )

    assert dates_without_lending(tmp_path) == [dt.date(2026, 9, 5), dt.date(2026, 9, 6)]


def test_dates_without_lending_ignores_files_that_are_not_dates(tmp_path) -> None:
    """置き場所にメモ書きが1つあっても、取得を止めない。"""
    from stock_ai.data.delisted import dates_without_lending

    (tmp_path / "README.csv").write_text("symbol\n7203\n", encoding="utf-8")

    assert dates_without_lending(tmp_path) == []


def test_dates_beyond_the_window_can_no_longer_be_refetched() -> None:
    """**5年窓の前端は毎日後ろへ動く。**

    保存した当時は取れた日付が、今日はもう外にある。ここを見ずに「取り直せる」
    と案内すると、成功しない .bat を何度も実行させることになる。警告が毎回出て、
    しかも消えない。
    """
    from stock_ai.data.delisted import beyond_the_window

    today = dt.date(2026, 9, 6)
    dates = [dt.date(2021, 9, 4), dt.date(2021, 10, 1), dt.date(2026, 9, 6)]

    assert beyond_the_window(dates, today) == [dt.date(2021, 9, 4)]


def test_the_window_edge_moves_with_the_day() -> None:
    """同じ日付が、昨日は窓の中で今日は外になる。**それが起きる形を押さえる。**"""
    from stock_ai.data.delisted import beyond_the_window

    day = dt.date(2021, 9, 4)

    assert beyond_the_window([day], dt.date(2026, 9, 3)) == []
    assert beyond_the_window([day], dt.date(2026, 9, 6)) == [day]


def test_the_reach_follows_the_plan_and_not_a_fixed_five_years() -> None:
    """**プランを上げた日に、何も起きないのが一番困る。**

    例外も警告も出ないまま、5年より前の日付を「窓の外だから取れない」と判断
    して要求を出さない。20年ぶん払って5年ぶんだけ落とすことになる。
    """
    from stock_ai.data.delisted import window_days

    assert window_days("Light") == 5 * 365
    assert window_days("Standard") == 10 * 365
    assert window_days("Premium") == 20 * 365


def test_an_unknown_plan_falls_to_the_narrow_side() -> None:
    """**広いほうに倒さない。**

    広く見積もると、取れない日付を「取れるはず」と案内して、成功しない .bat を
    何度も実行させることになる。狭く見積もったときの害は、断られ方が1回記録に
    残るだけである。
    """
    from stock_ai.data.delisted import window_days

    assert window_days("Enterprise") == 5 * 365
    assert window_days(None) == 5 * 365
    assert window_days("") == 5 * 365


def test_the_plan_name_is_read_however_it_was_typed() -> None:
    """`.env` を手で編集する値である。**大文字小文字で黙って Light に落ちない。**"""
    from stock_ai.data.delisted import window_days

    assert window_days("premium") == 20 * 365
    assert window_days(" PREMIUM ") == 20 * 365


def test_the_default_start_moves_back_when_the_plan_goes_up() -> None:
    """既定の開始日は固定値ではなく、プランと今日から引く。

    1年を365日で数えているので、20年では閏日のぶん**5日ほど手前**に出る。
    直さないのは、ずれが**狭い側**だからである——本当は届く日を届かないと
    見なすだけで、その害は断られ方が1回記録に残らないことに留まる。逆向きに
    ずらすと、取れない日付を取れると案内することになる。
    """
    from stock_ai.data.delisted import earliest_reachable

    today = dt.date(2026, 9, 15)

    assert earliest_reachable("Light", today) == dt.date(2021, 9, 16)
    assert earliest_reachable("Premium", today) == dt.date(2006, 9, 20)
    assert earliest_reachable("Premium", today) > today.replace(year=today.year - 20)


def test_a_date_outside_light_is_inside_premium() -> None:
    """同じ日付が、プランによって「取り直せない」から「取れる」に変わる。"""
    from stock_ai.data.delisted import beyond_the_window

    day = dt.date(2015, 6, 1)
    today = dt.date(2026, 9, 15)

    assert beyond_the_window([day], today, plan="Light") == [day]
    assert beyond_the_window([day], today, plan="Premium") == []
