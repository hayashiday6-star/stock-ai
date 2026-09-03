"""短期リバーサルの母集団を、リターンを計算せずに数える。

**この説を事前登録する前に、測れるかどうかだけを先に確かめる。** 決算ドリフト
の2本（`docs/PREREG_PEAD_JP.md`、`docs/PREREG_SUE_JP.md`）は、封印してから
検出力や母集団の薄さが分かった。順序を逆にする。

決算ドリフトと違い、リバーサルは**イベント駆動ではない**。全銘柄が毎営業日
「直近5日でどれだけ下げたか」を持つので、観測は銘柄×営業日になる。したがって
数えるべきものが変わる。

- イベント数ではなく **1営業日あたり何銘柄が条件を通るか**。分位を作るには
  1日に最低でも数十銘柄が要る
- 独立日数は営業日数そのもの。**決算ドリフトで効いた「両分位が同じ日に揃うか」
  という制約は、ここでは効かない**
- 大きさの偏りは、時価総額ではなく**売買代金の中央値**で見る。株数を持ち出す
  可動部が要らず、そのまま「売買できるか」を意味する

リターンは1つも計算しない。数えるのは件数・日数・並べ替え変数の分布だけ。
"""

from __future__ import annotations

import datetime as dt
import logging
from collections import Counter
from dataclasses import dataclass, field

import pandas as pd

from stock_ai.backtest.pead import MIN_TURNOVER, TURNOVER_WINDOW, Period
from stock_ai.data.schema import CLOSE, OPEN, VOLUME, split_adjusted
from stock_ai.database.engine import Database
from stock_ai.database.repository import PriceRepository, list_securities

logger = logging.getLogger(__name__)

#: 「大きく下げた」を測る期間（営業日）。
LOOKBACK_DAYS = 5

#: 保有期間（営業日）。リバーサルは短期の現象なので、決算ドリフトの60日より短い。
HOLDING_DAYS = 20

#: 分位数。決算ドリフトと同じにして、比較できるようにする。
QUANTILES = 5


@dataclass(frozen=True)
class ReversalCensus:
    """数えた結果。**リターンは含まない。**"""

    symbols_scanned: int
    symbols_without_prices: int
    observations: int
    """流動性と前後のバーをすべて通った銘柄×営業日の数。"""
    excluded_no_lookback: int
    """判定日の5営業日前が無い（上場直後など）。"""
    excluded_thin: int
    """売買代金が下限に届かない。"""
    excluded_no_window: int
    """翌日の寄付き、または保有期間ぶん後の終値が無い。"""
    per_day: Counter[dt.date] = field(default_factory=Counter)
    """営業日ごとの通過銘柄数。**分位を作れるかはこれで決まる。**"""
    returns: list[float] = field(default_factory=list)
    """並べ替えに使う5日リターン。分布を見るためだけに持つ。"""
    turnovers: list[float] = field(default_factory=list)
    """同じ観測の売買代金。``returns`` と同じ順で並ぶ。"""
    days: list[dt.date] = field(default_factory=list)
    """同じ観測の判定日。``returns`` と同じ順で並ぶ。"""

    @property
    def trading_days(self) -> int:
        """観測が1つでもあった営業日の数。"""
        return len(self.per_day)

    @property
    def thin_days(self) -> int:
        """分位を作れるだけの銘柄が揃わなかった日。

        5分位に分けるには最低5銘柄が要る。**足りない日は差を取れないので、
        実質的なサンプルから落ちる。**
        """
        return sum(1 for count in self.per_day.values() if count < QUANTILES)

    def breadth(self) -> list[tuple[str, int]]:
        """1営業日あたりの通過銘柄数の分布。"""
        if not self.per_day:
            return []
        counts = sorted(self.per_day.values())

        def at(fraction: float) -> int:
            return counts[min(len(counts) - 1, int(fraction * len(counts)))]

        return [(f"p{int(f * 100)}", at(f)) for f in (0.0, 0.05, 0.5, 0.95)]

    def return_quantiles(self) -> list[tuple[str, float]]:
        """5日リターンの分布。分位の境目が潰れていないかを見る。"""
        if not self.returns:
            return []
        values = sorted(self.returns)

        def at(fraction: float) -> float:
            return values[min(len(values) - 1, int(fraction * len(values)))]

        return [(f"p{int(f * 100)}", at(f)) for f in (0.01, 0.05, 0.2, 0.5, 0.8, 0.95, 0.99)]

    def by_year(self) -> list[tuple[int, int, int]]:
        """年ごとの (年, 観測数, 営業日数)。"""
        years = sorted({day.year for day in self.per_day})
        return [
            (
                year,
                sum(count for day, count in self.per_day.items() if day.year == year),
                sum(1 for day in self.per_day if day.year == year),
            )
            for year in years
        ]

    def turnover_profile(self) -> list[tuple[str, float]]:
        """日次5分位ごとの売買代金の中央値（億円）。

        **リバーサルは小型・低流動性で強いことが知られている。** 端の分位だけ
        売買代金が小さければ、流動性フィルタを通った後でも「売買しにくい銘柄を
        並べている」ことになる。アキュムレーションが中止になったのと同じ形の
        問題が、フィルタの内側でも起きていないかを見る。
        """
        if len(self.returns) < QUANTILES * 20:
            return []
        frame = pd.DataFrame({"day": self.days, "ret": self.returns, "turnover": self.turnovers})
        frame["bucket"] = frame.groupby("day")["ret"].transform(
            lambda values: (
                pd.qcut(values.rank(method="first"), QUANTILES, labels=False, duplicates="drop")
                if len(values) >= QUANTILES
                else pd.NA
            )
        )
        frame = frame.dropna(subset=["bucket"])
        if frame.empty:
            return []
        median = frame.groupby("bucket")["turnover"].median() / 1e8
        return [(f"分位{int(bucket) + 1}", float(value)) for bucket, value in median.items()]


def run_census(
    database: Database,
    period: Period = Period.ALL,
    symbols: list[str] | None = None,
    min_turnover: float = MIN_TURNOVER,
    lookback: int = LOOKBACK_DAYS,
    holding: int = HOLDING_DAYS,
) -> ReversalCensus:
    """全銘柄×全営業日を走査して数える。**リターンは計算しない。**

    入場条件は決算ドリフトと同じ形にしてある。判定日 D の**前日まで**の20営業日
    平均売買代金が下限以上で、D+1 の寄付きと D+保有日数 の終値が存在すること。

    Args:
        database: 価格の保存先。
        period: IS / OOS / ALL。センサスは判定に使わないので既定を置いてよい。
        symbols: 対象銘柄。省略時は ``market="JP"`` の全銘柄。
        min_turnover: 流動性の下限（円）。決算ドリフトと同じ1億円を引き継ぐ。
        lookback: 「大きく下げた」を測る営業日数。
        holding: 保有営業日数。
    """
    census_days: Counter[dt.date] = Counter()
    returns: list[float] = []
    turnovers: list[float] = []
    days: list[dt.date] = []
    no_prices = no_lookback = thin = no_window = 0

    with database.session() as session:
        if symbols is None:
            symbols = [sym for sym, market in list_securities(session) if market == "JP"]
        price_repo = PriceRepository(session)

        for symbol in symbols:
            raw = price_repo.get_raw_prices(symbol)
            if raw.empty:
                no_prices += 1
                continue
            adjusted = split_adjusted(raw)
            index = adjusted.index
            close = adjusted[CLOSE].to_numpy(dtype=float)
            # 売買代金は生値で測る。調整済み終値に実出来高を掛けると、分割前の
            # バーで売買代金を分割比率のぶん過小に見積もる。
            turnover = (raw[CLOSE] * raw[VOLUME]).rolling(TURNOVER_WINDOW).mean().shift(1)
            floor = turnover.to_numpy(dtype=float)
            opens = adjusted[OPEN].to_numpy(dtype=float)
            exit_offset = 1 + holding

            for position in range(len(index)):
                when = index[position].date()
                if not period.contains(when):
                    continue
                if position < lookback:
                    no_lookback += 1
                    continue
                level = floor[position]
                if pd.isna(level) or level < min_turnover:
                    thin += 1
                    continue
                exit_at = position + exit_offset
                if exit_at >= len(index) or not opens[position + 1] > 0:
                    no_window += 1
                    continue
                before = close[position - lookback]
                if not before > 0:
                    no_lookback += 1
                    continue
                census_days[when] += 1
                returns.append(close[position] / before - 1.0)
                turnovers.append(float(level))
                days.append(when)

    logger.info("リバーサル・センサス: %d 観測 ／ %d 営業日", len(returns), len(census_days))
    return ReversalCensus(
        symbols_scanned=len(symbols),
        symbols_without_prices=no_prices,
        observations=len(returns),
        excluded_no_lookback=no_lookback,
        excluded_thin=thin,
        excluded_no_window=no_window,
        per_day=census_days,
        returns=returns,
        turnovers=turnovers,
        days=days,
    )
