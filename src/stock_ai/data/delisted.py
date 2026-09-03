"""上場していた会社の名簿を、消える前に日付ごと保存する。

これまでの事前登録はすべて「上場廃止銘柄が universe に入っていない」と
但し書きを付けてきた。短期リバーサル（大きく下げた銘柄を買う）では、これは
但し書きではなく**主要な脅威**になる。下げて消えた会社こそ、いまの一覧から
抜けている銘柄そのものだからである。

J-Quants の ``equities/master`` は ``date`` を取り、その日時点の一覧を返す。
実測（``universe-snapshots``）では、スナップショット同士の差＝廃止銘柄は
年あたり 49〜106 件あり、その5件すべてで株価も取れた。つまり生存バイアスは
**恒久的な制約ではなく、作業**である。

保存するのは和集合ではなく**日付ごとの名簿**である。和集合しか持たないと、
2023年に上場した会社を2021年の分位に入れられてしまう。生存バイアスを直した
つもりで先読みを入れることになり、直す前より悪い。

制約が2つある。どちらも後から緩められない。

- **5年ローリング窓の外は返らない。** 2026-09 時点の境界は 2021-09 前後。
  それ以前について、この方法で生存バイアスは直せない。
- **解約予定日を過ぎると二度と取れない。** 名簿も、廃止銘柄の株価も、
  契約が生きているうちにディスクへ落としておく必要がある。
"""

from __future__ import annotations

import csv
import datetime as dt
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from stock_ai.config.constants import DATA_DIR
from stock_ai.core.logging import get_logger
from stock_ai.data.types import SecurityProfile

logger = get_logger(__name__)

#: 名簿の置き場所。DB ではなくファイルなのは、これが「取り直せない生データ」
#: だからである。DB は作り直せるが、解約後の J-Quants は作り直せない。
DEFAULT_SNAPSHOT_DIR: Path = DATA_DIR / "universe_snapshots"

#: CSV の列。順序ごと固定する（後から足すなら末尾に足す）。
COLUMNS = ("symbol", "name", "sector", "industry")

#: 名簿を取りに行く間隔の既定値。月1回。廃止は年 50〜106 件なので、
#: 1ヶ月刻みなら「いつ消えたか」は月単位まで分かる。
DEFAULT_STEP_DAYS = 30

#: この日以降しか返らない、と実測で分かっている境界（2026-09 時点）。
#: 既定の開始日に使うだけで、これより前を禁止はしない——境界は時間とともに
#: 前に進むのではなく**後ろに動く**ので、断られ方そのものが記録に値する。
ROLLING_WINDOW_START = dt.date(2021, 9, 1)

#: 名簿を取りに行くための呼び出し。日付を受け、その日の上場一覧を返す。
SnapshotFetcher = Callable[[dt.date], list[SecurityProfile]]

#: 窓の外を頼んだときに J-Quants が返す文面から、実際の境界日を読む。
#:
#: 実測（2026-09-04）:
#: ``Your subscription covers the following dates: 2021-09-04 ~``
#:
#: **境界は API が知っている。** こちらで刻み幅から逆算すると、たまたま刻みが
#: 乗った日を境界だと思い込む。実際 30日刻みでは 2021-10-01 が最初の名簿に
#: なったが、本当の境界は 2021-09-04 で、4週間ぶん取り逃していた。
_COVERED_FROM = re.compile(r"covers the following dates:\s*(\d{4}-\d{2}-\d{2})")


def covered_from(message: str) -> dt.date | None:
    """断られた文面から「ここからなら取れる」日付を読む。読めなければ ``None``。

    文面が変わったら黙って ``None`` を返す。ここで例外を投げると、取り直せない
    データの取得が、エラーメッセージの書式変更で止まる。
    """
    found = _COVERED_FROM.search(message)
    if not found:
        return None
    try:
        return dt.date.fromisoformat(found.group(1))
    except ValueError:
        return None


def snapshot_dates(
    start: dt.date, end: dt.date, step_days: int = DEFAULT_STEP_DAYS
) -> list[dt.date]:
    """``start`` から ``end`` まで ``step_days`` 刻みの日付を返す。

    ``end`` は刻み幅に乗らなくても必ず含める。最後の1ヶ月ぶんの廃止銘柄を
    取りこぼすと、いちばん新しい——つまりいちばん検証に使う——期間が欠ける。

    Args:
        start: 最初の日付（含む）。
        end: 最後の日付（含む）。
        step_days: 間隔。1以上。

    Raises:
        ValueError: ``step_days`` が1未満か、``end`` が ``start`` より前。
    """
    if step_days < 1:
        raise ValueError(f"step_days must be at least 1; got {step_days}.")
    if end < start:
        raise ValueError(f"end ({end}) is before start ({start}).")

    dates: list[dt.date] = []
    when = start
    while when < end:
        dates.append(when)
        when += dt.timedelta(days=step_days)
    dates.append(end)
    return dates


def snapshot_path(directory: Path, on: dt.date) -> Path:
    """``on`` の名簿を置くパス。"""
    return directory / f"{on.isoformat()}.csv"


def write_snapshot(directory: Path, on: dt.date, profiles: Iterable[SecurityProfile]) -> Path:
    """``on`` の名簿を CSV で書き、パスを返す。

    銘柄コード順に並べる。差分（＝廃止銘柄）を目で追えるようにするためで、
    順序が実行ごとに変わると git の差分が読めなくなる。
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = snapshot_path(directory, on)
    rows = sorted(profiles, key=lambda profile: profile.symbol)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for profile in rows:
            writer.writerow(
                [profile.symbol, profile.name or "", profile.sector or "", profile.industry or ""]
            )
    logger.info("Wrote %d listing(s) for %s to %s", len(rows), on, path.name)
    return path


def read_snapshot(path: Path) -> list[SecurityProfile]:
    """CSV の名簿を読む。空文字は ``None`` に戻す。"""
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return [
            SecurityProfile(
                symbol=row["symbol"],
                market="JP",
                name=row.get("name") or None,
                sector=row.get("sector") or None,
                industry=row.get("industry") or None,
            )
            for row in reader
            if row.get("symbol")
        ]


def stored_dates(directory: Path) -> list[dt.date]:
    """すでに保存済みの名簿の日付を、古い順に返す。

    ファイル名が日付として読めないものは黙って飛ばす。ここで例外を投げると、
    ディレクトリに置かれた1つのメモ書きが、取り直せないデータの取得を
    止めてしまう。
    """
    if not directory.is_dir():
        return []
    found: list[dt.date] = []
    for path in directory.glob("*.csv"):
        try:
            found.append(dt.date.fromisoformat(path.stem))
        except ValueError:
            logger.debug("Ignoring %s: not a snapshot date.", path.name)
    return sorted(found)


def membership(directory: Path) -> dict[dt.date, set[str]]:
    """保存済みの名簿を ``日付 -> 銘柄コード`` で返す。

    分位を組むときは、**その日以前で最も新しい名簿**を universe に使う。
    全期間の和集合を使うと、まだ上場していない銘柄を過去に置くことになる。
    """
    result: dict[dt.date, set[str]] = {}
    for on in stored_dates(directory):
        result[on] = {profile.symbol for profile in read_snapshot(snapshot_path(directory, on))}
    return result


def universe_as_of(snapshots: dict[dt.date, set[str]], on: dt.date) -> set[str]:
    """``on`` の時点で上場していた銘柄。名簿が無ければ空集合。

    「``on`` 以前で最も新しい名簿」を使う。``on`` 以降の名簿を混ぜないのが
    肝心で、混ぜた瞬間に先読みになる。
    """
    usable = [when for when in sorted(snapshots) if when <= on]
    return set(snapshots[usable[-1]]) if usable else set()


@dataclass
class HarvestReport:
    """名簿の取得が何をして、何をできなかったか。"""

    written: list[dt.date] = field(default_factory=list)
    """今回ネットワークから取って書いた日付。"""
    reused: list[dt.date] = field(default_factory=list)
    """すでにファイルがあったので取りに行かなかった日付。"""
    refused: dict[dt.date, str] = field(default_factory=dict)
    """取れなかった日付と、その断られ方。5年窓の外はここに入る。"""
    profiles: dict[str, SecurityProfile] = field(default_factory=dict)
    """全名簿に一度でも出た銘柄。名前・業種は**最後に出た名簿**のもの。"""

    @property
    def union(self) -> set[str]:
        """一度でも上場していた銘柄コード全体。"""
        return set(self.profiles)

    def summary(self) -> str:
        """1行の要約。"""
        text = (
            f"名簿 {len(self.written)} 件を取得、{len(self.reused)} 件は既存を再利用、"
            f"{len(self.refused)} 件は取れず。延べ {len(self.profiles):,} 銘柄。"
        )
        return text


def harvest_snapshots(
    directory: Path,
    dates: Iterable[dt.date],
    fetch: SnapshotFetcher,
    refetch: bool = False,
) -> HarvestReport:
    """``dates`` の名簿をディスクに集める。

    途中で止めても安全に再開できる。すでにファイルがある日付は取りに行かない
    ——1回の実行が数十分かかり、締切（解約日）が動かない以上、再開が安いこと
    そのものが要件である。

    Args:
        directory: 名簿の置き場所。
        dates: 取りに行く日付。
        fetch: 日付を受けてその日の上場一覧を返す呼び出し。
        refetch: すでにファイルがある日付も取り直す。名簿の中身が変わった
            疑いがあるときだけ。

    Returns:
        :class:`HarvestReport`。1日ぶんの失敗で全体を止めない——5年窓の外は
        必ず断られるので、断られること自体は異常ではない。
    """
    report = HarvestReport()
    for on in dates:
        path = snapshot_path(directory, on)
        if path.exists() and not refetch:
            report.reused.append(on)
            for profile in read_snapshot(path):
                report.profiles[profile.symbol] = profile
            continue
        try:
            profiles = fetch(on)
        except Exception as exc:  # noqa: BLE001 - 断られ方そのものが記録に値する
            report.refused[on] = f"{type(exc).__name__}: {exc}"
            logger.warning("Snapshot for %s refused: %s", on, exc)
            continue
        if not profiles:
            report.refused[on] = "空で返った"
            continue
        write_snapshot(directory, on, profiles)
        report.written.append(on)
        for profile in profiles:
            report.profiles[profile.symbol] = profile
    return report


def delistings(snapshots: dict[dt.date, set[str]]) -> list[tuple[dt.date, dt.date, list[str]]]:
    """隣り合う名簿の差＝その期間に消えた銘柄を返す。

    DB との差ではなく**名簿同士**の差を取る。DB は過去に取り込んだ分の集合で
    あって上場一覧ではないので、DB との差は「廃止」と「そもそも取っていない」
    を混ぜてしまう（実際に一度混ぜた）。
    """
    ordered = sorted(snapshots)
    return [
        (earlier, later, sorted(snapshots[earlier] - snapshots[later]))
        for earlier, later in zip(ordered, ordered[1:], strict=False)
    ]
