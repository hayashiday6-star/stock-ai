"""#9「買いにくい相場は高い」— PBR で並べた分位の月次リターン。

**格言は、割高な側が割安な側を上回ると言っている。** 世間の常識と逆向きである。
`docs/PREREG_ANTIVALUE_JP.md` に封印前の取り決めがある。

## #7 と何を共有し、何を分けたか

**いつ組み替えていつ降りるか**（`monthly_grid`）と、**その日に上場していたか**
（`listed_on`）は同じものを使う。2つ持つと、片方だけ直したときに気付けない。

**並べる材料だけが違う。** #7 はボラティリティ（価格から作る）、#9 は PBR
（原本から抜いた月末の値）。#7 が要る「窓ぶんの履歴」は、ここでは要らない。

## 先読みを入れない

月末の PBR で並べ、**その翌営業日の始値で入り、翌月末の翌営業日の始値で降りる。**
組み替え日より後の PBR は使わない（`_pbr_on` が日付で切る）。

## 入れ替わり率は測る。#7 の値を写さない

`MONTHLY_TURNOVER = 0.115` は**ボラで並べたときの実測値**である。PBR は株価で
毎日動くので、入れ替わりは違う。**転記すれば「実測値の見た目」だけができる。**
"""

from __future__ import annotations

import dataclasses
import datetime as dt

import numpy as np
import pandas as pd

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

#: 1往復の費用。#6・#7 と同じ。**これは写してよい**——売買のコストであって、
#: 並べ方には依らない。
ROUND_TRIP_COST = 0.004

#: 分位を作るのに要る最低銘柄数。#7 と同じ。
MIN_SYMBOLS_PER_MONTH = 100

#: `pbr` が 92% 以上埋まるのは 2009年から。2008年は 63% しかない。
#:
#: **埋まっている銘柄だけが選ばれると、断面が歪む。** 事前登録 §6 でそう決めた。
USABLE_FROM = dt.date(2009, 1, 1)


@dataclasses.dataclass
class AntiValueSeries:
    """月ごとの分位リターンと、入れ替わり。"""

    months: list[dt.date]
    quantiles: list[tuple[float, ...]]
    """分位ごとの月次リターン。**添字0が最も割安、末尾が最も割高。**"""

    members: list[tuple[frozenset[str], frozenset[str]]]
    """月ごとの（最も割安な分位、最も割高な分位）の顔ぶれ。"""

    counts: list[int]
    benchmark: list[float]
    skipped_thin: int
    skipped_no_pbr: int

    def spread(self) -> list[float]:
        """**高PBR − 低PBR。** 格言はこれが正だと予測する。"""
        return [row[-1] - row[0] for row in self.quantiles]

    def turnover(self) -> float:
        """両端の分位の、月をまたいだ入れ替わり率。**測る。写さない。**

        顔ぶれが 100件から 100件へ動くとき、消えた割合を取る。前月が無い
        最初の月は数えない。
        """
        changes = []
        for (cheap_now, rich_now), (cheap_before, rich_before) in zip(
            self.members[1:], self.members[:-1], strict=False
        ):
            for now, before in ((cheap_now, cheap_before), (rich_now, rich_before)):
                if before:
                    changes.append(1.0 - len(now & before) / len(before))
        return float(np.mean(changes)) if changes else 0.0

    def beta_to_benchmark(self) -> float:
        """スプレッドのベンチマークに対する β（最小二乗）。

        **ロング・ショートでも β は 0 ではない。** 両端の分位の感応度が違えば
        差にも市場が残る。割安な側は感応度が高いことが多いので、**市場が動いた
        月はスプレッドが一方向に出る。** その上下動が分散のほとんどを作り、
        検出力を食う。

        `cross_section.beta_to_benchmark` を呼ぶ。**同じ処理を2つ書かない。**

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
        return ROUND_TRIP_COST * self.turnover()

    def summary(self) -> str:
        """1行のまとめ。**平均は出さない**——§0 が判定を先食いしないため。"""
        if not self.months:
            return "分位を作れた月が1つも無い。**比べていない。**"
        return (
            f"{len(self.months)} ヶ月（{self.months[0]} 〜 {self.months[-1]}）、"
            f"1ヶ月あたり {int(np.mean(self.counts)):,} 銘柄。"
            f"入れ替わり {self.turnover():.1%}／月 → 費用 {self.cost_per_month():.3%}／月"
            f"（年 {self.cost_per_month() * 12:.2%}）。"
            f"薄くて飛ばした月 {self.skipped_thin}、PBR が無くて外した銘柄月 "
            f"{self.skipped_no_pbr:,}。"
        )


def _pbr_by_month(frame: pd.DataFrame) -> dict[tuple[str, pd.Period], tuple[dt.date, float]]:
    """（銘柄, 月）→（その月末の日付, PBR）。

    **月で引く。日付そのものでは引かない。** 月の途中で上場廃止になった銘柄の
    最後の観測は、ベンチマークの月末とは違う日である。日付で引くと、**その銘柄
    が丸ごと落ちる。**
    """
    found: dict[tuple[str, pd.Period], tuple[dt.date, float]] = {}
    if frame.empty:
        return found
    months = pd.to_datetime(frame["date"]).dt.to_period("M")
    for (symbol, month), date, pbr in zip(
        zip(frame["symbol"], months, strict=True),
        frame["date"],
        frame["pbr"],
        strict=True,
    ):
        found[(symbol, month)] = (date, float(pbr))
    return found


def build_series(  # noqa: PLR0913 - 事前登録が固定した条件をすべて受け取る
    database: Database,
    valuation: pd.DataFrame,
    period: Period = Period.ALL,
    symbols: list[str] | None = None,
    benchmark: str = BENCHMARK,
    start: dt.date | None = None,
    end: dt.date | None = None,
    min_turnover: float = MIN_TURNOVER,
    min_symbols: int = MIN_SYMBOLS_PER_MONTH,
    quantiles: int = QUANTILES,
    snapshots: dict[dt.date, set[str]] | None = None,
) -> AntiValueSeries:
    """月末の PBR で並べ、翌月のリターンを分位ごとに集める。

    Args:
        database: 価格の保存先。
        valuation: :func:`~stock_ai.data.valuation_monthly.read` が返す表。
        period: IS / OOS / ALL。
        symbols: 対象銘柄。省略時は ``market="JP"`` の全銘柄。
        benchmark: ベンチマーク。**暦もこれに合わせる。**
        start: この日より前の組み替え日を使わない。既定は 2009-01-01。
        end: **この日より後のデータを1つも使わない。**
        min_turnover: 流動性の下限（円）。
        min_symbols: 分位を作るのに必要な最低銘柄数。
        quantiles: 分位数。
        snapshots: 日付ごとの名簿。**渡さないと生存バイアスが入る。**

    Returns:
        :class:`AntiValueSeries`。

    Raises:
        ValueError: ベンチマークの価格が無いか、組み替え日が足りない。
    """
    from stock_ai.database.repository import list_securities

    floor = USABLE_FROM if start is None else max(start, USABLE_FROM)
    pbr_of = _pbr_by_month(valuation)

    with database.session() as session:
        price_repo = PriceRepository(session)
        bench_raw = price_repo.get_raw_prices(benchmark)
        if bench_raw.empty:
            raise ValueError(f"ベンチマーク {benchmark!r} の価格が無い。暦を決められない。")
        bench = split_adjusted(bench_raw)
        calendar = bench.index
        bench_open = bench[OPEN].to_numpy(dtype=float)
        grid = build_grid(calendar, formation_dates(calendar), period, floor, end)

        ordered_snapshots = sorted(snapshots) if snapshots else []
        if symbols is None:
            symbols = [sym for sym, market in list_securities(session) if market == "JP"]
        targets = [symbol for symbol in symbols if symbol != benchmark]

        buckets: dict[int, list[tuple[float, float, str]]] = {index: [] for index, _ in grid.usable}
        no_pbr = 0

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
                month = pd.Period(on, freq="M")
                found = pbr_of.get((symbol, month))
                if found is None:
                    no_pbr += 1
                    continue
                pbr_date, pbr = found
                # **組み替え日より後の PBR を使わない。** 先読みである。
                if pbr_date > on:
                    no_pbr += 1
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
                    continue
                buckets[index].append((pbr, float(opens[exit_at] / opens[entry] - 1.0), symbol))

    months: list[dt.date] = []
    rows: list[tuple[float, ...]] = []
    members: list[tuple[frozenset[str], frozenset[str]]] = []
    counts: list[int] = []
    bench_returns: list[float] = []
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

    return AntiValueSeries(
        months=months,
        quantiles=rows,
        members=members,
        counts=counts,
        benchmark=bench_returns,
        skipped_thin=thin,
        skipped_no_pbr=no_pbr,
    )
