"""日次自動化が走らなかった日を数える。**出力からは気付けなかった。**

2026-09-19 にユーザーが `logs/` を手で数えて分かった——`logs/daily` に 4日、
`logs/accumulation` に 2日の穴があった。

**「土日だから」ではない。** 09-12(土)・09-13(日)・09-19(土) は走っている。
そして **09-16 は同じ日の 07:02 に accumulation が走っている**ので PC は
動いていた。06:00 の daily だけがログを1行も残していない。

## なぜ気付けなかったか

`Get-ScheduledTaskInfo` は両方 `LastTaskResult: 0` と出る。**それは「最後に
走った回」の話で、走らなかった回は数に入らない。** 走らなければ結果も残らない
ので、**「異常なし」の顔をしたまま4日ぶん抜ける。**

**無いことは、出力に出ない。** 出すには、**在るべき日を先に決めて引き算する**
しかない。

## 数え方

- **`daily` と `accumulation` を独立に数える。** 片方が埋まっていると、
  もう片方の穴が「異常なし」に化ける（#5 で踏んだ形）
- **ファイルが無い日と、在るが空の日を別に数える。** `0` を「読めた」と
  読まない
- **最初のログより前は数えない。** 自動化を入れる前を穴と呼ばない
- **今日は数えない。** まだ走っていない時刻かもしれない
- **毎日走る前提である。** 土日も走っていることが実際のログで確かめられて
  いるので、平日だけに絞らない
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import pathlib
import re

from stock_ai.core.logging import get_logger

logger = get_logger(__name__)

#: 何日ぶん遡って見るか。
#:
#: 30 は「1ヶ月ぶん見れば、気付かないまま過ぎた分が拾える」という目安で、
#: **測って決めた値ではない。** そう書いておく。
DEFAULT_WINDOW_DAYS = 30

#: 見るログのフォルダ。**独立に数える。**
#:
#: `scripts\\4-daily.ps1` が `logs\\daily`、`scripts\\7-accumulation-daily.ps1` が
#: `logs\\accumulation` に日付つきで書く。**片方だけ見ると、もう片方の穴が
#: 黙って通る。**
WATCHED = ("daily", "accumulation")

#: ログの名前。``2026-09-16.log``。
_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})\.log$")


@dataclasses.dataclass(frozen=True)
class Coverage:
    """1つのフォルダについて、在るべき日と在る日の差。"""

    name: str
    window_from: dt.date
    window_to: dt.date
    missing: tuple[dt.date, ...]
    """ログが1件も無い日。**走らなかった日である。**"""

    empty: tuple[dt.date, ...]
    """ログは在るが、中身が空の日。**`0` を「読めた」と読まないため別に数える。**"""

    seen: int
    first_log: dt.date | None
    """いちばん古いログの日。**無ければ `None`**（0 日ではない）。"""

    def summary(self) -> str:
        """1行。**穴が無いときはこれで終わる。**"""
        if self.first_log is None:
            return f"{self.name}: ログが1件も無い"
        span = (self.window_to - self.window_from).days + 1
        if not self.missing and not self.empty:
            return f"{self.name}: 直近 {span} 日、穴なし"
        parts = []
        if self.missing:
            parts.append(f"走っていない {len(self.missing)} 日")
        if self.empty:
            parts.append(f"空 {len(self.empty)} 日")
        return f"{self.name}: 直近 {span} 日で " + "、".join(parts)

    def warnings(self) -> list[str]:
        """気付かなくても目に入るべきこと。**表の1行にしない。**"""
        found: list[str] = []
        if self.first_log is None:
            return [f"**{self.name} のログが1件も無い。** 自動化が動いていない。"]
        # **2つを1つの警告で守らない。** 片方が通ったときに黙る。
        if self.missing:
            found.append(
                f"**{self.name} が {len(self.missing)} 日走っていない**: "
                + "、".join(day.isoformat() for day in self.missing)
            )
        if self.empty:
            found.append(
                f"**{self.name} のログが {len(self.empty)} 日ぶん空である**: "
                + "、".join(day.isoformat() for day in self.empty)
            )
        return found


def survey(
    logs: pathlib.Path,
    name: str,
    today: dt.date | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> Coverage:
    """``logs/<name>`` に日付の穴が無いかを数える。

    Args:
        logs: `logs` ディレクトリ。
        name: その下のフォルダ名（`daily` など）。
        today: 今日。**今日は数えない。** 省くとシステムの今日。
        window_days: 何日ぶん遡るか。

    Returns:
        :class:`Coverage`。

    Raises:
        ValueError: ``window_days`` が 1 未満。
    """
    if window_days < 1:
        raise ValueError(f"window_days must be at least 1; got {window_days}.")

    now = today or dt.date.today()
    where = logs / name
    found: dict[dt.date, int] = {}
    if where.is_dir():
        for path in where.iterdir():
            match = _NAME.match(path.name)
            if match is None:
                continue
            try:
                day = dt.date.fromisoformat(match.group(1))
            except ValueError:
                # **読めない名前は数えない。** 穴でもなければ、走った証拠でもない。
                continue
            found[day] = len(path.read_text(encoding="utf-8", errors="replace").strip())

    first = min(found) if found else None
    # **今日は数えない。** まだ走っていない時刻かもしれない。
    last = now - dt.timedelta(days=1)
    # **自動化を入れる前を穴と呼ばない。**
    begin = now - dt.timedelta(days=window_days)
    if first is not None:
        begin = max(begin, first)

    missing: list[dt.date] = []
    empty: list[dt.date] = []
    if first is not None:
        day = begin
        while day <= last:
            size = found.get(day)
            if size is None:
                missing.append(day)
            elif size == 0:
                empty.append(day)
            day += dt.timedelta(days=1)

    logger.info(
        "ログの穴（%s）: 走っていない %d 日、空 %d 日",
        name,
        len(missing),
        len(empty),
    )
    return Coverage(
        name=name,
        window_from=begin,
        window_to=last,
        missing=tuple(missing),
        empty=tuple(empty),
        seen=len(found),
        first_log=first,
    )


def survey_all(
    logs: pathlib.Path,
    names: tuple[str, ...] = WATCHED,
    today: dt.date | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> list[Coverage]:
    """見るフォルダすべてを、**独立に**数える。

    Args:
        logs: `logs` ディレクトリ。
        names: フォルダ名。
        today: 今日。
        window_days: 何日ぶん遡るか。

    Returns:
        フォルダごとの :class:`Coverage`。**渡した順のまま。**
    """
    return [survey(logs, name, today=today, window_days=window_days) for name in names]
