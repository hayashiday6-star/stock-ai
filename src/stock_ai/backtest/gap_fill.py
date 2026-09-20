"""#15「窓は埋まる」— 下に開いた窓の後、20営業日で宇宙を上回るか。

`docs/PREREG_GAP_FILL_JP.md` に封印前の取り決めがある。

## 事象の定義が、別のものを拾っていないか

**3% の下窓は、2つのものがそう見える。**

1. **権利落ち。** 配当の落ちは機械的な値下がりで、**埋まらない**（配当は
   支払われている）。外さなければ、事象の定義が配当を拾う
2. **調整漏れの分割・併合。** #6 で 8308 の 1:1000 併合を実際に見て確定した
   （`discontinuity`）。壁の下見の1回目で、これを外さずに SD 273%／イベント日
   を出した

**どちらも外す。** 外した件数は別々に数える——**片方にまとめると、どちらで
落ちたのか分からなくなる。**

## 先読みを入れない

窓は寄付きで開くので、**D の寄付きには間に合わない**——その日の始値そのものが
事象である。**入るのは D+1 の寄付き**（事前登録 §4）。

**権利落ちの除外にも先読みが入りうる。** 権利落ち日は前もって分かる情報だが、
**後から出た訂正を使えば先読みになる。** 使うのは `公表日 ≤ D` のものだけ。

## 窓の定義はここが正本

`wall.scan`（壁の下見）も同じ規則を呼ぶ。**2つ持つと、下見で選んだ設計と
判定に使う設計が、黙ってずれる。**
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from bisect import bisect_right
from collections.abc import Callable

import numpy as np
import pandas as pd

from stock_ai.backtest.discontinuity import crossings, session_breaks, spans_break
from stock_ai.backtest.fall import fell_at_least
from stock_ai.backtest.pead import MIN_TURNOVER, TURNOVER_WINDOW
from stock_ai.core.logging import get_logger
from stock_ai.data.schema import CLOSE, OPEN, VOLUME, split_adjusted
from stock_ai.database.engine import Database

logger = get_logger(__name__)

#: 下窓と呼ぶ幅。**始値が前日終値をこれだけ下回ったら1件。**
#:
#: **壁の下見で使った値である**（2026-09-19）。事前登録 §3 で固定した。
#: **何通りも並べて良いほうを採ると、選んだこと自体が多重検定になる**（#10）。
GAP_DOWN = 0.03

#: 保有営業日数。**#5 と同じ物差し**（事前登録 §4）。
HOLDING = 20

#: IS の期間。**2009年からではない**——`ExDate` が 2012-12-26 からしか無く、
#: 外せる期間と外せない期間を混ぜないため（事前登録 §6）。
IS_FROM = dt.date(2013, 1, 1)
IS_END = dt.date(2017, 12, 31)

#: OOS。**件数だけ数える。リターンは計算しない。**
OOS_FROM = dt.date(2018, 1, 1)
OOS_END = dt.date(2026, 8, 31)


@dataclasses.dataclass
class GapEvents:
    """集めた下窓と、**外した件数の内訳。**"""

    events: list[tuple[str, dt.date]]
    """IS の事象。``(銘柄, 窓が開いた日)``。"""

    days_is: int
    """IS の別々の日数。**独立な観測はこちらである。**"""

    days_oos: int
    """OOS の別々の日数。**判定に使える期数。** リターンは計算していない。"""

    events_oos: int
    excluded_ex_date: int
    """権利落ちなので外した件数（IS + OOS）。**0 なら、調整が既に配当を抜いている。**"""

    excluded_broken: int
    """不連続をまたぐので外した件数（IS + OOS）。"""

    symbols: int
    thin: int
    """**下窓だったが**流動性で外した件数。

    **銘柄日ではない。** 最初は全銘柄日の非流動を数えていて、隣に並ぶ
    「権利落ちで外した」「不連続で外した」と**単位が違っていた**
    （2026-09-19、1,300万 対 4,015）。`CLAUDE.md`「同じ列に、2つの単位を
    並べない」。
    """

    def summary(self) -> str:
        """1行のまとめ。**平均は出さない**——§0 が判定を先食いしないため。"""
        if not self.events:
            return "下窓を1件も拾えなかった。**比べていない。**"
        return (
            f"{self.symbols:,} 銘柄から、IS の下窓 {len(self.events):,} 件"
            f"（{self.days_is:,} 日）。OOS は {self.events_oos:,} 件"
            f"（**{self.days_oos:,} 日**）。"
            f"権利落ちで外した {self.excluded_ex_date:,} 件、"
            f"不連続で外した {self.excluded_broken:,} 件、"
            f"流動性で外した {self.thin:,} 件。"
        )

    def warnings(self) -> list[str]:
        """気付かなくても目に入るべきこと。**早期 return しない。**"""
        found: list[str] = []
        if not self.events:
            return ["**下窓を1件も拾えなかった。**"]
        if not self.excluded_ex_date:
            found.append(
                "**権利落ちで外した件数が 0 である。** 原本が無いか、"
                "**調整後の価格が既に配当を抜いている**かのどちらか。"
                "事前登録 §3 は前者を想定していない。"
            )
        if not self.days_oos:
            found.append("**OOS に下窓が1日も無い。** 判定に使えない。")
        if self.days_is < 100:  # noqa: PLR2004 - 1% を落とせる最小の数
            found.append(f"**IS のイベント日が {self.days_is} しか無い。** 散らばりが粗い。")
        return found


def gap_positions(
    opens: np.ndarray,
    closes: np.ndarray,
    liquid: np.ndarray,
    gap: float = GAP_DOWN,
) -> np.ndarray:
    """下窓が開いた足の位置。**窓の定義はここが正本である。**

    Args:
        opens: 調整後の始値。
        closes: 調整後の終値。
        liquid: その足が流動性の下限を満たすか（``opens`` と同じ長さ）。
        gap: 下窓と呼ぶ幅。

    Returns:
        位置の配列。**先頭の足は前日が無いので入らない。**

    Raises:
        ValueError: 長さが揃っていない。
    """
    if not len(opens) == len(closes) == len(liquid):
        raise ValueError(
            f"opens {len(opens)}・closes {len(closes)}・liquid {len(liquid)} の長さが違う。"
        )
    if len(opens) < 2:  # noqa: PLR2004 - 前日が無ければ窓は作れない
        return np.array([], dtype=int)

    # **線の当て方は `fall.fell_at_least` が正本。** #16 と同じ式を2つ持って
    # いて、**どちらも割り当てが「未満」になっていた**（2026-09-20）。
    fell = fell_at_least(closes[:-1], opens[1:], gap)
    return np.flatnonzero(fell & liquid[1:]) + 1


def liquid_bars(closes: np.ndarray, volumes: np.ndarray, floor: float) -> np.ndarray:
    """その日までの 20営業日の売買代金の中央値が ``floor`` 以上か。

    **#9・#12・#14 と同じ絞りである。** 揃えないと、壁を比べられない。
    """
    traded = pd.Series(closes * volumes)
    level = traded.rolling(TURNOVER_WINDOW + 1, min_periods=TURNOVER_WINDOW + 1).median()
    return (level >= floor).to_numpy(dtype=bool)


def known_ex_dates(
    announced: list[tuple[dt.date, dt.date]] | None,
    by: dt.date,
) -> set[dt.date]:
    """``by`` の時点で**既に公表されていた**権利落ち日。

    **後から出た訂正を使えば先読みになる**（事前登録 §8）。

    Args:
        announced: ``(公表日, 権利落ち日)`` の並び。**公表日の順**であること。
        by: この日までに公表されたものを採る。

    Returns:
        権利落ち日の集合。
    """
    if not announced:
        return set()
    cut = bisect_right(announced, (by, dt.date.max))
    return {when for _published, when in announced[:cut]}


def build_events(  # noqa: PLR0913 - 事前登録が固定した条件をすべて受け取る
    database: Database,
    announced: dict[str, list[tuple[dt.date, dt.date]]],
    symbols: list[str] | None = None,
    gap: float = GAP_DOWN,
    holding: int = HOLDING,
    min_turnover: float = MIN_TURNOVER,
    progress: Callable[[int, int], None] | None = None,
) -> GapEvents:
    """下窓を集める。**権利落ちと不連続を外し、外した数を数える。**

    Args:
        database: 価格の保存先。
        announced: :func:`~stock_ai.data.jquants_dividend.ex_dates_known_by` の形。
        symbols: 対象銘柄。省くと JP の全銘柄。
        gap: 下窓と呼ぶ幅。
        holding: 保有営業日数。**窓の中に不連続が無いことを見るのに使う。**
        min_turnover: 流動性の下限（円）。
        progress: ``(済み, 全体)`` で呼ばれる。**1行に収めること。**

    Returns:
        :class:`GapEvents`。

    Raises:
        ValueError: 銘柄が1つも無い。
    """
    from stock_ai.database.repository import PriceRepository, list_securities

    events: list[tuple[str, dt.date]] = []
    is_days: set[dt.date] = set()
    oos_days: set[dt.date] = set()
    oos_events = ex_dropped = broken_dropped = thin = read = 0

    with database.session() as session:
        if symbols is None:
            symbols = [sym for sym, market in list_securities(session) if market == "JP"]
        if not symbols:
            raise ValueError("銘柄が1つも無い。価格を取り込んでいない。")
        prices = PriceRepository(session)

        for position, symbol in enumerate(symbols, start=1):
            if progress is not None:
                progress(position, len(symbols))
            raw = prices.get_raw_prices(symbol)
            if raw.empty:
                continue
            read += 1
            adjusted = split_adjusted(raw)
            closes = adjusted[CLOSE].to_numpy(dtype=float)
            opens = adjusted[OPEN].to_numpy(dtype=float)
            volumes = adjusted[VOLUME].to_numpy(dtype=float)
            days = [stamp.date() for stamp in adjusted.index]
            liquid = liquid_bars(closes, volumes, min_turnover)
            prefix = crossings(session_breaks(adjusted[CLOSE]))
            last = len(closes) - 1
            own = announced.get(symbol)

            # **絞りの前に数える。** 外した件数を、外す対象と同じ単位
            # （＝下窓の件数）で出すため。
            everything = np.ones(len(opens), dtype=bool)
            for index in gap_positions(opens, closes, everything, gap):
                when = days[index]
                if when < IS_FROM or when > OOS_END:
                    continue
                if not liquid[index]:
                    thin += 1
                    continue
                # **権利落ちは、その日より前に公表されたものだけで外す。**
                if when in known_ex_dates(own, when):
                    ex_dropped += 1
                    continue
                # **窓の中に不連続があれば使わない。** 前日も見る。
                if spans_break(prefix, max(index - 1, 0), min(index + holding, last)):
                    broken_dropped += 1
                    continue
                if when <= IS_END:
                    events.append((symbol, when))
                    is_days.add(when)
                else:
                    oos_events += 1
                    oos_days.add(when)

    logger.info(
        "下窓: IS %d 件（%d 日）、OOS %d 件（%d 日）",
        len(events),
        len(is_days),
        oos_events,
        len(oos_days),
    )
    return GapEvents(
        events=sorted(events, key=lambda row: (row[1], row[0])),
        days_is=len(is_days),
        days_oos=len(oos_days),
        events_oos=oos_events,
        excluded_ex_date=ex_dropped,
        excluded_broken=broken_dropped,
        symbols=read,
        thin=thin,
    )
