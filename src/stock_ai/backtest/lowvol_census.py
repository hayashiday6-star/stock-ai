"""低ボラティリティの母集団を、リターンを計算せずに数える。

**封印の前に、測れるかどうかだけを先に確かめる。** #2 と #3 は封印してから
検出力が足りないと分かった。#6 で順序を逆にして、それは正しかった。同じ手順を
踏む。

リバーサル（#6）との違いが3つあり、そのどれもが数えるべきものを変える。

- **月次リバランスなので、観測は銘柄×営業日ではなく銘柄×月**になる。1日ごとに
  分位を組まないので、#6 を苦しめた重なりの膨張（2.95x）が起きない
- **選抜が値動きの穏やかな銘柄に寄る。** 破綻寄りの銘柄はむしろ高ボラ側に落ちる
  ので、生存バイアスは #6 より小さいはずである。**「はず」なので数える**
- **業種が偏るという指摘がある**（内需・ディフェンシブに集中）。#6 では規模の
  偏りを測って「平ら」と分かった。今回は業種の偏りを測る

リターンは1つも計算しない。数えるのは件数・月数・ボラティリティの分布・
分位ごとの売買代金と業種だけである。

## 測定窓を3つ同時に数える理由

60 / 120 / 250 営業日のどれを使うかは、封印前に決めなければならない。**件数で
決めるのは事後的な選択にならない**（リターンを見ていない）ので、3つとも数えて
から選ぶ。窓が長いほど履歴を要求するので、通る銘柄は減る。その減り方を見て
決める。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from stock_ai.backtest.pead import MIN_TURNOVER, TURNOVER_WINDOW, Period
from stock_ai.backtest.reversal import BENCHMARK, MAX_SESSION_MOVE
from stock_ai.backtest.reversal_census import QUANTILES
from stock_ai.core.logging import get_logger
from stock_ai.data.schema import CLOSE, OPEN, VOLUME, split_adjusted
from stock_ai.database.engine import Database
from stock_ai.database.repository import PriceRepository, get_profile, list_securities

logger = get_logger(__name__)

#: 数える測定窓（営業日）。**どれを使うかは封印前に決める。**
VOLATILITY_WINDOWS = (60, 120, 250)

#: リバランスの間隔。月次で確定している（重なりを作らないため）。
#: 暦の各月の最終営業日に組み替える。


@dataclass
class WindowCensus:
    """1つの測定窓について数えた結果。**リターンは含まない。**

    走査しながら数え上げるので凍結しない（`ReversalCensus` は最後に一度だけ
    組み立てるので凍結してある）。
    """

    window: int
    months: int
    """組み替え日の数（最後の1つは次が無いので含めない）。"""
    excluded_no_history: int = 0
    """測定窓ぶんの履歴が無い（上場直後など）。"""
    excluded_thin: int = 0
    """売買代金が下限に届かない。"""
    excluded_no_window: int = 0
    """翌営業日の寄付き、または次のリバランス日の寄付きが無い。"""
    excluded_discontinuity: int = 0
    """測定窓か保有期間が価格系列の不連続をまたぐ。"""
    per_month: Counter[str] = field(default_factory=Counter)
    volatilities: list[float] = field(default_factory=list)
    turnovers: list[float] = field(default_factory=list)
    sectors: list[str] = field(default_factory=list)
    months_key: list[str] = field(default_factory=list)
    symbols_key: list[str] = field(default_factory=list)

    @property
    def observations(self) -> int:
        """すべての条件を通った銘柄×月の数。"""
        return len(self.volatilities)

    @property
    def per_quantile(self) -> int:
        """1分位あたりの銘柄数（月の中央値から）。"""
        if not self.per_month:
            return 0
        return int(np.median(list(self.per_month.values()))) // QUANTILES

    def breadth(self) -> list[tuple[str, int]]:
        """1ヶ月あたりの通過銘柄数の分布。"""
        if not self.per_month:
            return []
        counts = sorted(self.per_month.values())

        def at(fraction: float) -> int:
            return counts[min(len(counts) - 1, int(fraction * len(counts)))]

        return [(f"p{int(f * 100)}", at(f)) for f in (0.0, 0.05, 0.5, 0.95)]

    def volatility_quantiles(self) -> list[tuple[str, float]]:
        """ボラティリティの分布。分位の境目が潰れていないかを見る。"""
        if not self.volatilities:
            return []
        values = sorted(self.volatilities)

        def at(fraction: float) -> float:
            return values[min(len(values) - 1, int(fraction * len(values)))]

        return [(f"p{int(f * 100)}", at(f)) for f in (0.01, 0.05, 0.5, 0.95, 0.99)]

    def _buckets(self) -> pd.DataFrame | None:
        """月ごとの5分位。**低いほうが分位1**（買う側）。"""
        if len(self.volatilities) < QUANTILES * 20:
            return None
        frame = pd.DataFrame(
            {
                "month": self.months_key,
                "symbol": self.symbols_key,
                "vol": self.volatilities,
                "turnover": self.turnovers,
                "sector": self.sectors,
            }
        )
        frame["bucket"] = frame.groupby("month")["vol"].transform(
            lambda values: (
                pd.qcut(values.rank(method="first"), QUANTILES, labels=False, duplicates="drop")
                if len(values) >= QUANTILES
                else pd.NA
            )
        )
        return frame.dropna(subset=["bucket"])

    def turnover_profile(self) -> list[tuple[str, float]]:
        """分位ごとの売買代金の中央値（億円）。

        #6 では「小型に寄る」という事前の見立てが外れて平らだった。今回も
        **測ってから言う。** 端の分位だけ売買代金が小さければ、フィルタを
        通った後でも売買しにくい銘柄を並べていることになる。
        """
        frame = self._buckets()
        if frame is None or frame.empty:
            return []
        median = frame.groupby("bucket")["turnover"].median() / 1e8
        return [(f"分位{int(bucket) + 1}", float(value)) for bucket, value in median.items()]

    def sector_profile(self) -> list[tuple[str, list[tuple[str, float]]]]:
        """分位ごとの業種構成の上位3つ（割合）。

        **低ボラは内需・ディフェンシブに偏るという指摘がある。** 偏っていれば、
        測っているものの一部は業種のリターン差になる。業種中立版を副次に置く
        かどうかを、封印前にこの数字で決める。
        """
        frame = self._buckets()
        if frame is None or frame.empty:
            return []
        result: list[tuple[str, list[tuple[str, float]]]] = []
        for bucket, group in frame.groupby("bucket"):
            share = group["sector"].value_counts(normalize=True).head(3)
            result.append(
                (
                    f"分位{int(bucket) + 1}",
                    [(str(name), float(value)) for name, value in share.items()],
                )
            )
        return result

    def thin_months(self, minimum: int) -> tuple[int, int]:
        """通過銘柄が ``minimum`` に満たない月の数と、全体の月数。

        **1ヶ月10銘柄では、5分位が2銘柄ずつになる。** その月の分位平均は
        個別銘柄のリターンそのもので、分散だけが大きくなる。最低銘柄数は
        封印前に決める必要があり、その材料がこれである。
        """
        return sum(1 for count in self.per_month.values() if count < minimum), len(self.per_month)

    def quantile_persistence(self, bucket: int = 0) -> tuple[float, int]:
        """月をまたいで分位 ``bucket`` に残る割合と、比べられた月の数。

        **この説の売りは回転率の低さである。** 月次リバランスでも、構成が
        ほとんど変わらないなら費用は保有期間で割られる。#6 は20営業日ごとに
        全入れ替えで 0.40%／回だったが、ここで8割が残るなら実効費用は
        その2割になる。

        **費用の前提は仮定せずに測る。** この説を選んだ理由そのものなので、
        当て推量で登録すると、判定の意味が変わってしまう。

        Args:
            bucket: 0 が分位1（低ボラ側＝買う側）。

        Returns:
            ``(残存率, 比べた月数)``。比べられなければ ``(nan, 0)``。
        """
        frame = self._buckets()
        if frame is None or frame.empty:
            return float("nan"), 0
        members = {
            str(month): set(group.loc[group["bucket"] == bucket, "symbol"])
            for month, group in frame.groupby("month")
        }
        months = sorted(members)
        kept: list[float] = []
        for earlier, later in zip(months, months[1:], strict=False):
            before = members[earlier]
            if not before:
                continue
            kept.append(len(before & members[later]) / len(before))
        if not kept:
            return float("nan"), 0
        return sum(kept) / len(kept), len(kept)


def formation_dates(calendar: pd.DatetimeIndex) -> list[int]:
    """各月の最終営業日の位置。**月次リバランスの組み替え日である。**

    暦はベンチマークのものを使う。銘柄ごとの暦で月末を決めると、売買停止を
    挟んだ銘柄だけ組み替え日がずれて、同じ月の分位に別の窓が混ざる。
    """
    if len(calendar) == 0:
        return []
    months = calendar.to_period("M")
    # 月が変わる直前の位置が、その月の最終営業日。
    return [index for index in range(len(calendar) - 1) if months[index] != months[index + 1]]


def run_census(
    database: Database,
    period: Period = Period.ALL,
    symbols: list[str] | None = None,
    benchmark: str = BENCHMARK,
    min_turnover: float = MIN_TURNOVER,
    windows: tuple[int, ...] = VOLATILITY_WINDOWS,
) -> list[WindowCensus]:
    """測定窓ごとに母集団を数える。**リターンは計算しない。**

    Args:
        database: 価格の保存先。
        period: IS / OOS / ALL。センサスは判定に使わない。
        symbols: 対象銘柄。省略時は ``market="JP"`` の全銘柄。
        benchmark: 暦を決めるための銘柄。
        min_turnover: 流動性の下限（円）。#6 と同じ1億円を引き継ぐ。
        windows: 数える測定窓。

    Raises:
        ValueError: ベンチマークの価格が無い。
    """
    with database.session() as session:
        price_repo = PriceRepository(session)
        bench_raw = price_repo.get_raw_prices(benchmark)
        if bench_raw.empty:
            raise ValueError(f"ベンチマーク {benchmark!r} の価格が無い。暦を決められない。")
        calendar = split_adjusted(bench_raw).index
        formations = formation_dates(calendar)
        if len(formations) < 2:
            raise ValueError("組み替え日が2つ未満。月次リバランスを作れない。")

        if symbols is None:
            symbols = [sym for sym, market in list_securities(session) if market == "JP"]
        targets = [symbol for symbol in symbols if symbol != benchmark]
        sectors = {symbol: (get_profile(session, symbol) or None) for symbol in targets}
        sector_of = {
            symbol: (profile.sector or "不明") if profile is not None else "不明"
            for symbol, profile in sectors.items()
        }

        results = [_census_one(window, formations) for window in windows]

        for symbol in targets:
            raw = price_repo.get_raw_prices(symbol)
            if raw.empty:
                continue
            adjusted = split_adjusted(raw).reindex(calendar)
            close = adjusted[CLOSE].to_numpy(dtype=float)
            opens = adjusted[OPEN].to_numpy(dtype=float)
            turnover = (raw[CLOSE] * raw[VOLUME]).rolling(TURNOVER_WINDOW).mean().shift(1)
            floor = turnover.reindex(calendar).to_numpy(dtype=float)

            # 不連続の検出は #6 と同じ規則・同じ定数を使う。ここで独自に近いものを
            # 書くと、数えた件数と実際に回したときの件数がずれる。
            filled = adjusted[CLOSE].ffill().to_numpy(dtype=float)
            with np.errstate(divide="ignore", invalid="ignore"):
                step = filled[1:] / filled[:-1]
            broken = np.zeros(len(calendar), dtype=bool)
            broken[1:] = np.isfinite(step) & (np.abs(step - 1.0) > MAX_SESSION_MOVE)
            breaks = np.concatenate(([0], np.cumsum(broken)))

            returns = np.full(len(calendar), np.nan)
            with np.errstate(divide="ignore", invalid="ignore"):
                returns[1:] = close[1:] / close[:-1] - 1.0

            for census in results:
                _scan_symbol(
                    census,
                    symbol,
                    sector_of[symbol],
                    formations,
                    calendar,
                    close,
                    opens,
                    floor,
                    breaks,
                    returns,
                    period,
                    min_turnover,
                )

    for census in results:
        logger.info(
            "低ボラ・センサス（%d日窓）: %d 観測 ／ %d ヶ月",
            census.window,
            len(census.volatilities),
            len(census.per_month),
        )
    return results


def _census_one(window: int, formations: list[int]) -> WindowCensus:
    """空の集計を作る。"""
    return WindowCensus(window=window, months=len(formations) - 1)


def _scan_symbol(  # noqa: PLR0913 - 数え口を1箇所に集めるための引数
    census: WindowCensus,
    symbol: str,
    sector: str,
    formations: list[int],
    calendar: pd.DatetimeIndex,
    close: np.ndarray,
    opens: np.ndarray,
    floor: np.ndarray,
    breaks: np.ndarray,
    returns: np.ndarray,
    period: Period,
    min_turnover: float,
) -> None:
    """1銘柄を全組み替え日について数える。**除外はすべて数え口を通す。**"""
    window = census.window
    for index, position in enumerate(formations[:-1]):
        when = calendar[position].date()
        if not period.contains(when):
            continue
        if position < window:
            census.excluded_no_history += 1
            continue

        entry = position + 1
        exit_at = formations[index + 1] + 1
        if exit_at >= len(calendar):
            census.excluded_no_window += 1
            continue

        level = floor[position]
        if not np.isfinite(level) or level < min_turnover:
            census.excluded_thin += 1
            continue

        # 測定窓と保有期間のどちらかが不連続をまたぐなら落とす。またぐ窓の
        # ボラティリティは値動きではなく、尺度の変わり目を測っている。
        if breaks[exit_at + 1] - breaks[position - window + 1] > 0:
            census.excluded_discontinuity += 1
            continue

        if not (opens[entry] > 0) or not (opens[exit_at] > 0):
            census.excluded_no_window += 1
            continue

        sample = returns[position - window + 1 : position + 1]
        if not np.isfinite(sample).all():
            census.excluded_no_history += 1
            continue

        key = f"{when:%Y-%m}"
        census.per_month[key] += 1
        census.volatilities.append(float(np.std(sample, ddof=1)))
        census.turnovers.append(float(level))
        census.sectors.append(sector)
        census.months_key.append(key)
        census.symbols_key.append(symbol)
