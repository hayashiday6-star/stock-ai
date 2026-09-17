"""#8 増担保規制 — 件数センサス。**リターンを1つも計算しない。**

事前登録 `docs/PREREG_MARGIN_JP.md` §2 の表と、§3 の保有窓を埋めるための材料
である。**窓はここで機械的に決まる**——「規制が解けるまでの営業日数の中央値
（上限20営業日）」で、リターンを見ずに測れる。

**中央値を見てから窓を選び直さない。** :func:`window_from` が式そのものなので、
選び直すには関数を書き換えることになる。

### 打ち切りを黙って落とさない

規制が解けた日は「旗が下りた日」で読む。ところが**銘柄が原本から消えてしまう
ことがある**（日々公表の対象から外れれば、行そのものが出ない）。そのとき解除日
は**分からない**のであって、**長い**のでも**短い**のでもない。

**分からないものを落とすと、中央値は短い側に寄る。** 落とさずに数えて、割合を
警告に回す。
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Sequence
from pathlib import Path
from statistics import median

from stock_ai.backtest.event_window import event_returns
from stock_ai.core.logging import get_logger
from stock_ai.data.jquants_margin import REGULATION_RELEASED, MarginAlert, onsets

logger = get_logger(__name__)

#: 保有窓の上限（営業日）。#3・#6 と揃える。
#:
#: **中央値が極端に長いときに窓が発散しないため**でもある（解除されない銘柄が
#: 多い場合）。
MAX_WINDOW = 20

#: 打ち切りがこの割合を超えたら警告する。
#:
#: **数字に根拠は無い。** 「1割を超えたら中央値の意味が怪しい」という目安で
#: あって、測って出した値ではない。**そう書いておく。**
CENSORED_LIMIT = 0.10

#: 「上位1割の日」がイベント全体のどれだけを占めたら固まっていると言うか。
#:
#: **52週高値の件数センサスで 39% だった**（2026-09-05、`docs/NEXT_CANDIDATES.md`）。
#: そこを通した実績があるので、同じ線を置く。
CLUSTER_LIMIT = 0.40


@dataclasses.dataclass(frozen=True)
class Spell:
    """1回の規制。**発動から解除まで。**"""

    symbol: str
    onset: dt.date
    """発動の公表日（`PubDate`）。**売買できるのは翌営業日から。**"""

    released: dt.date | None
    """解除の公表日。**`None` は「解けていない」ではなく「分からない」。**"""

    last_seen: dt.date
    """その銘柄を原本で最後に見た日。打ち切りの長さの下限になる。"""

    @property
    def censored(self) -> bool:
        """解除日が分からないか。"""
        return self.released is None


def spells(alerts: Sequence[MarginAlert], reason: str = "Restricted") -> list[Spell]:
    """発動から解除までを1つずつ拾う。

    解除は**旗が下りた日**で読む。`TSEMrgnRegCls` が ``101``（規制解除）の日も
    解除とする。**どちらか早いほう。**

    Args:
        alerts: 期間ぶん。順序は問わない。
        reason: 見る旗。既定は増担保。

    Returns:
        発動日の昇順。
    """
    by_symbol: dict[str, list[MarginAlert]] = {}
    for item in alerts:
        by_symbol.setdefault(item.symbol, []).append(item)

    starts = onsets(list(alerts), reason=reason)
    found: list[Spell] = []
    for symbol, onset in starts:
        rows = sorted(by_symbol[symbol], key=lambda row: row.published)
        last_seen = rows[-1].published
        released: dt.date | None = None
        for item in rows:
            if item.published <= onset:
                continue
            down = reason not in item.reasons
            explicit = (item.regulation or "").strip() == REGULATION_RELEASED
            if down or explicit:
                released = item.published
                break
        found.append(Spell(symbol, onset, released, last_seen))
    return sorted(found, key=lambda spell: (spell.onset, spell.symbol))


def sessions_between(start: dt.date, stop: dt.date, calendar: Sequence[dt.date]) -> int | None:
    """``start`` から ``stop`` までの営業日数。暦は外から渡す。

    **暦日で数えない。** 保有窓は営業日で決めるので、暦日を混ぜると年末年始の
    発動だけ窓が短くなる。

    Returns:
        営業日数。どちらかが暦に無ければ ``None``。
    """
    index = {day: position for position, day in enumerate(calendar)}
    here, there = index.get(start), index.get(stop)
    if here is None or there is None:
        return None
    return there - here


def window_from(days: int | None) -> int:
    """保有窓 N。**中央値を見てから選び直さないための式。**

    事前登録 §3 の一行そのもの——「規制が解けるまでの営業日数の中央値、上限
    20営業日」。**人が当てはめると、封印前でも窓が動く。**

    Args:
        days: 解除までの営業日数の中央値。測れなければ ``None``。

    Returns:
        1 以上 :data:`MAX_WINDOW` 以下。測れなければ :data:`MAX_WINDOW`。
    """
    if days is None:
        return MAX_WINDOW
    return max(1, min(MAX_WINDOW, int(days)))


@dataclasses.dataclass(frozen=True)
class MarginCensus:
    """§2 の表と §3 の窓。**リターンは1つも入っていない。**"""

    events: int
    by_year: dict[int, int]
    first: dt.date | None
    last: dt.date | None
    days_with_events: int
    busiest_share: float
    """イベント件数の多い上位1割の日が、全体の何割を占めるか。"""

    same_day_median: int
    after_lending: int
    lending_unknown: int
    """貸借区分が読めなかったイベント。**「貸借でない」に数えない。**"""

    after_liquidity: int
    resolved: int
    censored: int
    release_days_median: int | None
    split_on: dt.date | None = None
    """IS と OOS の境。**この日までが IS。**"""

    events_is: int = 0
    """**絞り込んだ後の** IS のイベント数。見込みと分散を推定する側。"""

    events_oos: int = 0
    """**絞り込んだ後の** OOS のイベント数。

    **§0 の「検出できる差」はこれで計算する。** 全期間で計算すると、IS で
    推定した効果を全期間の検出力と比べることになる——**尺度の違う2つを
    組み合わせる、このプロジェクトが繰り返している形そのものである。**
    """

    @property
    def censored_share(self) -> float:
        """解除日が分からなかった割合。"""
        total = self.resolved + self.censored
        return self.censored / total if total else float("nan")

    @property
    def window(self) -> int:
        """保有窓 N。**式から出る。**"""
        return window_from(self.release_days_median)

    def summary(self) -> str:
        """1行のまとめ。"""
        if not self.events:
            return "発動イベントが1件も無い。**数えていない。**"
        return (
            f"発動 {self.events:,} 件（{self.first} 〜 {self.last}、"
            f"{self.days_with_events:,} 日）。"
            f"貸借に絞ると {self.after_lending:,} 件、流動性を通すと "
            f"{self.after_liquidity:,} 件。"
        )

    def warnings(self) -> list[str]:
        """気付かなくても目に入るべきこと。**表を読ませない。**"""
        found: list[str] = []
        if not self.events:
            found.append("**発動イベントが1件も無い。** 原本を読めていない可能性がある。")
            return found
        if self.busiest_share > CLUSTER_LIMIT:
            found.append(
                f"**上位1割の日が全体の {self.busiest_share:.0%} を占める。** "
                "同じ日に固まると独立な観測が減り、標準誤差が膨らむ。"
            )
        if self.censored_share > CENSORED_LIMIT:
            found.append(
                f"**解除日が分からないイベントが {self.censored_share:.0%} ある。** "
                "中央値は解けたものだけで出しているので、**短い側に寄っている。**"
            )
        if self.lending_unknown:
            found.append(
                f"貸借区分が読めなかったイベントが {self.lending_unknown:,} 件ある。"
                "**「貸借でない」には数えていない。**"
            )
        if self.events_oos and self.events_oos < 1_000:
            found.append(
                f"**OOS のイベントが {self.events_oos:,} 件しかない。** "
                "2026-09-05 に出した結論は「短い窓で、独立なイベントが 1,000 件"
                "以上あるもの」だった。**検出できる差がそのぶん大きくなる。**"
            )
        if self.after_lending < self.events // 2:
            found.append(
                f"**貸借に絞って {1 - self.after_lending / self.events:.0%} 落ちた。** "
                "空売りできない銘柄でショートを検証しないための絞りだが、"
                "**件数はここでいちばん減る。**"
            )
        if self.after_liquidity < self.after_lending:
            lost = self.after_lending - self.after_liquidity
            found.append(
                f"流動性の下限で {lost:,} 件落ちた"
                f"（{lost / self.after_lending:.0%}）。**イベント型は件数が命である。**"
            )
        return found


@dataclasses.dataclass
class LendingIndex:
    """日付ごとの貸借区分。**「いま貸借か」ではなく「その日に貸借だったか」。**

    2026-09-08 に踏んだ形がこれである——市場区分を「最後に見えた姿」で引いて、
    当時 TOKYO PRO だった銘柄を「スタンダード」と読んだ。**例外は出ず、
    もっともらしい値が出るだけだった。**

    名簿は変わった日だけ覚える。全日ぶん持つと、18年 × 4,000銘柄で持ちきれない。
    """

    changes: dict[str, list[tuple[dt.date, bool]]]
    covers: tuple[dt.date, dt.date] | None

    def __call__(self, symbol: str, on: dt.date) -> bool | None:
        """``on`` の時点で貸借銘柄だったか。**分からなければ `None`。**

        **`False` と `None` を混ぜない。** 名簿が届いていない日を「貸借でない」
        と読むと、その期間のイベントが黙って全部消える。
        """
        if self.covers is None or on < self.covers[0]:
            return None
        history = self.changes.get(symbol)
        if not history:
            return None
        found: bool | None = None
        for day, value in history:
            if day > on:
                break
            found = value
        return found


def _is_lending(text: str | None) -> bool | None:
    """名簿の `lending` の文字列から、貸借銘柄かを読む。

    J-Quants は `MarginCode`（`1` 信用 / `2` 貸借 / `3` 該当なし）と
    `MarginCodeName`（`信用銘柄` / `貸借銘柄` / `その他`）の両方を返しうる。
    **どちらで入っているかは名簿による。**

    **知らない中身を「貸借でない」と読まない。** `None` を返して数える。
    """
    value = (text or "").strip()
    if not value:
        return None
    if "貸借" in value or value == "2":
        return True
    if "信用" in value or "その他" in value or value in {"1", "3"}:
        return False
    return None


def lending_index(directory: Path) -> LendingIndex:
    """保存済みの名簿から :class:`LendingIndex` を組む。

    **変わった日だけ覚える。** 名簿を全部メモリに載せない。

    Args:
        directory: 名簿の置き場所。

    Returns:
        :class:`LendingIndex`。名簿が1枚も無ければ、いつでも ``None`` を返す。
    """
    from stock_ai.data.delisted import read_snapshot, snapshot_path, stored_dates

    days = stored_dates(directory)
    if not days:
        return LendingIndex({}, None)

    changes: dict[str, list[tuple[dt.date, bool]]] = {}
    for day in days:
        for profile in read_snapshot(snapshot_path(directory, day)):
            value = _is_lending(profile.lending)
            if value is None:
                continue
            history = changes.setdefault(profile.symbol, [])
            if not history or history[-1][1] != value:
                history.append((day, value))
    logger.info(
        "貸借区分: %d 銘柄、%s 〜 %s の名簿 %d 枚から",
        len(changes),
        days[0],
        days[-1],
        len(days),
    )
    return LendingIndex(changes, (days[0], days[-1]))


def busiest_share(counts: Sequence[int]) -> float:
    """上位1割の日が占める割合。**日数が10未満なら 1.0（全部が上位）。**"""
    if not counts:
        return float("nan")
    ordered = sorted(counts, reverse=True)
    top = max(1, len(ordered) // 10)
    return sum(ordered[:top]) / sum(ordered)


def census(  # noqa: PLR0913 - §2 が固定した絞り込みをすべて受け取る
    alerts: Sequence[MarginAlert],
    calendar: Sequence[dt.date],
    lending_on: object = None,
    liquid_on: object = None,
    start: dt.date | None = None,
    end: dt.date | None = None,
    split_on: dt.date | None = None,
    reason: str = "Restricted",
) -> MarginCensus:
    """§2 の表を埋める。**リターンを1つも計算しない。**

    Args:
        alerts: 原本ぶん。
        calendar: 営業日。解除までの日数を数えるのに使う。
        lending_on: ``(symbol, date) -> bool | None`` 。``None`` は**読めなかった**
            という意味で、「貸借でない」ではない。省略すると絞らない。
        liquid_on: ``(symbol, date) -> bool`` 。省略すると絞らない。
        start: この日より前の発動を数えない。
        end: この日より後の発動を数えない。
        split_on: IS と OOS の境。省略すると、覆う期間の**真ん中**。
        reason: 見る旗。

    Returns:
        :class:`MarginCensus`。
    """
    found = [
        spell
        for spell in spells(alerts, reason=reason)
        if (start is None or spell.onset >= start) and (end is None or spell.onset <= end)
    ]
    if not found:
        return MarginCensus(0, {}, None, None, 0, float("nan"), 0, 0, 0, 0, 0, 0, None)

    # **境は「原本が覆う期間の前半／後半」**（事前登録 §6）。**件数の半分では
    # ない。** 件数で割ると、イベントの多い時期がそのまま境を動かす。
    covered = (found[0].onset, found[-1].onset)
    split = split_on or covered[0] + (covered[1] - covered[0]) / 2

    per_day: dict[dt.date, int] = {}
    by_year: dict[int, int] = {}
    for spell in found:
        per_day[spell.onset] = per_day.get(spell.onset, 0) + 1
        by_year[spell.onset.year] = by_year.get(spell.onset.year, 0) + 1

    lending: list[Spell] = []
    unknown = 0
    for spell in found:
        if lending_on is None:
            lending.append(spell)
            continue
        verdict = lending_on(spell.symbol, spell.onset)
        if verdict is None:
            unknown += 1
            continue
        if verdict:
            lending.append(spell)

    liquid = [
        spell for spell in lending if liquid_on is None or liquid_on(spell.symbol, spell.onset)
    ]

    # **窓は、絞り込んだ後の束で測る。** 絞る前で測ると、実際には使わない
    # イベントが窓を決めることになる。
    durations = [
        sessions_between(spell.onset, spell.released, calendar)
        for spell in liquid
        if spell.released is not None
    ]
    resolved = [value for value in durations if value is not None and value > 0]
    censored = sum(1 for spell in liquid if spell.censored)

    result = MarginCensus(
        events=len(found),
        by_year=dict(sorted(by_year.items())),
        first=found[0].onset,
        last=found[-1].onset,
        days_with_events=len(per_day),
        busiest_share=busiest_share(list(per_day.values())),
        same_day_median=int(median(per_day.values())),
        after_lending=len(lending),
        lending_unknown=unknown,
        after_liquidity=len(liquid),
        resolved=len(resolved),
        censored=censored,
        release_days_median=int(median(resolved)) if resolved else None,
        split_on=split,
        events_is=sum(1 for spell in liquid if spell.onset <= split),
        events_oos=sum(1 for spell in liquid if spell.onset > split),
    )
    logger.info(
        "増担保センサス: 発動 %d 件、貸借 %d 件、流動性通過 %d 件、窓 %d 営業日",
        result.events,
        result.after_lending,
        result.after_liquidity,
        result.window,
    )
    return result


#: **正本は `event_window` にある。** 呼ぶ側で書き直さない。
#:
#: #5（上方修正）が同じ形を使うので、説をまたいで1つだけ置いた。ここは名前を
#: 残すためだけの再輸出である。
__all__ = ["event_returns"]
