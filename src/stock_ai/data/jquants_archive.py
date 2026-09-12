"""一括ダウンロードの原本を、そのままディスクに残す。

**取得は1回きり、解析は何度でもやり直せる。** 契約は 2026-09-22 で終わるが、
パーサの誤りは10月にも11月にも見つかる。原本が無ければ、そのとき取り返せない。

名簿（`data/universe_snapshots/`）を CSV で残したのと同じ理屈である。あちらの
説明にはこう書いてある。

    DB ではなくファイルなのは、これが「取り直せない生データ」だから
    である。DB は作り直せるが、解約後の J-Quants は作り直せない。

**一括ファイルも同じ性質を持つ。** それなのに、いまの `bulk.download()` は
展開して中身を返すだけで、**原本を残していない。** 取り込みが1行でも取り
こぼしていたら、それに気付いた時点でもう元が無い。

## 何を残すか

**返ってきたバイト列をそのまま。** 展開もしないし、CSV に直しもしない。

- 展開すると、gzip の中身が壊れていたときに区別が付かなくなる
- CSV に直すと、こちらの読み方が入る。**読み方は後から変えたい側である**

## 何を記録するか

`manifest.csv` に1本1行。

| 列 | なぜ要るか |
|---|---|
| `key` | 何を落としたか |
| `size` | 一覧が言っていた大きさ |
| `bytes` | **実際に届いた大きさ。** 一致しなければ途中で切れている |
| `sha256` | 後から中身が変わっていないことを確かめる |
| `last_modified` | 向こう側の更新時刻 |
| `fetched_on` | こちらが落とした日 |

**`size` と `bytes` を別々に持つのが要点である。** 同じ列に入れると、切れた
ダウンロードが「落とせた」ことになる。転送の失敗は例外を出さないことがある。

## 再開できること

**既にあるファイルは落としに行かない。** 1週間しかない契約で、途中で止まった
ときに最初からやり直すのは高い。名簿の取り込みと同じ規則にしてある。

ただし**大きさが合わないファイルは落とし直す。** 「ファイルがある」と「中身が
揃っている」は別で、この2つを混ぜたまま進むと、欠けたまま解析することになる。
"""

from __future__ import annotations

import csv
import dataclasses
import datetime as dt
import hashlib
import time
from collections.abc import Callable
from pathlib import Path

from stock_ai.config.constants import DATA_DIR
from stock_ai.core.exceptions import RateLimitError
from stock_ai.core.logging import get_logger
from stock_ai.data.jquants_bulk import BulkFile

logger = get_logger(__name__)

#: 原本の置き場所。**DB ではなくファイル。** 取り直せないため。
DEFAULT_ARCHIVE_DIR: Path = DATA_DIR / "jquants_bulk"

#: 目録のファイル名。
MANIFEST = "manifest.csv"

#: 目録の列。順序ごと固定する（後から足すなら末尾に足す）。
MANIFEST_COLUMNS = ("key", "size", "bytes", "sha256", "last_modified", "fetched_on")

#: 1本を落とす呼び出し。``key`` を受けて**展開していない**バイト列を返す。
RawFetcher = Callable[[str], bytes]


@dataclasses.dataclass(frozen=True)
class ArchivedFile:
    """目録の1行。"""

    key: str
    size: int
    """一覧が言っていた大きさ。"""

    bytes_written: int
    """実際に届いた大きさ。**``size`` と別に持つ。**"""

    sha256: str
    last_modified: str
    fetched_on: dt.date

    @property
    def complete(self) -> bool:
        """一覧の言う大きさと、届いた大きさが一致するか。

        一覧が大きさを持っていない（0）ときは、確かめようがないので通す。
        **「確かめられない」と「確かめて合っている」を同じにしない**ため、
        呼ぶ側はこの区別が要るなら ``size`` を直接見ること。
        """
        return self.size == 0 or self.size == self.bytes_written


@dataclasses.dataclass
class ArchiveReport:
    """1回の実行で何が起きたか。**数えないと、欠けたことに気付けない。**"""

    written: list[str] = dataclasses.field(default_factory=list)
    reused: list[str] = dataclasses.field(default_factory=list)
    refetched: list[str] = dataclasses.field(default_factory=list)
    """既にあったが大きさが合わず、落とし直したもの。"""

    failed: dict[str, str] = dataclasses.field(default_factory=dict)
    truncated: list[str] = dataclasses.field(default_factory=list)
    """落とし直しても大きさが合わなかったもの。**残るが、印を付ける。**"""

    waits: int = 0
    """レート制限で待った回数。**0 でないなら、間隔が速すぎる。**"""

    bytes_written: int = 0

    def summary(self) -> str:
        """1行のまとめ。"""
        return (
            f"{len(self.written)} 本を保存、{len(self.reused)} 本は既存、"
            f"{len(self.refetched)} 本を取り直し、{len(self.failed)} 本が失敗、"
            f"{self.bytes_written / 1_000_000:.1f} MB"
            + (f"、レート制限で {self.waits} 回待った" if self.waits else "")
        )


def path_for(directory: Path, key: str) -> Path:
    """``key`` の置き場所。**向こうのパスをそのまま使う。**

    ``fins/summary/historical/2021/fins_summary_202109.csv.gz`` のような形が
    そのままディレクトリになる。こちらで名前を付け替えない——付け替えると、
    どの `key` から来たのかを別に覚えておく必要が出る。
    """
    return directory / key.lstrip("/")


def read_manifest(directory: Path) -> dict[str, ArchivedFile]:
    """目録を ``key`` で引ける形にする。無ければ空。"""
    path = directory / MANIFEST
    if not path.is_file():
        return {}
    result: dict[str, ArchivedFile] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            key = (row.get("key") or "").strip()
            if not key:
                continue
            try:
                fetched = dt.date.fromisoformat((row.get("fetched_on") or "").strip())
            except ValueError:
                fetched = dt.date.min
            result[key] = ArchivedFile(
                key=key,
                size=_int(row.get("size")),
                bytes_written=_int(row.get("bytes")),
                sha256=(row.get("sha256") or "").strip(),
                last_modified=(row.get("last_modified") or "").strip(),
                fetched_on=fetched,
            )
    return result


def write_manifest(directory: Path, entries: dict[str, ArchivedFile]) -> Path:
    """目録を書く。``key`` の昇順。

    **毎回まるごと書き直す。** 追記にすると、落とし直した行が二重になる。
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / MANIFEST
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(MANIFEST_COLUMNS)
        for key in sorted(entries):
            item = entries[key]
            writer.writerow(
                [
                    item.key,
                    item.size,
                    item.bytes_written,
                    item.sha256,
                    item.last_modified,
                    item.fetched_on.isoformat(),
                ]
            )
    return path


def _int(text: str | None) -> int:
    try:
        return int(float((text or "0").strip() or 0))
    except ValueError:
        return 0


#: 目録を書き直す間隔（本）。
#:
#: **最後にまとめて書かない。** 途中で止めると目録だけが失われ、ファイルは
#: 残る。次に実行したとき「あるのに目録に無い」状態になり、**全部を落とし
#: 直す。** 2026-09-07 のリハーサルで、171本がこれで落とし直された。
MANIFEST_EVERY = 20

#: レート制限で待ち直す回数の上限。
#:
#: **レート制限はその1本ではなく走行全体のものである。** 次へ進んでも同じ
#: 拒否を集めるだけで、`RateLimitError` の説明にもそう書いてある。それなのに
#: 1本ぶんの失敗として飛ばしていて、リハーサルで74本が同じ理由で落ちた。
RATE_LIMIT_RETRIES = 4


def archive(
    files: list[BulkFile],
    fetch: RawFetcher,
    directory: Path = DEFAULT_ARCHIVE_DIR,
    on: dt.date | None = None,
    progress: Callable[[int, int, str], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    retries: int = RATE_LIMIT_RETRIES,
) -> ArchiveReport:
    """一覧のファイルを1本ずつ落として、**そのまま**保存する。

    展開しない。CSV に直さない。**返ってきたバイト列をそのまま書く。**

    途中で止めても安全に再開できる。既にあって大きさの合うファイルは落としに
    行かない——1週間しかない契約で、最初からやり直すのは高い。

    **大きさが合わないファイルは落とし直す。** 「ファイルがある」と「中身が
    揃っている」は別である。転送が途中で切れても例外が出ないことがあり、その
    まま解析へ進むと、欠けた行に気付かないまま結論が出る。

    Args:
        files: `bulk.list_files` が返した一覧。
        fetch: ``key`` を受けて**展開していない**バイト列を返す呼び出し。
        directory: 置き場所。
        on: 落とした日として記録する日付。省略時は今日。
        progress: 1本ごとに ``(番号, 総数, key)`` で呼ばれる。
        sleep: 待つときに呼ぶもの。試験で実際に待たないために差し替える。
        retries: レート制限で取り直す回数の上限。

    Returns:
        :class:`ArchiveReport`。**1本の失敗で全体を止めない**——落とせるものを
        落としきってから、失敗した分だけ再実行できるほうが、期限のある作業には
        合う。
    """
    today = on or dt.date.today()
    manifest = read_manifest(directory)
    report = ArchiveReport()

    try:
        _run(files, fetch, directory, today, progress, sleep, retries, manifest, report)
    finally:
        # **中断でも目録を書く。** Ctrl-C は `except Exception` に掛からない。
        write_manifest(directory, manifest)
    logger.info("原本の保存: %s", report.summary())
    return report


def _run(
    files: list[BulkFile],
    fetch: RawFetcher,
    directory: Path,
    today: dt.date,
    progress: Callable[[int, int, str], None] | None,
    sleep: Callable[[float], None],
    retries: int,
    manifest: dict[str, ArchivedFile],
    report: ArchiveReport,
) -> None:
    """保存の本体。目録の書き出しは呼び元が `finally` で受け持つ。"""
    total = len(files)
    for index, item in enumerate(files, start=1):
        if progress is not None:
            progress(index, total, item.key)

        target = path_for(directory, item.key)
        known = manifest.get(item.key)
        if target.is_file() and known is not None and known.complete:
            report.reused.append(item.key)
            continue

        stale = target.is_file()
        payload = _fetch_with_backoff(item.key, fetch, sleep, retries, report)
        if payload is None:
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        entry = ArchivedFile(
            key=item.key,
            size=item.size,
            bytes_written=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            last_modified=item.last_modified,
            fetched_on=today,
        )
        manifest[item.key] = entry
        report.bytes_written += len(payload)
        (report.refetched if stale else report.written).append(item.key)
        if not entry.complete:
            # **残すが、印を付ける。** 消すと、次の実行が同じところで切れた
            # ときに何も残らない。
            report.truncated.append(item.key)
            logger.warning(
                "大きさが合わない: %s（一覧 %d / 届いた %d）",
                item.key,
                item.size,
                entry.bytes_written,
            )

        saved = len(report.written) + len(report.refetched)
        if saved % MANIFEST_EVERY == 0:
            write_manifest(directory, manifest)


def _fetch_with_backoff(
    key: str,
    fetch: RawFetcher,
    sleep: Callable[[float], None],
    retries: int,
    report: ArchiveReport,
) -> bytes | None:
    """1本を落とす。**レート制限なら待って同じ本を取り直す。**

    飛ばさないのは、`RateLimitError` の説明にある通り「レート制限はその1本
    ではなく走行全体のもの」だからである。次へ進んでも、残り全部が同じ拒否を
    集める。**リハーサルでは74本がそうなった。**

    レート制限以外の失敗は1本ぶんの話なので、記録して次へ行く。期限のある
    作業では、落とせるものを落としきるほうが合う。
    """
    for attempt in range(retries + 1):
        try:
            return fetch(key)
        except RateLimitError as exc:
            if attempt == retries:
                report.failed[key] = f"{type(exc).__name__}: {exc}"
                logger.warning("レート制限が解けなかった: %s", key)
                return None
            wait = getattr(exc, "retry_after", None) or 5.0
            # **回を追うごとに長く待つ。** 同じ間隔で叩き直すと、向こうから
            # 見れば速度が変わっていない。
            report.waits += 1
            logger.info("レート制限。%.0f 秒待つ（%d 回目）", wait * (attempt + 1), attempt + 1)
            sleep(wait * (attempt + 1))
        except Exception as exc:  # noqa: BLE001 - 断られ方そのものが記録に値する
            report.failed[key] = f"{type(exc).__name__}: {exc}"
            logger.warning("原本を落とせなかった: %s: %s", key, exc)
            return None
    return None


#: 指紋を取るときに一度に読む量。
#:
#: **ファイル1本を丸ごとメモリに載せない。** いまの最大は十数MBなので載せても
#: 通るが、分足やティックを足すと1本が大きくなる。**そのとき落ちるのではなく、
#: 落ちる前に遅くなって気付けなくなる。**
HASH_CHUNK = 1024 * 1024


def fingerprint(path: Path) -> tuple[int, str]:
    """ファイルの ``(大きさ, SHA-256)``。**少しずつ読む。**"""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(HASH_CHUNK):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def verify(
    directory: Path = DEFAULT_ARCHIVE_DIR,
    progress: Callable[[int, int, str], None] | None = None,
) -> tuple[list[str], list[str], list[str]]:
    """保存済みの原本を目録と突き合わせる。**落とすことはしない。**

    解約後にも実行できる。**解約後こそ実行する意味がある**——そのとき欠けて
    いると分かっても取り返せないが、欠けているのに揃っていると思って解析する
    よりはよい。

    **全部のファイルを読み直す。** 大きさだけを見ると、途中で切れた転送が
    たまたま同じ長さになった場合に通ってしまう。5年ぶん（265MB）なら一瞬だが、
    20年ぶんや同期フォルダ越しでは分単位になる。**そのあいだ何も出ないと、
    止まったのか動いているのか分からない**ので ``progress`` を受ける。

    Args:
        directory: 見る場所。写し先を指してもよい——目録も一緒に写るので、
            **写した先だけを見て確かめられる。**
        progress: 1本ごとに ``(番号, 総数, key)`` で呼ばれる。

    Returns:
        ``(欠けている, 大きさが合わない, 中身が変わった)`` の3つ。
    """
    manifest = read_manifest(directory)
    missing: list[str] = []
    wrong_size: list[str] = []
    changed: list[str] = []
    total = len(manifest)
    for number, (key, item) in enumerate(sorted(manifest.items()), start=1):
        if progress is not None:
            progress(number, total, key)
        path = path_for(directory, key)
        if not path.is_file():
            missing.append(key)
            continue
        size, digest = fingerprint(path)
        if size != item.bytes_written:
            wrong_size.append(key)
        elif item.sha256 and digest != item.sha256:
            changed.append(key)
    return missing, wrong_size, changed
