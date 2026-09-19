"""#12「トレンドは友」— 過去12ヶ月（直近1ヶ月を除く）で並べた分位の月次リターン。

**格言は、上がっている銘柄は上がり続けると言っている。**
`docs/PREREG_MOMENTUM_JP.md` に封印前の取り決めがある。

## #9 と何を共有し、何を分けたか

**いつ組み替えていつ降りるか**（`monthly_grid`）、**その日に上場していたか**
（`listed_on`）、**分位が出来たあとの読み方**（`QuantileSeries`）は同じものを
使う。2つ持つと、片方だけ直したときに気付けない。

**並べる材料だけが違う。** #9 は PBR（原本から抜いた月末の値）、ここは
**その銘柄自身の過去の終値**である。外部の表を読まないので、`pbr` のような
欠測は無い——代わりに**13ヶ月分の月末が要る。**

## 設計は文献の標準形に固定してある。**外れない**

形成12ヶ月・**直近1ヶ月スキップ**・保有1ヶ月・5分位・等加重・月次
（事前登録 §3）。

**#10 は設計の選び方で IS の `t` が 2.8倍振れて、封印できなかった。**
形成期間を 3/6/12 と試して良いものを選べば、**選んだこと自体が多重検定**
になる。標準形に固定するのは、**こちらが選んでいないから**である。

## 先読みを入れない

組み替え日 `D`（月末）の時点で、**`D` より後の価格を1つも読まない。**
形成期間は `D` の13ヶ月前の月末から、**1ヶ月前の月末まで**である。

**スキップ月を形成期間に入れない。** 入れれば短期反転と混ざり、
「モメンタムを測った」ことにならない。

## 古すぎる価格を黙って使わない

月末に足の無い銘柄（売買停止など）は、**その直前の足**で代用する。ただし
`STALE_LIMIT_DAYS` より古ければ**使わずに外し、件数を数える。**

**代用そのものが危ないのではない。** 危ないのは、**何年も前の価格が「その月の
値」として黙って入ること**である。月末は約30日おきなので、1ヶ月以上古ければ
その銘柄はその時期に動いていない。
"""

from __future__ import annotations

import dataclasses
import datetime as dt

import numpy as np
import pandas as pd

from stock_ai.backtest.lowvol import MIN_SYMBOLS_PER_MONTH
from stock_ai.backtest.lowvol_census import formation_dates
from stock_ai.backtest.monthly_grid import build_grid, listed_on
from stock_ai.backtest.pead import MIN_TURNOVER, TURNOVER_WINDOW, Period
from stock_ai.backtest.quantile_series import QuantileSeries
from stock_ai.backtest.reversal import BENCHMARK
from stock_ai.backtest.reversal_census import QUANTILES
from stock_ai.core.logging import get_logger
from stock_ai.data.schema import CLOSE, OPEN, VOLUME, split_adjusted
from stock_ai.database.engine import Database
from stock_ai.database.repository import PriceRepository

logger = get_logger(__name__)

#: 形成期間（月）。**事前登録 §3 で固定した。動かさない。**
FORMATION_MONTHS = 12

#: スキップする月数。**直近1ヶ月を除く**——短期反転と混ざらないようにする。
SKIP_MONTHS = 1

#: 2009年より前は使わない。**#9 と同じ日付だが、理由は違う。**
#:
#: #9 は `pbr` が 2009年から 92% 埋まるからである。ここは価格しか使わないので
#: その制約は無い。**揃えるのは読み比べられるようにするため**で、事前登録 §6
#: にそう書いた。
#:
#: **価格はもっと前から読む。** 2009-01 の断面を作るには 2007-12 の月末が要る。
#: 読むことと判定に使うことは別である。
USABLE_FROM = dt.date(2009, 1, 1)

#: 月末の代わりに使ってよい足の古さ（日）。
#:
#: 月末は約30日おきなので、**1ヶ月以上古い足は「その月の値」ではない。**
#: 31 は暦から出した値であって、測って決めたものではない——そう書いておく。
STALE_LIMIT_DAYS = 31


@dataclasses.dataclass
class MomentumSeries(QuantileSeries):
    """月ごとの分位リターン。**添字0が最も負けている、末尾が最も勝っている。**

    **読み方は `QuantileSeries` に置いてある。** ここに残すのは、この説に
    固有の数え落としだけ。

    **`spread()` は「勝者 − 敗者」になる**（上端 − 下端）。格言はこれが正だと
    予測する。
    """

    skipped_thin: int = 0
    skipped_no_history: int = 0
    """形成期間の13ヶ月分が揃わなかった銘柄月。**新規上場はここに入る。**"""

    skipped_stale: int = 0
    """足が `STALE_LIMIT_DAYS` より古くて外した銘柄月。"""

    def summary(self) -> str:
        """1行のまとめ。**平均は出さない**——§0 が判定を先食いしないため。"""
        if not self.months:
            return "分位を作れた月が1つも無い。**比べていない。**"
        return (
            f"{len(self.months)} ヶ月（{self.months[0]} 〜 {self.months[-1]}）、"
            f"1ヶ月あたり {int(np.mean(self.counts)):,} 銘柄。"
            f"入れ替わり {self.turnover():.1%}／月 → 費用 {self.cost_per_month():.3%}／月"
            f"（年 {self.cost_per_month() * 12:.2%}）。"
            f"薄くて飛ばした月 {self.skipped_thin}、履歴が足りず外した銘柄月 "
            f"{self.skipped_no_history:,}、足が古くて外した銘柄月 {self.skipped_stale:,}。"
        )

    def warnings(self) -> list[str]:
        """気付かなくても目に入るべきこと。**表は読む側が気付く必要がある。**"""
        found: list[str] = []
        if not self.months:
            return ["**分位を作れた月が1つも無い。**"]
        if self.skipped_thin:
            found.append(f"**{self.skipped_thin} ヶ月は銘柄が足りず、飛ばしている。**")
        if self.skipped_stale:
            found.append(
                f"**{self.skipped_stale:,} 銘柄月を、足が {STALE_LIMIT_DAYS} 日より"
                "古いという理由で外した。** 売買停止が長い銘柄である。"
            )
        # **入れ替わりは費用を決める。** 事前登録 §0 の線は費用を賄えるかで
        # 置いてあるので、ここが大きければ線に届きにくくなる。
        if self.turnover() > 0.5:
            found.append(
                f"**入れ替わりが {self.turnover():.1%}／月ある。** "
                f"費用は年 {self.cost_per_month() * 12:.2%} になる。"
            )
        return found


def _as_of(own: pd.DatetimeIndex, when: pd.Timestamp) -> int | None:
    """``when`` 以前でいちばん新しい足の位置。**無ければ `None`。**

    **`when` より後は見ない。** 先読みである。

    Args:
        own: その銘柄の暦。
        when: 見たい日。

    Returns:
        位置。``when`` 以前の足が1本も無ければ ``None``。
    """
    position = int(own.searchsorted(when, side="right")) - 1
    return position if position >= 0 else None


def momentum_on(  # noqa: PLR0911 - 使えない理由ごとに別の答えを返す
    own: pd.DatetimeIndex,
    closes: object,
    calendar: pd.DatetimeIndex,
    formations: list[int],
    index: int,
    formation_months: int = FORMATION_MONTHS,
    skip_months: int = SKIP_MONTHS,
    stale_days: int = STALE_LIMIT_DAYS,
) -> tuple[float | None, str]:
    """組み替え ``index`` の時点で使ってよいモメンタム。

    形成期間は **``index - skip - formation`` の月末から ``index - skip`` の
    月末まで**である。組み替え日そのものの価格は**使わない**——スキップ月を
    除くとはそういう意味である。

    Args:
        own: その銘柄の暦。
        closes: その銘柄の調整後終値。
        calendar: ベンチマークの暦。**月の切れ目はこれで決める。**
        formations: 月末の位置（ベンチマークの暦の中）。
        index: 組み替え日の `formations` の中での添字。
        formation_months: 形成期間（月）。
        skip_months: スキップする月数。
        stale_days: 月末の代わりに使ってよい足の古さ。

    Returns:
        ``(モメンタム, 理由)``。使えたときの理由は ``"ok"``、使えないときは
        ``"no_history"`` か ``"stale"``。**理由を返すのは、呼ぶ側が別々に
        数えられるようにするため**——1つの数にまとめると、どちらで落ちたのか
        分からなくなる。

    Raises:
        ValueError: ``formation_months`` か ``skip_months`` が負、または
            形成期間が 1ヶ月未満。
    """
    if formation_months < 1 or skip_months < 0:
        raise ValueError(
            f"formation_months must be at least 1 and skip_months at least 0; "
            f"got {formation_months} and {skip_months}."
        )

    last = index - skip_months
    first = last - formation_months
    if first < 0:
        return None, "no_history"

    limit = pd.Timedelta(days=stale_days)
    prices: list[float] = []
    for step in (first, last):
        target = calendar[formations[step]]
        position = _as_of(own, target)
        if position is None:
            return None, "no_history"
        if target - own[position] > limit:
            return None, "stale"
        value = float(closes[position])  # type: ignore[index]
        if not (value > 0):
            return None, "stale"
        prices.append(value)

    return prices[1] / prices[0] - 1.0, "ok"


def build_series(  # noqa: PLR0913, PLR0912, PLR0915 - 事前登録が固定した条件をすべて受け取る
    database: Database,
    period: Period = Period.ALL,
    symbols: list[str] | None = None,
    benchmark: str = BENCHMARK,
    start: dt.date | None = None,
    end: dt.date | None = None,
    min_turnover: float = MIN_TURNOVER,
    min_symbols: int = MIN_SYMBOLS_PER_MONTH,
    quantiles: int = QUANTILES,
    snapshots: dict[dt.date, set[str]] | None = None,
    formation_months: int = FORMATION_MONTHS,
    skip_months: int = SKIP_MONTHS,
) -> MomentumSeries:
    """月末のモメンタムで並べ、翌月のリターンを分位ごとに集める。

    Args:
        database: 価格の保存先。
        period: IS / OOS / ALL。
        symbols: 対象銘柄。省略時は ``market="JP"`` の全銘柄。
        benchmark: ベンチマーク。**暦もこれに合わせる。**
        start: この日より前の組み替え日を使わない。既定は 2009-01-01。
        end: **この日より後のデータを1つも使わない。**
        min_turnover: 流動性の下限（円）。
        min_symbols: 分位を作るのに必要な最低銘柄数。
        quantiles: 分位数。
        snapshots: 日付ごとの名簿。**渡さないと生存バイアスが入る。**
        formation_months: 形成期間。**事前登録が固定している。動かさない。**
        skip_months: スキップ月。**同上。**

    Returns:
        :class:`MomentumSeries`。

    Raises:
        ValueError: ベンチマークの価格が無いか、組み替え日が足りない。
    """
    from stock_ai.database.repository import list_securities

    floor = USABLE_FROM if start is None else max(start, USABLE_FROM)

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
        no_history = 0
        stale = 0

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
                signal, reason = momentum_on(
                    own,
                    closes,
                    calendar,
                    grid.formations,
                    index,
                    formation_months=formation_months,
                    skip_months=skip_months,
                )
                if signal is None:
                    # **理由ごとに独立に数える。** 1つにまとめると、どちらで
                    # 落ちたのか分からなくなる（#5 で踏んだ形）。
                    if reason == "stale":
                        stale += 1
                    else:
                        no_history += 1
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
                buckets[index].append((signal, float(opens[exit_at] / opens[entry] - 1.0), symbol))

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

    logger.info(
        "#12 モメンタム: %d ヶ月、履歴不足 %d、古い足 %d、薄い月 %d",
        len(months),
        no_history,
        stale,
        thin,
    )
    return MomentumSeries(
        months=months,
        quantiles=rows,
        members=members,
        counts=counts,
        benchmark=bench_returns,
        skipped_thin=thin,
        skipped_no_history=no_history,
        skipped_stale=stale,
    )
