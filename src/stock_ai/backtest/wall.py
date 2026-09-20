"""**壁の下見** — 事前登録を書く前に、検出できる差だけを出す。

## なぜ要るか

**7本続けて §0 で閉じている。** 説が悪いからではない——**設計の散らばりと
観測数で、壁の高さが決まっている。**

既に測った形の壁は `docs/PASSING.md` に出ている（**生成物である。ここに
書き写さない**——線が動けば全部動く）。月次の分位ロングショートは年20%台で、
**実在するアノマリーでその大きさのものは、まず無い。**

それなら、事前登録を書く前に**壁の高さのほうを先に見る**ほうが安い。

## なぜこれが「答えを先に見る」ことにならないか

検出できる差は **`線 × SD ÷ √n`** で、**散らばりと観測数だけで決まる。**
効果（平均）は式に入らない。

**だから `Wall` に平均の欄を作っていない。** `PowerEstimate` が平均を持たない
のと同じ理由である——**入れられる形にすると、「効果がありそうだから通す」が
書けてしまう。** `tests/test_wall.py` が、この module が平均を計算していない
ことを機械的に確かめる。

**設計を検出力で選ぶのは、答えを見て選ぶのとは違う。** #10 が封印できなく
なったのは、**IS の `t`（＝効果を見た数字）が設計で 2.8倍振れた**からである。
ここで比べるのは壁の高さだけで、**どの設計が勝っているかは分からない。**

## 何を測り、何を測らないか

**IS（2009-01〜2017-12）のリターンだけを読む。** OOS のリターンは1つも計算
しない。**ただしイベントの件数は OOS も数える**——判定に使える観測数 `n` が
そこで決まるためで、**件数は効果ではない**（#5 の件数センサスと同じ扱い）。

**イベント型の窓は 20営業日に固定した**（#5 と同じ）。**壁を同じ物差しで
並べるため**であって、事前登録がそれを選ぶという意味ではない。窓を変えれば
壁も動くので、**そのときは測り直す。**
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import math
from collections.abc import Callable

import numpy as np
import pandas as pd

from stock_ai.backtest.discontinuity import crossings, session_breaks, spans_break
from stock_ai.backtest.gap_fill import GAP_DOWN as _GAP_DOWN
from stock_ai.backtest.gap_fill import gap_positions, liquid_bars
from stock_ai.backtest.knife import KNIFE_DAYS as _KNIFE_DAYS
from stock_ai.backtest.knife import KNIFE_DROP as _KNIFE_DROP
from stock_ai.backtest.knife import knife_positions
from stock_ai.backtest.lowvol_census import formation_dates
from stock_ai.backtest.pead import MIN_TURNOVER
from stock_ai.core.logging import get_logger
from stock_ai.data.schema import CLOSE, OPEN, VOLUME, split_adjusted
from stock_ai.database.engine import Database

logger = get_logger(__name__)

#: IS の終わり。**ここまでのリターンしか読まない。**
IS_END = dt.date(2017, 12, 31)

#: OOS の始まり。**件数はここも数える。リターンは計算しない。**
OOS_FROM = dt.date(2018, 1, 1)

#: OOS の終わり。
OOS_END = dt.date(2026, 8, 31)

#: イベント型の保有営業日数。**#5 と同じ。** 壁を同じ物差しで並べるため。
HOLDING = 20

#: 52週高値を測る営業日数。**52週 ≒ 250営業日。** 暦から出した値である。
HIGH_WINDOW = 250

#: 下窓と呼ぶ幅。**正本は `gap_fill.GAP_DOWN` にある。**
#:
#: #15 が同じ規則を使う。**2つ持つと、下見で選んだ設計と判定に使う設計が、
#: 黙ってずれる。** ここは名前を残すためだけである。
GAP_DOWN = _GAP_DOWN

#: 急落と呼ぶ幅と日数。**正本は `knife` にある。**
#:
#: #16 が同じ規則を使う。**2つ持つと、下見で選んだ設計と判定に使う設計が、
#: 黙ってずれる。** ここは名前を残すためだけである。
KNIFE_DROP = _KNIFE_DROP
KNIFE_DAYS = _KNIFE_DAYS


@dataclasses.dataclass(frozen=True)
class Wall:
    """ある設計の**壁の高さ**。**平均は持たない。**

    `PowerEstimate` と同じ設計である。**平均を持てる形にすると、「効果が
    ありそうだから通す」が書けてしまう。**
    """

    candidate: int
    """`docs/CANDIDATES.md` の順位。"""

    name: str
    pipe: str
    unit: str
    """1観測が何か（`月` / `年` / `イベント日`）。"""

    observations: int
    """**判定に使える観測数。** OOS の数である。"""

    sd: float
    """1観測あたりの標準偏差。**IS で測った。**"""

    inflation: float
    """重なりで標準誤差が何倍になるか。**実測。**

    **掛け忘れると緩む。** 20日保有・毎日エントリーなら理屈の上で4倍前後に
    なるので、落とせば壁が数倍低く出る（`power.standard_error`）。
    """

    line: float
    source: str
    """どうやって測ったか。**出典の無い数字を書かない。**"""

    per_year: float | None = None
    """1年あたりの観測数。年率に直せない設計では ``None``。"""

    notes: tuple[str, ...] = ()

    @property
    def detectable(self) -> float:
        """検出できる差。**式は `power` に1つだけ置いてある。**

        `線 × SD × 膨張 ÷ √n` である。**膨張を落とした版を1度書いた**
        （2026-09-19）——`passing.Shape` は掛けていたのに、ここだけ落ちて
        いた。**同じ式を3つ書けば、1つは間違える。**
        """
        from stock_ai.backtest.power import detectable_difference

        return detectable_difference(self.sd, self.inflation, self.observations, self.line)

    @property
    def annual(self) -> float | None:
        """年あたりに直した壁。**直せない設計では ``None``。**

        **決めずに掛けない。** 資金をどれだけ張るかを決めないと、イベント型は
        年率に直せない（`docs/PASSING.md` と同じ扱い）。
        """
        return None if self.per_year is None else self.detectable * self.per_year


@dataclasses.dataclass(frozen=True)
class Missing:
    """**材料が無くて測れない候補。** 出力に出す——無いことは出力に出ない。"""

    candidate: int
    name: str
    reason: str


@dataclasses.dataclass
class Materials:
    """1回の走査で集めた材料。**価格を何度も読まないため。**"""

    high52: dict[tuple[str, pd.Period], tuple[dt.date, float]]
    gaps_is: list[tuple[str, dt.date]]
    gaps_oos_days: int
    knives_is: list[tuple[str, dt.date]]
    knives_oos_days: int
    symbols: int
    skipped_short: int
    """52週に足りず、近さを作れなかった銘柄。"""

    dropped_broken: int
    """**不連続をまたぐので捨てたイベント**（IS）。

    **5営業日で −20% は、調整漏れの分割がそう見える形**である。#6 は不連続を
    外すだけで SD が 24.42% → 3.64% になった（`discontinuity`）。
    """

    def summary(self) -> str:
        """1行のまとめ。**平均は出さない。**"""
        return (
            f"{self.symbols:,} 銘柄を読んだ。"
            f"52週高値への近さ {len(self.high52):,} 銘柄月"
            f"（履歴が足りず外した銘柄 {self.skipped_short:,}）、"
            f"下窓 {len(self.gaps_is):,} 件（IS）、"
            f"急落 {len(self.knives_is):,} 件（IS）。"
            f"**不連続をまたぐので捨てた {self.dropped_broken:,} 件。**"
        )


def scan(
    database: Database,
    symbols: list[str] | None = None,
    min_turnover: float = MIN_TURNOVER,
    progress: Callable[[int, int], None] | None = None,
) -> Materials:
    """価格を**1度だけ**読んで、3つの設計の材料を同時に作る。

    Args:
        database: 価格の保存先。
        symbols: 対象銘柄。省くと JP の全銘柄。
        min_turnover: 流動性の下限（円）。
        progress: ``(済み, 全体)`` で呼ばれる。**1行に収めること。**

    Returns:
        :class:`Materials`。

    Raises:
        ValueError: 銘柄が1つも無い。
    """
    from stock_ai.database.repository import PriceRepository, list_securities

    high52: dict[tuple[str, pd.Period], tuple[dt.date, float]] = {}
    gaps_is: list[tuple[str, dt.date]] = []
    knives_is: list[tuple[str, dt.date]] = []
    gap_days_oos: set[dt.date] = set()
    knife_days_oos: set[dt.date] = set()
    read = short = dropped = 0

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
            index = adjusted.index
            if len(closes) < 2:  # noqa: PLR2004 - 1本では何も作れない
                continue

            liquid = liquid_bars(closes, volumes, min_turnover)
            days = [stamp.date() for stamp in index]
            # **不連続をまたぐ窓を使わない。** 規則も定数も #6 と同じものを
            # 呼ぶ（`discontinuity`）——ここで近いものを書き直すと、数えた
            # 件数と実際に回したときの件数がずれる。
            prefix = crossings(session_breaks(adjusted[CLOSE]))
            last = len(closes) - 1

            def clean(first: int, until: int, _prefix=prefix, _last=last) -> bool:
                """``[first, until]`` に不連続が無いか。**端は切り詰める。**"""
                return not spans_break(_prefix, max(first, 0), min(until, _last))

            # --- 下窓（前日終値 → 当日始値）------------------------------
            # **規則は `gap_fill` に1つだけ置いてある。**
            for position in gap_positions(opens, closes, liquid, GAP_DOWN):
                # **窓の中に不連続があれば使わない。** 前日も見る——窓そのもの
                # が不連続でできている形を外すため。
                if not clean(position - 1, position + HOLDING):
                    dropped += 1
                    continue
                when = days[position]
                if when <= IS_END:
                    gaps_is.append((symbol, when))
                elif OOS_FROM <= when <= OOS_END:
                    gap_days_oos.add(when)

            # --- 急落（KNIFE_DAYS 営業日で KNIFE_DROP 以上）---------------
            # **規則は `knife` に1つだけ置いてある。**
            if len(closes) > KNIFE_DAYS:
                for position in knife_positions(closes, liquid, KNIFE_DROP, KNIFE_DAYS):
                    # **急落そのものが不連続でないこと。** 1:2 の併合は
                    # −50% に見える。そして窓の中も見る。
                    if not clean(position - KNIFE_DAYS, position + HOLDING):
                        dropped += 1
                        continue
                    when = days[position]
                    if when <= IS_END:
                        knives_is.append((symbol, when))
                    elif OOS_FROM <= when <= OOS_END:
                        knife_days_oos.add(when)

            # --- 52週高値への近さ（月末だけ）-----------------------------
            if len(closes) < HIGH_WINDOW:
                short += 1
                continue
            highs = pd.Series(closes).rolling(HIGH_WINDOW, min_periods=HIGH_WINDOW).max().to_numpy()
            months = index.to_period("M")
            # **その銘柄の、その月の最後の足。** 暦の月末を探さない——月の
            # 途中で上場廃止になった銘柄が丸ごと落ちる（`valuation_monthly`）。
            last_of_month = np.flatnonzero(np.r_[months[1:] != months[:-1], True])
            for offset in last_of_month:
                top = highs[offset]
                if not np.isfinite(top) or top <= 0 or not closes[offset] > 0:
                    continue
                if not liquid[offset]:
                    continue
                high52[(symbol, months[offset])] = (days[offset], float(closes[offset] / top))

    return Materials(
        high52=high52,
        gaps_is=gaps_is,
        gaps_oos_days=len(gap_days_oos),
        knives_is=knives_is,
        knives_oos_days=len(knife_days_oos),
        symbols=read,
        skipped_short=short,
        dropped_broken=dropped,
    )


def halloween_episodes(
    returns: list[float],
    dates: list[dt.date],
    end: dt.date = IS_END,
) -> tuple[list[int], list[float]]:
    """「冬（11月〜翌4月） − 夏（5月〜10月）」を年ごとに1つ作る。

    **年に1観測である。** 半年ごとに2つ作ると、同じ年の冬と夏が別々の観測に
    なり、**差を取っていないことになる。**

    Args:
        returns: 日次リターン。
        dates: その日付（``returns`` と同じ長さ）。
        end: この日より後を使わない。

    Returns:
        ``(年, その年の差)``。**冬と夏が両方そろった年だけ。**

    Raises:
        ValueError: 長さが違う。
    """
    if len(returns) != len(dates):
        raise ValueError(f"returns {len(returns)} と dates {len(dates)} の長さが違う。")

    winter: dict[int, list[float]] = {}
    summer: dict[int, list[float]] = {}
    for value, when in zip(returns, dates, strict=True):
        if when > end or not math.isfinite(value):
            continue
        if when.month >= 11:  # noqa: PLR2004 - 11月と12月は翌年の冬
            winter.setdefault(when.year + 1, []).append(value)
        elif when.month <= 4:  # noqa: PLR2004 - 1月〜4月はその年の冬
            winter.setdefault(when.year, []).append(value)
        else:
            summer.setdefault(when.year, []).append(value)

    years: list[int] = []
    episodes: list[float] = []
    for year in sorted(set(winter) & set(summer)):
        # **両方そろった年だけ。** 端の半年だけで1観測を作らない。
        years.append(year)
        episodes.append(float(np.sum(winter[year])) - float(np.sum(summer[year])))
    return years, episodes


def complete_halloween_years(start: dt.date, end: dt.date) -> int:
    """``start``〜``end`` に、冬と夏が**両方**収まる年が何回あるか。

    冬は前年11月から始まるので、**期間の頭の1年は作れない。**

    Args:
        start: 期間の始め。
        end: 期間の終わり。

    Returns:
        回数。
    """
    found = 0
    for year in range(start.year, end.year + 1):
        if dt.date(year - 1, 11, 1) >= start and dt.date(year, 10, 31) <= end:
            found += 1
    return found


def usable_rebalances(calendar: pd.DatetimeIndex, start: dt.date, end: dt.date) -> int:
    """``start``〜``end`` に収まる**組み替えの回数。判定に使える月数である。**

    **`build_grid` に聞く。** 月末を数えるだけでは1回多くなる——最後の月末は、
    降りる先の月末が無いので使えない。**同じ処理を2つ書かない。**

    Args:
        calendar: ベンチマークの暦。
        start: 期間の始め。
        end: 期間の終わり。**降りる日がこれを越える組み替えは数えない。**

    Returns:
        回数。1回も作れなければ 0。
    """
    from stock_ai.backtest.monthly_grid import build_grid

    try:
        grid = build_grid(calendar, formation_dates(calendar), start=start, end=end)
    except ValueError:
        # **1回も作れないのは例外ではない。** 期間が短ければ当たり前に起きる。
        return 0
    return grid.months
