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

**権利落ちも外す。** 特別配当のような大きいものが在れば**機械的な値下がりで
戻らない**ので、この説の事象としては偽物である。

**「0 が想定」と書いたが、実データは 819 件だった**（2026-09-20）。予想が
外れたのは、**配当が 20% を作る必要が無い**からである——除外の窓は急落の
急落を作った5営業日なので、**19% 下げた銘柄を線の向こうに押し出せば足りる。**
しかも
「20% 下げた」で絞る時点で、押し出された側が選ばれる。

**そのうち何件が「外すべきでなかった」かは `ex_date_audit` が見る。**

## 外す窓は、下げを作った日だけ

**基準日に落ちた配当は、比を1つも動かさない**——`closes[index]` と
`closes[index - days]` のどちらにも同じだけ乗るからである。**外す理由に
なりえない日**を窓に入れていて、実データで 158 件をそれで外していた
（2026-09-20）。

**そして順序が逆だった**（2026-09-20、ユーザーが指摘）。§3 が言っているのは
「機械的な値下がりを外す」で、「権利落ちが窓に在れば外す」は**代理**である。
実データで、**外すために置いた規則が本物の急落を 189 件巻き込んでいた。**

**先に配当を落としてから −20% を当てる。** そうすれば配当が線の向こうに
押し出すことが起きないので、**外す規則そのものが要らない。**

**残るのは、権利落ち日までに額が引けなかった権利落ちだけ**——調整のしようが
無い。そこは外す。**急落側と保有側の両方を見る**——ショートは配当を払う側
なので、片側だけ外すと非対称が残る。

**「額が一度も公表されていない」ではない**（2026-09-21 に直した）。後から
公表されているものが含まれる——その日の時点で使えなかっただけである。
**札が、数えているものと違うことを言っていた。**

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
from stock_ai.backtest.fall import fell_at_least
from stock_ai.backtest.gap_fill import IS_END, IS_FROM, OOS_END, known_ex_dates, liquid_bars
from stock_ai.backtest.pead import MIN_TURNOVER
from stock_ai.core.logging import get_logger
from stock_ai.data.schema import (
    CLOSE,
    VOLUME,
    DividendAdjustment,
    dividend_adjusted,
    split_adjusted,
)
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

    dividends: DividendAdjustment = dataclasses.field(default_factory=DividendAdjustment)
    """落とした配当の内訳。**当てた数と、当てなかった理由。**

    以前は :func:`~stock_ai.data.schema.dividend_adjusted` が黙って飛ばして
    いたので、**尺度を間違えていることが出力からは見えなかった**
    （2026-09-20）。ここに出しておけば、次は静かに増えない。
    """

    ex_date_events: list[tuple[str, dt.date]] = dataclasses.field(default_factory=list)
    """権利落ちで外した急落そのもの。**中身を見るために持って返る。**

    「中身を見ること」と警告に書いておきながら、**見る道具が無かった**
    （2026-09-20、ユーザーが指摘）。件数だけ返すと、外したものが何だったか
    を後から調べられない。
    """

    def __post_init__(self) -> None:
        """外した件数と、外したものの数が合うこと。

        **数と中身を別々に持つと、片方だけ直したときに黙ってずれる。**
        `ExDateCoverage` と同じ作りである。

        Raises:
            ValueError: 件数と中身の数が合わない。
        """
        if self.ex_date_events and len(self.ex_date_events) != self.excluded_ex_date:
            raise ValueError(
                f"権利落ちで外した件数 {self.excluded_ex_date} と、"
                f"持って返った {len(self.ex_date_events)} 件が合わない。"
            )

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
        # **配当の内訳は、急落が 0 件でも出す。** 落とせなかった理由は急落の
        # 有無と関係がない。下に早期 return が在るので、**その上に置く**
        # ——`Alignment.warnings()` で、足した検査が先頭の return に黙らされた
        # （2026-09-20）。
        found.extend(self.dividends.warnings())
        if not self.events:
            found.append("**急落を1件も拾えなかった。**")
            return found
        if self.excluded_ex_date:
            # **#15 とは逆で、0 でないほうが驚きである。** ただし、驚く理由を
            # 決め打たない——**最初そう書いて、説明を1つ書き落としていた**
            # （2026-09-20）。**配当が 20% を作る必要は無い。** 除外の窓は
            # 急落を作った5営業日なので、**19% 下げた銘柄を線の向こうに
            # 押し出せば足りる。** しかも「20% 下げた」で絞る時点で、
            # 押し出された側が選ばれる。
            found.append(
                f"**額を落とせない権利落ちで {self.excluded_ex_date:,} 件外した。** "
                "**額が公表されていれば、外さずに価格から落としている。** "
                "ここに残るのは**権利落ち日までに額が引けなかった権利落ち**だけで、"
                "調整のしようが無いぶんである。**「一度も公表されていない」では"
                "ない**——後から出ているものが含まれる。"
                "`checks\\権利落ちの日は合っているか.bat` が2つに割って数える。"
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

    # **線の当て方は `fall.fell_at_least` が正本。** ここで書き直さない
    # ——`a/b - 1.0 <= -drop` は割り算の丸めで**ちょうど −20% を落とす**
    # （2026-09-20、ユーザーが 2461 の 680→544 で見つけた）。
    fell = fell_at_least(closes[:-days], closes[days:], drop)
    return np.flatnonzero(fell & liquid[days:]) + days


def build_events(  # noqa: PLR0913 - 事前登録が固定した条件をすべて受け取る
    database: Database,
    announced: dict[str, list[tuple[dt.date, dt.date]]],
    rates: dict[str, list[tuple[dt.date, dt.date, float]]] | None = None,
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
        announced: :func:`~stock_ai.data.jquants_dividend.ex_dates_known_by` の
            ``by_symbol``。**額を落とせなかった権利落ちを外すのに使う。**
        rates: :func:`~stock_ai.data.jquants_dividend.ex_dividends_known_by` の形。
            **価格から配当を落とすのに使う。** 渡さなければ落とさない。
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
    excluded: list[tuple[str, dt.date]] = []
    is_days: set[dt.date] = set()
    oos_days: set[dt.date] = set()
    oos_events = ex_dropped = broken_dropped = thin = read = 0
    dividends = DividendAdjustment()

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
            # **配当を落としてから −20% を当てる。** 順序が逆だと、配当が線の
            # 向こうに押し出した下げまで事象になる（2026-09-20、ユーザーが
            # 指摘）。**外す規則は代理であって、§3 の「機械的な値下がりを
            # 外す」そのものではなかった。**
            paid = (rates or {}).get(symbol)
            plain = split_adjusted(raw)
            # **割る相手は調整前の終値。** 調整後で割ると、分割より前の
            # 権利落ちが分割比のぶん余計に落ちる（2026-09-20 に再現）。
            netted, counted = dividend_adjusted(
                plain, paid, base=raw[CLOSE].to_numpy(dtype=float), symbol=symbol
            )
            dividends = dividends + counted
            # **下げの判定だけ、配当を落とした値で行う。**
            closes = netted[CLOSE].to_numpy(dtype=float)
            volumes = plain[VOLUME].to_numpy(dtype=float)
            when_of = [stamp.date() for stamp in plain.index]
            # **流動性は落とす前の値で見る。** 配当調整はリターンのための
            # もので、**規模のためのものではない。** 落とした値で売買代金を
            # 測ると実際より小さく出て、**古い足ほど強く削られる**——14年・
            # 年2回・利回り 1.3% なら、いちばん古い足は 0.69倍になる。
            # **1億円の線の上下にいる銘柄が、時期によって違う基準で落ちる**
            # （2026-09-20、ユーザーが件数の食い違いから見つけた）。
            liquid = liquid_bars(plain[CLOSE].to_numpy(dtype=float), volumes, min_turnover)
            # **不連続も落とす前の値で見る。** 50% の段差に配当は効かないが、
            # 他の説と同じ系列で見るほうが揃う。
            prefix = crossings(session_breaks(plain[CLOSE]))
            last = len(closes) - 1
            # **落とせなかった権利落ちだけ、外す側に残す。** 額が一度も公表
            # されていない日は調整のしようが無く、**機械的な値下がりが値動きに
            # 混ざったままになる。**
            adjusted_days = {when for _published, when, _rate in paid or []}
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
                # **落とせた配当は外さない。** 価格から引いてあるので、
                # **配当が −20% の線の向こうに押し出すことが起きない。**
                # 外す規則は代理で、本物の急落を 189 件巻き込んでいた
                # （2026-09-20、ユーザーが指摘）。
                #
                # **残るのは落とせなかったぶんだけ。** そして窓は**急落側と
                # 保有側の両方**を見る——ショートは配当を払う側なので、
                # 片側だけ外すと非対称が残る。
                known = known_ex_dates(own, when) - adjusted_days
                if known and any(
                    when_of[step] in known
                    for step in range(index - days + 1, min(index + holding, last) + 1)
                ):
                    ex_dropped += 1
                    excluded.append((symbol, when))
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
        dividends=dividends,
        ex_date_events=sorted(excluded, key=lambda row: (row[1], row[0])),
        excluded_broken=broken_dropped,
        symbols=read,
        thin=thin,
    )
