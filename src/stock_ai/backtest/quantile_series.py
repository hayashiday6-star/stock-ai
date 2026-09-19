"""月次の分位ロングショートに**共通する読み方**を1つだけ置く。

#9（PBR で並べる）と #12（モメンタムで並べる）は、**並べる材料しか違わない。**
スプレッドの取り方、入れ替わりの数え方、β の引き方、費用の掛け方は同じである。

**呼ぶ側で書き直さない。** `key_period` を、それを戒める文章を書いた同じ日に
2つ目書いた前例がある（`CLAUDE.md`）。

## 何をここに置き、何を置かないか

**置くのは「分位が出来たあとの読み方」だけ。** どう並べるか、どこで入って
どこで降りるか、何を流動性で弾くかは**説ごとに違う**ので、それぞれの module に
残す。

**ここに平均は無い。** `§0` を埋める段で平均を出すのは設計ごとの判断で、
共通部分が勝手に出すと、**判定を先食いしたことに気付けない。**
"""

from __future__ import annotations

import dataclasses
import datetime as dt

import numpy as np
import pandas as pd

from stock_ai.backtest import tails
from stock_ai.backtest.lowvol import MIN_SYMBOLS_PER_MONTH, ROUND_TRIP_COST
from stock_ai.backtest.lowvol_census import formation_dates
from stock_ai.backtest.monthly_grid import build_grid, listed_on
from stock_ai.backtest.pead import MIN_TURNOVER, TURNOVER_WINDOW, Period
from stock_ai.backtest.reversal import BENCHMARK
from stock_ai.backtest.reversal_census import QUANTILES
from stock_ai.core.logging import get_logger
from stock_ai.data.schema import CLOSE, OPEN, VOLUME, split_adjusted
from stock_ai.database.engine import Database
from stock_ai.database.repository import PriceRepository

logger = get_logger(__name__)

#: （その月末の日付, 値）。**日付を捨てない**——月で引いて日付で弾くため。
_Observed = tuple[dt.date, float]


@dataclasses.dataclass
class QuantileSeries:
    """月ごとの分位リターンと、その顔ぶれ。**添字0が下端、末尾が上端。**

    「下端」「上端」が何を意味するかは**並べた材料で決まる**ので、ここでは
    決めない。#9 は PBR 昇順（0 が最も割安）、#12 はモメンタム昇順（0 が最も
    負けている）。
    """

    months: list[dt.date]
    quantiles: list[tuple[float, ...]]
    """分位ごとの月次リターン。"""

    members: list[tuple[frozenset[str], frozenset[str]]]
    """月ごとの（下端の分位、上端の分位）の顔ぶれ。"""

    counts: list[int]
    benchmark: list[float]

    round_trip_cost: float = dataclasses.field(default=ROUND_TRIP_COST, init=False, repr=False)
    """1往復の費用。**売買のコストであって、並べ方には依らない。**"""

    def spread(self) -> list[float]:
        """**上端 − 下端。**

        **向きはここで決めない。** どちらが正だと予測するかは説の側の話である
        ——`AntiValueSeries` は「高PBR − 低PBR」、`MomentumSeries` は
        「勝者 − 敗者」。どちらも上端から下端を引いた同じ式である。
        """
        return [row[-1] - row[0] for row in self.quantiles]

    def turnover(self) -> float:
        """両端の分位の、月をまたいだ入れ替わり率。**測る。写さない。**

        顔ぶれが 100件から 100件へ動くとき、消えた割合を取る。前月が無い
        最初の月は数えない。
        """
        changes = []
        for (low_now, high_now), (low_before, high_before) in zip(
            self.members[1:], self.members[:-1], strict=False
        ):
            for now, before in ((low_now, low_before), (high_now, high_before)):
                if before:
                    changes.append(1.0 - len(now & before) / len(before))
        return float(np.mean(changes)) if changes else 0.0

    def beta_to_benchmark(self) -> float:
        """スプレッドのベンチマークに対する β（最小二乗）。

        **ロング・ショートでも β は 0 ではない。** 両端の分位の感応度が違えば
        差にも市場が残る。**市場が動いた月はスプレッドが一方向に出る**ので、
        その上下動が分散のほとんどを作り、検出力を食う。

        #9 は §5 に「β を引いた α も併記する」と書いておきながら、§0 の表を
        生の差だけで埋めた（2026-09-16）。**桁が違いうる。**

        Raises:
            ValueError: 月が2つ未満、またはベンチマークが動かない。
        """
        from stock_ai.backtest.cross_section import beta_to_benchmark

        return beta_to_benchmark(self.spread(), self.benchmark)

    def alpha(self, beta: float) -> list[float]:
        """スプレッド − β×ベンチマーク。

        β は外から渡す。**この系列自身から推定した β を判定期間に当てると、
        判定期間の情報でその期間を調整することになる**（#7 §7-2 と同じ）。
        IS の推定では IS の β を当ててよいが、**判定では IS で固定した値を渡す。**
        """
        return [
            value - beta * bench for value, bench in zip(self.spread(), self.benchmark, strict=True)
        ]

    def cost_per_month(self) -> float:
        """月あたりの費用。**実測した入れ替わり率から出す。**"""
        return self.round_trip_cost * self.turnover()

    def worst_month(self) -> float:
        """いちばん悪かった月のスプレッド。**式は `tails` に1つだけ置いてある。**"""
        return tails.worst(self.spread())

    def left_tail(self, share: float = tails.DEFAULT_TAIL_SHARE) -> float:
        """下位 ``share`` の月の平均。**1点ではなく帯で見る。**

        Args:
            share: 下から取る割合。

        Returns:
            下位の平均。月が無ければ ``nan``。

        Raises:
            ValueError: ``share`` が 0〜1 の外。
        """
        return tails.left_tail(self.spread(), share)

    def hit_rate(self) -> float:
        """スプレッドが正だった月の割合。**平均だけで語らない。**"""
        return tails.hit_rate(self.spread())


def monthly_values(frame: pd.DataFrame, column: str) -> dict[tuple[str, pd.Period], _Observed]:
    """（銘柄, 月）→（その月末の日付, 値）。

    **月で引く。日付そのものでは引かない。** 月の途中で上場廃止になった銘柄の
    最後の観測は、ベンチマークの月末とは違う日である。日付で引くと、**その銘柄
    が丸ごと落ちる。**

    Args:
        frame: :func:`~stock_ai.data.valuation_monthly.read` が返す形。
        column: 取り出す列。

    Returns:
        （銘柄, 月）で引ける表。**値が空の行は入らない。**

    Raises:
        KeyError: ``column`` がその表に無い。
    """
    found: dict[tuple[str, pd.Period], _Observed] = {}
    if frame.empty:
        return found
    if column not in frame.columns:
        raise KeyError(f"{column!r} がこの表に無い。在るのは {list(frame.columns)}。")
    months = pd.to_datetime(frame["date"]).dt.to_period("M")
    for (symbol, month), date, value in zip(
        zip(frame["symbol"], months, strict=True),
        frame["date"],
        frame[column],
        strict=True,
    ):
        if value is None or pd.isna(value):
            continue
        found[(symbol, month)] = (date, float(value))
    return found


def value_on(
    values: dict[tuple[str, pd.Period], _Observed],
    symbol: str,
    on: dt.date,
) -> float | None:
    """組み替え日 ``on`` の時点で使ってよい値。無ければ ``None``。

    **組み替え日より後の値を使わない。** 先読みである。月で引いておいて日付で
    弾くのは、月の途中で上場廃止になった銘柄を落とさないためである。

    **この関門は1箇所にしか無い。** 呼ぶ側で書き直すと、片方だけ先読みを通す
    形になりうる。
    """
    found = values.get((symbol, pd.Period(on, freq="M")))
    if found is None:
        return None
    when, value = found
    return None if when > on else value


@dataclasses.dataclass
class Panel:
    """分位を作った結果と、**作れなかったものの数**。

    **どう並べるかは呼ぶ側が決める。** ここは「材料が揃ったあと」だけを扱う。
    材料の小さい順に並べるので、**添字0が最小、末尾が最大**である。
    """

    months: list[dt.date]
    quantiles: list[tuple[float, ...]]
    members: list[tuple[frozenset[str], frozenset[str]]]
    counts: list[int]
    benchmark: list[float]
    skipped_thin: int
    skipped_no_value: int
    top_cutoff: list[float]
    """月ごとの、**上端の分位に入るのに要った材料の値**（その分位の最小値）。"""

    rejected_illiquid: list[list[float]]
    """月ごとの、**流動性で落とした銘柄の材料の値。** 何を削ったかを見るため。"""


def build_panel(  # noqa: PLR0913 - 事前登録が固定した条件をすべて受け取る
    database: Database,
    values: dict[tuple[str, pd.Period], _Observed],
    period: Period = Period.ALL,
    symbols: list[str] | None = None,
    benchmark: str = BENCHMARK,
    start: dt.date | None = None,
    end: dt.date | None = None,
    min_turnover: float = MIN_TURNOVER,
    min_symbols: int = MIN_SYMBOLS_PER_MONTH,
    quantiles: int = QUANTILES,
    snapshots: dict[dt.date, set[str]] | None = None,
) -> Panel:
    """月末の値で並べ、翌月のリターンを分位ごとに集める。

    **#9（PBR）と #14（時価総額）が同じものを呼ぶ。** 並べる材料しか違わない
    のに2つ書けば、片方だけ直したときに気付けない。

    Args:
        database: 価格の保存先。
        values: :func:`monthly_values` が返す表。**小さい順に並ぶ。**
        period: IS / OOS / ALL。
        symbols: 対象銘柄。省略時は ``market="JP"`` の全銘柄。
        benchmark: ベンチマーク。**暦もこれに合わせる。**
        start: この日より前の組み替え日を使わない。
        end: **この日より後のデータを1つも使わない。** 退場日まで見る。
        min_turnover: 流動性の下限（円）。
        min_symbols: 分位を作るのに必要な最低銘柄数。
        quantiles: 分位数。
        snapshots: 日付ごとの名簿。**渡さないと生存バイアスが入る。**

    Returns:
        :class:`Panel`。

    Raises:
        ValueError: ベンチマークの価格が無いか、組み替え日が足りない。
    """
    from stock_ai.database.repository import list_securities

    with database.session() as session:
        price_repo = PriceRepository(session)
        bench_raw = price_repo.get_raw_prices(benchmark)
        if bench_raw.empty:
            raise ValueError(f"ベンチマーク {benchmark!r} の価格が無い。暦を決められない。")
        bench = split_adjusted(bench_raw)
        calendar = bench.index
        bench_open = bench[OPEN].to_numpy(dtype=float)
        grid = build_grid(calendar, formation_dates(calendar), period, start, end)

        ordered_snapshots = sorted(snapshots) if snapshots else []
        if symbols is None:
            symbols = [sym for sym, market in list_securities(session) if market == "JP"]
        targets = [symbol for symbol in symbols if symbol != benchmark]

        buckets: dict[int, list[tuple[float, float, str]]] = {index: [] for index, _ in grid.usable}
        rejected: dict[int, list[float]] = {index: [] for index, _ in grid.usable}
        no_value = 0

        for symbol in targets:
            raw = price_repo.get_raw_prices(symbol)
            if raw.empty:
                continue
            adjusted = split_adjusted(raw)
            opens = adjusted[OPEN].to_numpy(dtype=float)
            closes = adjusted[CLOSE].to_numpy(dtype=float)
            volumes = adjusted[VOLUME].to_numpy(dtype=float)
            own = adjusted.index

            for index, position in grid.usable:
                on = calendar[position].date()
                if ordered_snapshots and not listed_on(
                    symbol, on, ordered_snapshots, snapshots, False, set()
                ):
                    continue
                value = value_on(values, symbol, on)
                if value is None:
                    no_value += 1
                    continue

                own_position = own.searchsorted(calendar[position])
                if own_position >= len(own) or own.values[own_position] != calendar[position]:
                    continue
                entry = own_position + 1
                exit_at = own.searchsorted(calendar[grid.exit_at(index)])
                if entry >= len(own) or exit_at >= len(own) or exit_at <= entry:
                    continue
                if not (opens[entry] > 0) or not (opens[exit_at] > 0):
                    continue
                window = closes[max(0, own_position - TURNOVER_WINDOW) : own_position + 1]
                traded = volumes[max(0, own_position - TURNOVER_WINDOW) : own_position + 1]
                level = float(np.median(window * traded)) if len(window) else float("nan")
                if not np.isfinite(level) or level < min_turnover:
                    # **削った側も残す。** 何を削ったのかは、残ったものからは
                    # 見えない（#14 の流動性の絞りは小型株をまるごと削る）。
                    rejected[index].append(value)
                    continue
                buckets[index].append((value, float(opens[exit_at] / opens[entry] - 1.0), symbol))

    months: list[dt.date] = []
    rows: list[tuple[float, ...]] = []
    members: list[tuple[frozenset[str], frozenset[str]]] = []
    counts: list[int] = []
    bench_returns: list[float] = []
    cutoffs: list[float] = []
    illiquid: list[list[float]] = []
    thin = 0

    for index, position in grid.usable:
        holding = buckets[index]
        if len(holding) < max(min_symbols, quantiles):
            thin += 1
            continue
        entry = bench_open[position + 1]
        leave = bench_open[grid.exit_at(index)]
        if not (entry > 0) or not (leave > 0):
            thin += 1
            continue
        holding.sort(key=lambda row: row[0])
        size = len(holding) // quantiles
        groups = [holding[step * size : (step + 1) * size] for step in range(quantiles)]
        rows.append(tuple(float(np.mean([row[1] for row in group])) for group in groups))
        members.append(
            (
                frozenset(row[2] for row in groups[0]),
                frozenset(row[2] for row in groups[-1]),
            )
        )
        months.append(calendar[position].date())
        counts.append(len(holding))
        bench_returns.append(float(leave / entry - 1.0))
        cutoffs.append(min(row[0] for row in groups[-1]))
        illiquid.append(rejected[index])

    return Panel(
        months=months,
        quantiles=rows,
        members=members,
        counts=counts,
        benchmark=bench_returns,
        skipped_thin=thin,
        skipped_no_value=no_value,
        top_cutoff=cutoffs,
        rejected_illiquid=illiquid,
    )
