"""一括ダウンロードの原本を残す。

**取得は1回きり、解析は何度でもやり直せる。** 契約は 2026-09-22 で終わるが、
パーサの誤りは10月にも見つかる。ここが黙って壊れると、そのとき原本が無い。

境目を1つずつ押さえる。**どれも例外を出さずに起きる形である。**
"""

from __future__ import annotations

import datetime as dt
import gzip
import hashlib

import pytest

from stock_ai.data.jquants_archive import (
    MANIFEST_COLUMNS,
    archive,
    path_for,
    read_manifest,
    verify,
)
from stock_ai.data.jquants_bulk import BulkFile

TODAY = dt.date(2026, 9, 15)


def _file(key: str, size: int) -> BulkFile:
    return BulkFile(key=key, last_modified="2026-09-15T00:00:00Z", size=size)


def _gz(text: str) -> bytes:
    return gzip.compress(text.encode("utf-8"))


def test_the_payload_is_written_exactly_as_it_arrived(tmp_path) -> None:
    """**展開しない。CSV に直さない。** 読み方は後から変えたい側である。"""
    payload = _gz("Code,Value\n13010,1\n")
    files = [_file("fins/summary/historical/2021/fins_summary_202109.csv.gz", len(payload))]

    report = archive(files, lambda _key: payload, tmp_path, on=TODAY)

    written = path_for(tmp_path, files[0].key)
    assert written.read_bytes() == payload  # バイト単位で同じ
    assert report.written == [files[0].key]
    assert report.bytes_written == len(payload)


def test_the_key_becomes_the_path(tmp_path) -> None:
    """向こうのパスをそのまま使う。付け替えると、由来を別に覚える必要が出る。"""
    payload = _gz("x")
    key = "markets/margin-alert/historical/2016/margin_alert_201601.csv.gz"

    archive([_file(key, len(payload))], lambda _k: payload, tmp_path, on=TODAY)

    assert (tmp_path / key).is_file()


def test_a_file_already_there_is_not_downloaded_again(tmp_path) -> None:
    """**1週間しかない契約で、最初からやり直すのは高い。**"""
    payload = _gz("x")
    files = [_file("a/b.csv.gz", len(payload))]
    calls: list[str] = []

    archive(files, lambda k: (calls.append(k), payload)[1], tmp_path, on=TODAY)
    report = archive(files, lambda k: (calls.append(k), payload)[1], tmp_path, on=TODAY)

    assert calls == ["a/b.csv.gz"]  # 2回目は落としに行かない
    assert report.reused == ["a/b.csv.gz"]


def test_a_short_file_is_downloaded_again(tmp_path) -> None:
    """**「ファイルがある」と「中身が揃っている」は別である。**

    転送が途中で切れても例外が出ないことがある。そのまま解析へ進むと、欠けた
    行に気付かないまま結論が出る。
    """
    full = _gz("Code,Value\n13010,1\n13020,2\n")
    files = [_file("a/b.csv.gz", len(full))]

    # 1回目は切れて届く。
    archive(files, lambda _k: full[: len(full) // 2], tmp_path, on=TODAY)
    # 2回目は全部届く。
    report = archive(files, lambda _k: full, tmp_path, on=TODAY)

    assert report.refetched == ["a/b.csv.gz"]
    assert path_for(tmp_path, "a/b.csv.gz").read_bytes() == full


def test_a_short_file_is_kept_and_flagged(tmp_path) -> None:
    """**消さずに印を付ける。** 消すと、次も同じところで切れたとき何も残らない。"""
    full = _gz("Code,Value\n13010,1\n")
    files = [_file("a/b.csv.gz", len(full))]

    report = archive(files, lambda _k: full[:3], tmp_path, on=TODAY)

    assert report.truncated == ["a/b.csv.gz"]
    assert path_for(tmp_path, "a/b.csv.gz").is_file()


def test_the_listed_size_and_the_received_size_are_kept_apart(tmp_path) -> None:
    """**同じ列に入れると、切れたダウンロードが「落とせた」ことになる。**"""
    full = _gz("Code,Value\n13010,1\n")
    files = [_file("a/b.csv.gz", 999_999)]  # 一覧は大きいと言っている

    archive(files, lambda _k: full, tmp_path, on=TODAY)

    (entry,) = read_manifest(tmp_path).values()
    assert entry.size == 999_999
    assert entry.bytes_written == len(full)
    assert not entry.complete


def test_the_manifest_records_what_is_needed_to_check_later(tmp_path) -> None:
    """解約後にも確かめられること。**そのときは取り返せないが、気付ける。**"""
    payload = _gz("x")
    archive([_file("a/b.csv.gz", len(payload))], lambda _k: payload, tmp_path, on=TODAY)

    (entry,) = read_manifest(tmp_path).values()

    assert entry.sha256 == hashlib.sha256(payload).hexdigest()
    assert entry.last_modified == "2026-09-15T00:00:00Z"
    assert entry.fetched_on == TODAY
    header = (tmp_path / "manifest.csv").read_text(encoding="utf-8").splitlines()[0]
    assert header == ",".join(MANIFEST_COLUMNS)


def test_one_failure_does_not_stop_the_rest(tmp_path) -> None:
    """**期限のある作業では、落とせるものを落としきるほうが合う。**"""
    payload = _gz("x")
    files = [_file("a.csv.gz", len(payload)), _file("b.csv.gz", len(payload))]

    def fetch(key: str) -> bytes:
        if key == "a.csv.gz":
            raise RuntimeError("403")
        return payload

    report = archive(files, fetch, tmp_path, on=TODAY)

    assert list(report.failed) == ["a.csv.gz"]
    assert report.written == ["b.csv.gz"]


def test_the_manifest_is_rewritten_not_appended(tmp_path) -> None:
    """追記にすると、落とし直した行が二重になる。"""
    full = _gz("Code,Value\n13010,1\n")
    files = [_file("a/b.csv.gz", len(full))]

    archive(files, lambda _k: full[:3], tmp_path, on=TODAY)
    archive(files, lambda _k: full, tmp_path, on=TODAY)

    lines = (tmp_path / "manifest.csv").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2  # 見出し + 1本


def test_verify_finds_a_file_that_went_missing(tmp_path) -> None:
    """**解約後こそ実行する意味がある。** 取り返せないが、気付ける。"""
    payload = _gz("x")
    archive([_file("a/b.csv.gz", len(payload))], lambda _k: payload, tmp_path, on=TODAY)
    path_for(tmp_path, "a/b.csv.gz").unlink()

    missing, wrong_size, changed = verify(tmp_path)

    assert missing == ["a/b.csv.gz"]
    assert not wrong_size and not changed


def test_verify_finds_a_file_whose_contents_changed(tmp_path) -> None:
    """同じ大きさで中身が違う形。**大きさだけ見ていると素通りする。**"""
    payload = _gz("Code,Value\n13010,1\n")
    archive([_file("a/b.csv.gz", len(payload))], lambda _k: payload, tmp_path, on=TODAY)
    target = path_for(tmp_path, "a/b.csv.gz")
    target.write_bytes(bytes(len(payload)))  # 同じ長さの別の中身

    missing, wrong_size, changed = verify(tmp_path)

    assert changed == ["a/b.csv.gz"]
    assert not missing and not wrong_size


def test_verify_on_an_empty_directory_is_quiet(tmp_path) -> None:
    """目録が無くても落ちない。"""
    assert verify(tmp_path / "無い") == ([], [], [])


def test_the_progress_callback_sees_every_file(tmp_path) -> None:
    """進捗は1行に収める前提だが、**全部を通ることは確かめておく。**"""
    payload = _gz("x")
    files = [_file(f"{index}.csv.gz", len(payload)) for index in range(3)]
    seen: list[tuple[int, int, str]] = []

    archive(files, lambda _k: payload, tmp_path, on=TODAY, progress=lambda *args: seen.append(args))

    assert [item[0] for item in seen] == [1, 2, 3]
    assert {item[1] for item in seen} == {3}


def test_a_gzip_payload_survives_the_round_trip(tmp_path) -> None:
    """**原本から元の CSV に戻せること。** 保存した意味がここにある。"""
    text = "Code,PubDate,PubReason\n13010,2026-09-15,{'Restricted': '1'}\n"
    payload = _gz(text)
    archive([_file("a/b.csv.gz", len(payload))], lambda _k: payload, tmp_path, on=TODAY)

    stored = path_for(tmp_path, "a/b.csv.gz").read_bytes()

    assert gzip.decompress(stored).decode("utf-8") == text


def test_an_empty_listing_writes_an_empty_manifest(tmp_path) -> None:
    """1本も無くても落ちない。目録は作る。"""
    report = archive([], lambda _k: pytest.fail("落としに行ってはいけない"), tmp_path, on=TODAY)

    assert report.summary()
    assert (tmp_path / "manifest.csv").is_file()
