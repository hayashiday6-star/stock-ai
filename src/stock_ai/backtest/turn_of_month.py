"""#13「月替わり効果」— 月末から月初の数日と、それ以外の日の差。

**格言は、月末・月初は上がると言っている。** `docs/PREREG_TURN_OF_MONTH_JP.md`
に封印前の取り決めがある。

## #5・#8 の管には載せられない

`event_window` は**銘柄ごとのイベント**を扱う。月替わりは**市場全体の暦**なので、

- 同じ日のイベントは等加重で1つにまとめる規則により、**全銘柄が1観測に潰れる**
- 引く相手が市場なので、**市場全体を持つ位置から市場を引くと差が構成上 0 になる**

**管が合わないのではなく、引き算の対象が消える。** だからここは3本目の管
（日次の暦）になる。

## 観測の単位は「月替わり1回」

**日ごとにしない。** #5 で 1,827 件を 831 日と数え違えた形（`CLAUDE.md`
「独立な観測を、件数で数えない」）に近づく。月ごとに1つの数にまとめれば、
単位が揃い、月次の設計と読み比べられる。

```
1観測 = （その月の窓の中の日次リターンの合計）
      − （窓の中の日数 × 窓の外の平均日次リターン）
```

**窓の外は、前回の窓が終わってから今回の窓が始まるまで**である。こう切ると、
**どの営業日もちょうど1つの窓か1つの窓外に属する**——重なりも隙間も無い。

## 対照は、本物の窓を見てはいけない

**偽の窓の「窓の外」は、構成上かならず本物の窓をまたぐ。** 偽の窓は本物を
避けて置かれるので、`前の偽窓の終わり+1 〜 今回の偽窓の始まり-1` という区間に
本物の月替わりが必ず入る。

すると本物の効果が**引き算する側**に混ざり、「何も無いときの分布」にならない。
しかも**混ざる向きから本物の符号が逆算できてしまう**ので、§0 の前に答えを
見ることになる（2026-09-19 に気付いて止めた）。

`exclude` は**そのためだけの口**である。対照は「ふつうの日」だけを見る。

## 加重も生存バイアスも、ここでは偏りを作らない

#5・#8 は**等加重のバスケットから時価総額加重の指数を引いて**いたので、加重差が
そのまま下駄になった（2026-09-18 に +0.22%/件 と測った）。

**ここは同じ系列の中で日どうしを比べる。** 加重は効果の大きさを変えるだけで、
**無いものを在るように見せることはできない。** 上場廃止の扱いも窓の内外で同じ
である。

## 窓は4営業日で、祝日でも欠けない

事前登録 §0 に「祝日で4日に満たない月がある」と書いたが、**間違いだった。**
窓は「最終営業日＋翌月の3営業日」で、**営業日で数えている。** 祝日は暦の幅を
伸ばすだけで、営業日の数は減らない。**欠けるのは系列の終わりだけ**である。

それでも**窓の日数は毎回数えて出す。** 書いた前提は、成り立つことを確かめる
（`CLAUDE.md`）。
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Sequence

import numpy as np
import pandas as pd

from stock_ai.backtest import tails
from stock_ai.core.logging import get_logger

logger = get_logger(__name__)

#: 窓の始まり。**月の最終営業日**（−1）。事前登録 §3 で固定した。
WINDOW_BEFORE = 1

#: 窓の終わり。**翌月3営業日目**（+3）。同上。
WINDOW_AFTER = 3

#: 窓の長さ（営業日）。**祝日では減らない。**
WINDOW_DAYS = WINDOW_BEFORE + WINDOW_AFTER

#: 2009年より前は使わない。**#9・#12 と揃える**（読み比べられるようにするため）。
USABLE_FROM = dt.date(2009, 1, 1)


@dataclasses.dataclass
class TurnOfMonthSeries:
    """月替わり1回ごとの差。**単位は「1月替わりあたり」。**"""

    source: str
    """何の日次リターンを見たか（`1306` か `universe`）。"""

    months: list[dt.date]
    """その月替わりの基準日（月の最終営業日）。"""

    episodes: list[float]
    """窓の中の合計 − 日数を揃えた窓の外の平均。"""

    window_days: list[int]
    outside_days: list[int]
    skipped_no_outside: int
    """窓の外の日が1日も無くて捨てた月替わり。"""

    skipped_short_window: int
    """窓が `WINDOW_DAYS` に満たなくて捨てた月替わり。**系列の終わりだけのはず。**"""

    def worst_month(self) -> float:
        """いちばん悪かった月替わり。**式は `tails` に1つだけ置いてある。**"""
        return tails.worst(self.episodes)

    def left_tail(self, share: float = tails.DEFAULT_TAIL_SHARE) -> float:
        """下位 ``share`` の月替わりの平均。**1点ではなく帯で見る。**

        Args:
            share: 下から取る割合。

        Returns:
            下位の平均。

        Raises:
            ValueError: ``share`` が 0〜1 の外。
        """
        return tails.left_tail(self.episodes, share)

    def hit_rate(self) -> float:
        """差が正だった月替わりの割合。**平均だけで語らない。**"""
        return tails.hit_rate(self.episodes)

    def summary(self) -> str:
        """1行のまとめ。**平均は出さない**——§0 が判定を先食いしないため。"""
        if not self.months:
            return "月替わりを1回も作れなかった。**比べていない。**"
        return (
            f"{self.source}: {len(self.months)} 回"
            f"（{self.months[0]} 〜 {self.months[-1]}）。"
            f"窓は {int(np.mean(self.window_days))} 営業日、"
            f"窓の外は 1回あたり {int(np.mean(self.outside_days))} 営業日。"
            f"窓の外が無くて捨てた {self.skipped_no_outside}、"
            f"窓が短くて捨てた {self.skipped_short_window}。"
        )

    def warnings(self) -> list[str]:
        """気付かなくても目に入るべきこと。**表は読む側が気付く必要がある。**"""
        found: list[str] = []
        if not self.months:
            return ["**月替わりを1回も作れなかった。**"]
        odd = [days for days in self.window_days if days != WINDOW_DAYS]
        if odd:
            found.append(
                f"**窓が {WINDOW_DAYS} 営業日でない月替わりが {len(odd)} 回ある。** "
                "事前登録は営業日で数えると書いてある——**祝日では減らないはず。**"
            )
        if self.skipped_short_window:
            found.append(
                f"**窓が短くて {self.skipped_short_window} 回捨てた。** "
                "系列の終わりだけのはずである。"
            )
        if self.skipped_no_outside:
            found.append(f"**窓の外が無くて {self.skipped_no_outside} 回捨てた。**")
        return found


def daily_returns(closes: Sequence[float]) -> list[float]:
    """終値から日次リターンを作る。**最初の日は作れないので落とす。**

    Args:
        closes: 調整後終値。

    Returns:
        ``closes`` より1つ短い並び。取れない日は ``nan``。
    """
    found: list[float] = []
    for before, after in zip(closes[:-1], closes[1:], strict=True):
        if before > 0 and after > 0:
            found.append(after / before - 1.0)
        else:
            found.append(float("nan"))
    return found


def build_series(  # noqa: PLR0913 - 事前登録が固定した条件をすべて受け取る
    returns: Sequence[float],
    dates: Sequence[dt.date],
    month_ends: Sequence[int],
    source: str = "1306",
    start: dt.date | None = None,
    end: dt.date | None = None,
    windows: Sequence[tuple[int, int]] | None = None,
    exclude: frozenset[int] | None = None,
) -> TurnOfMonthSeries:
    """月替わり1回ごとの差を作る。

    Args:
        returns: 日次リターン（``dates`` と同じ長さ）。
        dates: その日付。
        month_ends: 月の最終営業日の位置。**`lowvol_census.formation_dates`
            が返すもの**——月の切れ目を2通り持たない。
        source: 何を見たかの札。
        start: この日より前の月替わりを使わない。既定は 2009-01-01。
        end: **この日より後のデータを1つも使わない。** 窓の終わりまで見る。
        windows: ``(開始位置, 長さ)`` を月替わりごとに差し替える。
            **陰性対照のためだけの口である。** 渡さなければ事前登録どおり。
        exclude: 窓からも窓の外からも外す位置。**これも対照のためだけ。**
            偽の窓の「窓の外」は**構成上かならず本物の窓をまたぐ**ので、
            渡さないと本物の効果が引き算する側に混ざる（2026-09-19）。

    Returns:
        :class:`TurnOfMonthSeries`。

    Raises:
        ValueError: ``returns`` と ``dates`` の長さが違う、または月末が足りない。
    """
    if len(returns) != len(dates):
        raise ValueError(f"returns {len(returns)} と dates {len(dates)} の長さが違う。")
    if len(month_ends) < 2:
        raise ValueError("月末が2つ未満。月替わりを作れない。")
    if windows is not None and len(windows) != len(month_ends):
        raise ValueError(f"windows {len(windows)} と month_ends {len(month_ends)} の数が違う。")

    floor = USABLE_FROM if start is None else max(start, USABLE_FROM)

    months: list[dt.date] = []
    episodes: list[float] = []
    window_lengths: list[int] = []
    outside_lengths: list[int] = []
    no_outside = 0
    short_window = 0

    for index, position in enumerate(month_ends):
        if index == 0:
            continue  # 前の窓が無いと、窓の外を切り出せない
        begin, length = windows[index] if windows is not None else (position, WINDOW_DAYS)
        last = begin + length - 1
        if last >= len(returns):
            short_window += 1
            continue
        if dates[begin] < floor:
            continue
        if end is not None and dates[last] > end:
            continue

        # **窓の外は、前の窓が終わってから今回の窓が始まるまで。**
        # こう切れば、どの営業日もちょうど1つの区間に属する。
        previous = month_ends[index - 1]
        previous_end = (
            windows[index - 1][0] + windows[index - 1][1] - 1
            if windows is not None
            else previous + WINDOW_DAYS - 1
        )
        # **除外は窓にも窓の外にも同じだけ当てる。** 片方だけ外すと、
        # 外した日が「無かったこと」ではなく「相手側に寄った」ことになる。
        # **内包表記の変数を外側と同じ名前にしない。** Python では別の束縛に
        # なるので動くが、読む側が「上書きされた」と読み違える。
        skip = exclude or frozenset()
        outside = [
            returns[day]
            for day in range(previous_end + 1, begin)
            if day not in skip and not np.isnan(returns[day])
        ]
        inside = [
            returns[day]
            for day in range(begin, last + 1)
            if day not in skip and not np.isnan(returns[day])
        ]
        if not outside:
            no_outside += 1
            continue
        if len(inside) < length:
            short_window += 1
            continue

        months.append(dates[position])
        episodes.append(float(sum(inside) - len(inside) * float(np.mean(outside))))
        window_lengths.append(len(inside))
        outside_lengths.append(len(outside))

    logger.info(
        "#13 月替わり（%s）: %d 回、窓の外なし %d、窓が短い %d",
        source,
        len(months),
        no_outside,
        short_window,
    )
    return TurnOfMonthSeries(
        source=source,
        months=months,
        episodes=episodes,
        window_days=window_lengths,
        outside_days=outside_lengths,
        skipped_no_outside=no_outside,
        skipped_short_window=short_window,
    )


def series_from_prices(
    frame: pd.DataFrame,
    source: str,
    start: dt.date | None = None,
    end: dt.date | None = None,
) -> TurnOfMonthSeries:
    """調整後終値の表から、そのまま月替わりの系列を作る。

    **月の切れ目は `formation_dates` に聞く。** 2通り持たない。

    Args:
        frame: `split_adjusted` を通した価格（`close` 列と日付の索引）。
        source: 何を見たかの札。
        start: この日より前の月替わりを使わない。
        end: この日より後のデータを1つも使わない。

    Returns:
        :class:`TurnOfMonthSeries`。

    Raises:
        ValueError: 価格が無い。
    """
    from stock_ai.backtest.lowvol_census import formation_dates
    from stock_ai.data.schema import CLOSE

    if frame.empty:
        raise ValueError(f"{source} の価格が無い。")

    closes = frame[CLOSE].to_numpy(dtype=float)
    returns = daily_returns(closes)
    # **リターンは1日短い。** 日付も揃えて落とす——ずらすと窓が1日ずれる。
    dates = [stamp.date() for stamp in frame.index[1:]]
    month_ends = [position - 1 for position in formation_dates(frame.index) if position >= 1]
    return build_series(returns, dates, month_ends, source=source, start=start, end=end)
