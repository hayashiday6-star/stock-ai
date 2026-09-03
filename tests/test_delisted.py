"""名簿の保存と、そこから universe を組み直す部分の検証。

ここで守りたいのは1つだけ。**和集合を universe に使わせないこと。** 生存
バイアスを直す作業が、そのまま先読みの持ち込みになるのが一番まずい形なので、
``universe_as_of`` が未来の名簿を混ぜないことを明示的に確かめる。
"""

from __future__ import annotations

import datetime as dt

import pytest

from stock_ai.data.delisted import (
    covered_from,
    delistings,
    harvest_snapshots,
    membership,
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
