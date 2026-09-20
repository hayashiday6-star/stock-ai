"""#16「落ちるナイフをつかむな」— 急落した銘柄を買うと、その後も下回るか。

`docs/PREREG_KNIFE_JP.md` に封印前の取り決めがある。

## 窓が短いのは、格言がそう言っているから

「つかむな」も「dead cat bounce」も**急落した直後の数日**の話である。
20営業日は #15（窓は埋まる）の物差しで、ここの物差しではない。

**検出力の話ではない。** 短くすれば SD も重なりも減るが、**それは結果で
あって理由ではない**（事前登録 §0）。

## 向きは格言から取った

「つかむな」は**買うと損をする**と言っている。**#15 が負に出たことは根拠に
使っていない**——答えを見てから向きを決めれば、事前登録の意味が無くなる。

## 急落の定義が、別のものを拾っていないか

**調整漏れの分割・併合は、まさに「5営業日で −20%」に見える**（1:2 の分割は
−50%）。壁の下見の1回目で、これを外さずに SD 273%／イベント日 を出した。

**権利落ちも外す。ただし 0 が想定である**——20% の下げを配当では作れない。
それでも外すのは、特別配当のような大きいものが在れば**機械的な値下がりで
戻らない**からで、この説の事象としては偽物である。

## 急落の定義はここが正本

`wall.scan`（壁の下見）も同じ規則を呼ぶ。**2つ持つと、下見で選んだ設計と
判定に使う設計が、黙ってずれる。**
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Callable

import numpy as np

from stock_ai.backtest.discontinuity import crossings, session_breaks, spans_break
from stock_ai.backtest.gap_fill import IS_END, IS_FROM, OOS_END, known_ex_dates, liquid_bars
from stock_ai.backtest.pead import MIN_TURNOVER
from stock_ai.core.logging import get_logger
from stock_ai.data.schema import CLOSE, VOLUME, split_adjusted
from stock_ai.database.engine import Database

logger = get_logger(__name__)

#: 急落と呼ぶ幅。**壁の下見で使った値**（事前登録 §3 で固定）。
KNIFE_DROP = 0.20

#: 急落を測る営業日数。同上。
KNIFE_DAYS = 5

#: 保有営業日数。**格言が急落直後の話だから 5**（事前登録 §4）。
HOLDING = 5


@dataclasses.dataclass
class KnifeEvents:
    """集めた急落と、**外した件数の内訳。**"""

    events: list[tuple[str, dt.date]]
    days_is: int
    days_oos: int
    events_oos: int
    excluded_ex_date: int
    """権利落ちが急落の5日に入っていたので外した件数。**0 が想定である。**"""

    excluded_broken: int
    symbols: int
    thin: int
    """**急落だったが**流動性で外した件数。**銘柄日ではない。**"""

    def summary(self) -> str:
        """1行のまとめ。**平均は出さない**——§0 が判定を先食いしないため。"""
        if not self.events:
            return "急落を1件も拾えなかった。**比べていない。**"
        return (
            f"{self.symbols:,} 銘柄から、IS の急落 {len(self.events):,} 件"
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
            return ["**急落を1件も拾えなかった。**"]
        if self.excluded_ex_date:
            # **#15 とは逆で、0 でないほうが驚きである。**
            found.append(
                f"**権利落ちで {self.excluded_ex_date:,} 件外した。** "
                "20% の下げを配当では作れないはずなので、**特別配当か、"
                "`ExDate` の読み違いである。** 中身を見ること。"
            )
        if not self.days_oos:
            found.append("**OOS に急落が1日も無い。** 判定に使えない。")
        if self.days_is < 100:  # noqa: PLR2004 - 1% を落とせる最小の数
            found.append(f"**IS のイベント日が {self.days_is} しか無い。** 散らばりが粗い。")
        return found


def knife_positions(
    closes: np.ndarray,
    liquid: np.ndarray,
    drop: float = KNIFE_DROP,
    days: int = KNIFE_DAYS,
) -> np.ndarray:
    """急落した足の位置。**急落の定義はここが正本である。**

    Args:
        closes: 調整後の終値。
        liquid: その足が流動性の下限を満たすか。
        drop: 急落と呼ぶ幅。
        days: 何営業日で測るか。

    Returns:
        位置の配列。**先頭の ``days`` 本は前が足りないので入らない。**

    Raises:
        ValueError: 長さが揃っていない、または ``days`` が 1 未満。
    """
    if len(closes) != len(liquid):
        raise ValueError(f"closes {len(closes)} と liquid {len(liquid)} の長さが違う。")
    if days < 1:
        raise ValueError(f"days must be at least 1; got {days}.")
    if len(closes) <= days:
        return np.array([], dtype=int)

    before, after = closes[:-days], closes[days:]
    usable = (before > 0) & (after > 0)
    fell = np.zeros(len(before), dtype=bool)
    fell[usable] = after[usable] / before[usable] - 1.0 <= -drop
    return np.flatnonzero(fell & liquid[days:]) + days


def build_events(  # noqa: PLR0913 - 事前登録が固定した条件をすべて受け取る
    database: Database,
    announced: dict[str, list[tuple[dt.date, dt.date]]],
    symbols: list[str] | None = None,
    drop: float = KNIFE_DROP,
    days: int = KNIFE_DAYS,
    holding: int = HOLDING,
    min_turnover: float = MIN_TURNOVER,
    progress: Callable[[int, int], None] | None = None,
) -> KnifeEvents:
    """急落を集める。**権利落ちと不連続を外し、外した数を数える。**

    Args:
        database: 価格の保存先。
        announced: :func:`~stock_ai.data.jquants_dividend.ex_dates_known_by` の形。
        symbols: 対象銘柄。省くと JP の全銘柄。
        drop: 急落と呼ぶ幅。
        days: 急落を測る営業日数。
        holding: 保有営業日数。**窓の中に不連続が無いことを見るのに使う。**
        min_turnover: 流動性の下限（円）。
        progress: ``(済み, 全体)`` で呼ばれる。**1行に収めること。**

    Returns:
        :class:`KnifeEvents`。

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
            volumes = adjusted[VOLUME].to_numpy(dtype=float)
            when_of = [stamp.date() for stamp in adjusted.index]
            liquid = liquid_bars(closes, volumes, min_turnover)
            prefix = crossings(session_breaks(adjusted[CLOSE]))
            last = len(closes) - 1
            own = announced.get(symbol)

            # **絞りの前に数える。** 外した件数を、外す対象と同じ単位で出す。
            everything = np.ones(len(closes), dtype=bool)
            for index in knife_positions(closes, everything, drop, days):
                when = when_of[index]
                if when < IS_FROM or when > OOS_END:
                    continue
                if not liquid[index]:
                    thin += 1
                    continue
                # **急落の5日に権利落ちが入っていたら外す。** 0 が想定である。
                known = known_ex_dates(own, when)
                if known and any(
                    when_of[step] in known for step in range(max(index - days, 0), index + 1)
                ):
                    ex_dropped += 1
                    continue
                # **急落そのものと、その後の窓に不連続が無いこと。**
                if spans_break(prefix, max(index - days, 0), min(index + holding, last)):
                    broken_dropped += 1
                    continue
                if when <= IS_END:
                    events.append((symbol, when))
                    is_days.add(when)
                else:
                    oos_events += 1
                    oos_days.add(when)

    logger.info(
        "急落: IS %d 件（%d 日）、OOS %d 件（%d 日）",
        len(events),
        len(is_days),
        oos_events,
        len(oos_days),
    )
    return KnifeEvents(
        events=sorted(events, key=lambda row: (row[1], row[0])),
        days_is=len(is_days),
        days_oos=len(oos_days),
        events_oos=oos_events,
        excluded_ex_date=ex_dropped,
        excluded_broken=broken_dropped,
        symbols=read,
        thin=thin,
    )
