"""2つの経路を突き合わせるとき、**重なる期間だけを見る。**

## なぜ1か所にまとめてあるか

**同じ形を3度踏んだ**（2026-09-14〜15）。

| | 何が起きたか |
|---|---|
| 項目5 | カレンダーの立会日と名簿を全期間で比べ、「名簿の無い立会日」が数千日出た |
| 項目6 | 同じ形で、名簿の始まる前が全部「欠け」に落ちた |
| 名簿の突き合わせ | 立会日なのに名簿が無い日が2日ある、と誤報した（2008-02-12、2008-03-13） |

どれも**欠けているのではなく、片方がそこまで始まっていない**だけだった。

**3度とも、気付いたのは数字を見たあとである。** 書いている最中に気付いた回は
一度も無い。だから注意ではなく、**関数にした。**

## 「比べていない」と「無い」は別である

範囲の外に落ちたものを黙って捨てない。:class:`Overlap` は、外に落ちた件数を
**持ったまま**返す。捨てると、比べていないことが「無い」に見える。
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Iterable


@dataclasses.dataclass
class Overlap:
    """2つの日付の集まりが、両方とも覆っている範囲。"""

    first: dt.date | None
    """重なりの始まり。重なりが無ければ ``None``。"""

    last: dt.date | None
    common: set[dt.date]
    """範囲の中で、両方にある日付。"""

    left_only: set[dt.date]
    """範囲の中で、左にしか無い日付。**ここが本当の食い違いである。**"""

    right_only: set[dt.date]
    before: set[dt.date]
    """範囲より前にあり、比べていない日付。**欠けではない。**"""

    after: set[dt.date]

    @property
    def compared(self) -> int:
        """実際に比べた日数。"""
        return len(self.common | self.left_only | self.right_only)

    @property
    def skipped(self) -> int:
        """範囲の外なので比べなかった日数。**0 と混ぜない。**"""
        return len(self.before | self.after)

    def summary(self) -> str:
        """1行のまとめ。**比べた範囲を必ず言う。**"""
        if self.first is None:
            return "重なる期間が無い。**比べていない。**"
        return (
            f"重なる {self.first} 〜 {self.last} の {self.compared:,} 日を比べた"
            f"（一致 {len(self.common):,}、左だけ {len(self.left_only):,}、"
            f"右だけ {len(self.right_only):,}）。"
            + (f"範囲の外の {self.skipped:,} 日は比べていない。" if self.skipped else "")
        )


def overlapping(left: Iterable[dt.date], right: Iterable[dt.date]) -> Overlap:
    """両方が覆っている期間だけで突き合わせる。

    範囲は **それぞれの最初と最後の、内側どうし** で決める（``max`` の始まりと
    ``min`` の終わり）。片方だけの端を使うと、もう片方が始まる前が全部
    「欠け」に落ちる。

    **範囲の外を捨てない。** :attr:`Overlap.before` と :attr:`Overlap.after` に
    残す。件数が見えていれば、「比べていない」を「無い」と読み違えずに済む。

    Args:
        left: 片方の日付。
        right: もう片方の日付。

    Returns:
        :class:`Overlap`。どちらかが空なら、重なりは無く全部が範囲の外になる。
    """
    a, b = set(left), set(right)
    if not a or not b:
        return Overlap(None, None, set(), set(), set(), a | b, set())

    first = max(min(a), min(b))
    last = min(max(a), max(b))
    if first > last:
        # 期間がまったく重なっていない。**片方が「全部欠け」に見える形。**
        return Overlap(None, None, set(), set(), set(), a | b, set())

    def inside(days: set[dt.date]) -> set[dt.date]:
        return {day for day in days if first <= day <= last}

    a_in, b_in = inside(a), inside(b)
    outside = (a | b) - a_in - b_in
    return Overlap(
        first=first,
        last=last,
        common=a_in & b_in,
        left_only=a_in - b_in,
        right_only=b_in - a_in,
        before={day for day in outside if day < first},
        after={day for day in outside if day > last},
    )
