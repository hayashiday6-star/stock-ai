"""値幅制限に達した日と、売買停止明けの日を、リターンを計算せずに数える。

**8本目を封印する前に、母集団があるかだけを先に確かめる。** #1 では、条件を
満たす銘柄の 97.5% が流動性フィルタで消えた。それが分かったのは検証を組んだ
あとだった。ここでは順序を逆にする。

数えるのは件数・営業日数・分布だけである。**リターンは1つも計算しない**ので、
判定を消費しない。

## なぜこの2つか

7本すべてが、検出力の最も不利な角にいた——長い保有窓と、少ない独立観測。
窓の標準偏差は窓の長さの平方根でおよそ効くので、**窓を短くするだけで検出できる
差が下がる。** どちらも保有1〜5営業日で、価格だけから検出できる。

- **値幅制限**は東証の規則である。制限に達した時点で売買が物理的に成立しなく
  なり、買いたい人が買えないまま翌日に持ち越される
- **売買停止**は情報が止まる。再開時に一度に織り込まれるなら、翌日以降に残りが
  出る

## 値幅制限の検出は近似である

**制限幅は株価帯ごとの階段表で決まり、その表は改定されている。** 過去の表を
持っていないので、ここでは近似する。

  高値 ＝ 安値、出来高あり、前日比がプラス

**この近似が当たっているかは、前日比の分布で見る。** 制限幅の表が効いている
なら、検出した日の前日比は**少数の離散値に固まる**はずである。散らばって
いるなら、拾っているのは制限ではなく「1日に1回しか約定しなかった薄い銘柄」で
ある。`move_histogram` がそれを出す。

**表を推測して書かない。** 出典の無い階段表を実装すると、当たっているかどうか
を確かめる手段ごと失う。

## 売買停止の検出は暦との差である

その銘柄に足が無く、市場には足がある日を数える。市場の暦は**実データから作る**
——ある日に何銘柄が約定したかを数え、`MIN_MARKET_BREADTH` 以上の日を営業日と
みなす。祝日表を持ち込まなくて済み、持ち込んだ表が古いという失敗も起きない。

分割・併合による停止と、それ以外の停止は**ここでは分けない。** #6 の
`MAX_SESSION_MOVE` を再開日に当てて、またいだ不連続の件数を別に数える。
分けるのは、件数が足りると分かってからでよい。
"""

from __future__ import annotations

import bisect
import datetime as dt
from collections import Counter
from dataclasses import dataclass, field

import pandas as pd
from sqlalchemy.orm import Session

from stock_ai.backtest.pead import MIN_TURNOVER, TURNOVER_WINDOW
from stock_ai.backtest.reversal import MAX_SESSION_MOVE
from stock_ai.core.logging import get_logger
from stock_ai.data.schema import CLOSE, HIGH, LOW, VOLUME, split_adjusted
from stock_ai.database.engine import Database
from stock_ai.database.repository import PriceRepository, list_securities

logger = get_logger(__name__)

#: その日を営業日とみなすのに要る約定銘柄数。
#:
#: 祝日表を持ち込まずに市場の暦を作るための下限である。実際の営業日には数千
#: 銘柄が約定するので、この線はどこに置いてもほぼ同じ暦になる。効くのは
#: 「数銘柄しか値の付いていない日」を落とすことだけで、そういう日を営業日に
#: 数えると、**その日に足の無い全銘柄が売買停止に見える。**
MIN_MARKET_BREADTH = 50

#: 暦の隙間がこれ以下なら、停止ではなく週末・連休とみなして調べない。
#:
#: 候補を絞るためだけの粗い足切りで、判定そのものは暦との突き合わせで行う。
#: 3連休を挟むと暦日で4日空くので、それより広いものだけを見る。
CANDIDATE_GAP_DAYS = 5


@dataclass(frozen=True)
class EventCensus:
    """数えた結果。**リターンは含まない。**"""

    kind: str
    """``値幅制限`` か ``売買停止明け``。"""

    symbols_scanned: int
    symbols_without_prices: int

    raw_events: int
    """流動性フィルタを通す**前**の件数。"""

    events: int
    """流動性フィルタを通した後の件数。**これが母集団である。**"""

    excluded_thin: int
    """売買代金が下限に届かなかった件数。"""

    excluded_no_history: int
    """売買代金を測るだけの履歴が無かった件数（上場直後など）。"""

    per_day: Counter[dt.date] = field(default_factory=Counter)
    """営業日ごとの件数。"""

    moves: list[float] = field(default_factory=list)
    """検出日の前日比。近似の当たり具合を見るためだけに持つ。"""

    turnovers: list[float] = field(default_factory=list)
    """同じ件の売買代金（円）。``moves`` と同じ順。"""

    lengths: list[int] = field(default_factory=list)
    """停止の長さ（営業日）。値幅制限では空。"""

    crossed_discontinuity: int = 0
    """再開日が #6 の不連続の規則に当たった件数。値幅制限では 0。"""

    @property
    def survival(self) -> float:
        """流動性フィルタを通った割合。**#1 はここが 2.5% だった。**"""
        return self.events / self.raw_events if self.raw_events else 0.0

    @property
    def trading_days(self) -> int:
        """件が1つでもあった営業日の数。"""
        return len(self.per_day)

    def by_year(self) -> list[tuple[int, int, int]]:
        """年ごとの (年, 件数, その年に件のあった営業日数)。"""
        years = sorted({day.year for day in self.per_day})
        return [
            (
                year,
                sum(count for day, count in self.per_day.items() if day.year == year),
                sum(1 for day in self.per_day if day.year == year),
            )
            for year in years
        ]

    def breadth(self) -> list[tuple[str, int]]:
        """1営業日あたりの件数の分布。"""
        return _quantiles(sorted(self.per_day.values()), (0.0, 0.5, 0.95, 1.0))

    def move_histogram(self, buckets: int = 12) -> list[tuple[str, int]]:
        """前日比の分布。**近似が当たっているかは、ここが固まるかで見る。**

        制限幅の階段表が効いているなら、山は少数の位置に立つ。なだらかなら、
        拾っているのは制限ではない。
        """
        if not self.moves:
            return []
        top = max(self.moves)
        if top <= 0:
            return []
        width = top / buckets
        counted: Counter[int] = Counter()
        for move in self.moves:
            counted[min(buckets - 1, int(move / width))] += 1
        return [
            (f"{index * width:.1%}〜{(index + 1) * width:.1%}", counted[index])
            for index in range(buckets)
            if counted[index]
        ]

    def length_histogram(self) -> list[tuple[str, int]]:
        """停止の長さの分布（営業日）。"""
        if not self.lengths:
            return []
        counted = Counter(self.lengths)
        return [
            (f"{length}日" if length < 10 else "10日以上", count)
            for length, count in sorted(counted.items())
        ]

    def turnover_quantiles(self) -> list[tuple[str, float]]:
        """検出した件の売買代金の分布（億円）。

        **フィルタを通ってなお小型に寄っていないか。** #1 で消えた 97.5% は
        フィルタの外側の話だが、内側でも偏りうる。
        """
        values = sorted(value / 1e8 for value in self.turnovers)
        return [(name, float(value)) for name, value in _quantiles(values, (0.05, 0.5, 0.95))]


def _quantiles(values: list[float] | list[int], fractions: tuple[float, ...]) -> list:
    """並べ替え済みの列から分位を拾う。"""
    if not values:
        return []
    return [
        (f"p{int(fraction * 100)}", values[min(len(values) - 1, int(fraction * len(values)))])
        for fraction in fractions
    ]


def _jp_symbols(session: Session) -> list[str]:
    """JP の銘柄コード。

    ``list_securities`` は市場の絞り込み引数を**取らない。** 渡すと
    ``TypeError`` になる（本番で一度出した）。返ってきた組を絞る。
    """
    return [symbol for symbol, market in list_securities(session) if market == "JP"]


def _turnover_floor(raw: pd.DataFrame) -> pd.Series:
    """前日までの20営業日平均売買代金。

    生値で測る。調整済み終値に実出来高を掛けると、分割前のバーを分割比率の
    ぶん過小に見積もる。
    """
    return (raw[CLOSE] * raw[VOLUME]).rolling(TURNOVER_WINDOW).mean().shift(1)


def count_limit_moves(
    database: Database,
    symbols: list[str] | None = None,
    min_turnover: float = MIN_TURNOVER,
) -> EventCensus:
    """値幅制限に達したとみられる日を数える（上側だけ）。

    上側だけを数えるのは、**ロングオンリーで使うのが上側だから**である。
    ストップ安は破綻銘柄に集中するので生存バイアス感応度が高く、いま持って
    いる名簿（2021-09 以降）では直しきれない。

    Args:
        database: 価格の保存先。
        symbols: 対象銘柄。省略時は JP の全銘柄。
        min_turnover: 流動性の下限（円）。他の説と同じ1億円。

    Returns:
        件数と分布。**リターンは含まない。**
    """
    per_day: Counter[dt.date] = Counter()
    moves: list[float] = []
    turnovers: list[float] = []
    raw_events = thin = no_history = no_prices = 0

    with database.session() as session:
        if symbols is None:
            symbols = _jp_symbols(session)
        prices = PriceRepository(session)

        for symbol in symbols:
            raw = prices.get_raw_prices(symbol)
            if raw.empty:
                no_prices += 1
                continue

            adjusted = split_adjusted(raw)
            close = adjusted[CLOSE].to_numpy(dtype=float)
            floor = _turnover_floor(raw).to_numpy(dtype=float)
            highs = raw[HIGH].to_numpy(dtype=float)
            lows = raw[LOW].to_numpy(dtype=float)
            volumes = raw[VOLUME].to_numpy(dtype=float)
            index = adjusted.index

            for position in range(1, len(index)):
                if not (highs[position] == lows[position] and volumes[position] > 0):
                    continue
                previous = close[position - 1]
                if not previous > 0:
                    continue
                move = close[position] / previous - 1.0
                if move <= 0:
                    continue
                # #6 と同じ規則。1日でこれを超えるのは値動きではなく、分割・
                # 併合か停止明けの不連続である。**制限に達した日ではない。**
                if move > MAX_SESSION_MOVE:
                    continue

                raw_events += 1
                level = floor[position]
                if pd.isna(level):
                    no_history += 1
                    continue
                if level < min_turnover:
                    thin += 1
                    continue

                per_day[index[position].date()] += 1
                moves.append(move)
                turnovers.append(float(level))

    return EventCensus(
        kind="値幅制限",
        symbols_scanned=len(symbols),
        symbols_without_prices=no_prices,
        raw_events=raw_events,
        events=len(moves),
        excluded_thin=thin,
        excluded_no_history=no_history,
        per_day=per_day,
        moves=moves,
        turnovers=turnovers,
    )


@dataclass(frozen=True)
class _HaltCandidate:
    """暦と突き合わせる前の、停止らしき隙間。"""

    symbol: str
    resumed_on: dt.date
    last_seen: dt.date
    turnover: float
    """停止に入る前の20営業日平均売買代金。``NaN`` なら履歴が足りない。"""
    step: float
    """停止をまたいだ終値の比。不連続かどうかを再開日で見る。"""


def count_halt_resumptions(
    database: Database,
    symbols: list[str] | None = None,
    min_turnover: float = MIN_TURNOVER,
) -> EventCensus:
    """売買停止が明けた日を数える。

    市場の暦は実データから作る。ある日に ``MIN_MARKET_BREADTH`` 以上の銘柄が
    約定していれば営業日とみなす。**祝日表を持ち込まない**ので、表が古いこと
    による誤検出が起きない。

    Args:
        database: 価格の保存先。
        symbols: 対象銘柄。省略時は JP の全銘柄。
        min_turnover: 流動性の下限（円）。停止に**入る前**の売買代金で測る。

    Returns:
        件数と分布。**リターンは含まない。**
    """
    market_days: Counter[dt.date] = Counter()
    candidates: list[_HaltCandidate] = []
    no_prices = 0

    with database.session() as session:
        if symbols is None:
            symbols = _jp_symbols(session)
        prices = PriceRepository(session)

        for symbol in symbols:
            raw = prices.get_raw_prices(symbol)
            if raw.empty:
                no_prices += 1
                continue

            dates = [stamp.date() for stamp in raw.index]
            market_days.update(dates)

            adjusted = split_adjusted(raw)
            close = adjusted[CLOSE].to_numpy(dtype=float)
            floor = _turnover_floor(raw).to_numpy(dtype=float)

            for position in range(1, len(dates)):
                # 粗い足切り。週末・連休で空くぶんは調べない。判定そのものは
                # 暦との突き合わせで行う。
                if (dates[position] - dates[position - 1]).days <= CANDIDATE_GAP_DAYS:
                    continue
                before, after = close[position - 1], close[position]
                candidates.append(
                    _HaltCandidate(
                        symbol=symbol,
                        resumed_on=dates[position],
                        last_seen=dates[position - 1],
                        turnover=float(floor[position]),
                        step=(after / before) if before > 0 else float("nan"),
                    )
                )

    calendar = sorted(day for day, count in market_days.items() if count >= MIN_MARKET_BREADTH)
    logger.info(
        "市場の暦: %d 営業日（%d 銘柄以上が約定した日）、停止の候補 %d 件",
        len(calendar),
        MIN_MARKET_BREADTH,
        len(candidates),
    )

    per_day: Counter[dt.date] = Counter()
    lengths: list[int] = []
    turnovers: list[float] = []
    raw_events = thin = no_history = crossed = 0

    for candidate in candidates:
        # 前後の足の間に、市場が開いていた日が何日あるか。
        left = bisect.bisect_right(calendar, candidate.last_seen)
        right = bisect.bisect_left(calendar, candidate.resumed_on)
        missing = right - left
        if missing < 1:
            continue

        raw_events += 1
        if pd.isna(candidate.turnover):
            no_history += 1
            continue
        if candidate.turnover < min_turnover:
            thin += 1
            continue

        per_day[candidate.resumed_on] += 1
        lengths.append(missing)
        turnovers.append(candidate.turnover)
        if pd.notna(candidate.step) and abs(candidate.step - 1.0) > MAX_SESSION_MOVE:
            crossed += 1

    return EventCensus(
        kind="売買停止明け",
        symbols_scanned=len(symbols),
        symbols_without_prices=no_prices,
        raw_events=raw_events,
        events=len(lengths),
        excluded_thin=thin,
        excluded_no_history=no_history,
        per_day=per_day,
        moves=[],
        turnovers=turnovers,
        lengths=lengths,
        crossed_discontinuity=crossed,
    )
