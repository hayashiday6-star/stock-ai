"""低ボラティリティの月次系列を作る。分位ごとの平均リターンまで。

`lowvol_census` が「何件あるか」を数えたのに対し、こちらは**月ごとのリターンの
列**を作る。合否は出さない。用途は #6 と同じ2つ——検出力の事前計算と、生存
バイアスの実測である。

## #6（リバーサル）との違い

**月次リバランスなので、観測が重ならない。** #6 は毎営業日エントリーして20日
持つ形だったので、隣り合う観測が19/20の日を共有し、標準誤差が 2.95倍に膨らんだ。
ここでは月末に組み替えて翌月末まで持つので、**窓が重ならない。**

そのぶん Newey-West のラグは短くてよい。ただし 0 にはしない——月次の
ポートフォリオ・リターンにも残差の自己相関はありうるので、3ヶ月ぶんを見る。

## 指標を3つ持つ理由

#6 で、主要指標（分位1 − ベンチマーク）が**2つのものを混ぜて**測っていたことが
判定後に分かった。5分位すべてがベンチマークを下回っていて、差の大半は
「等金額で持った銘柄群が時価総額加重の指数に負けた」分だった。

同じ轍を踏まないために、**分位1 − 全分位平均**を最初から持つ。これは等金額
どうしの比較なので、加重方式の差が入らない。

- ``long_only()`` … 分位1 − ベンチマーク（**主要。実行できる形**）
- ``vs_average()`` … 分位1 − 全分位平均（**加重方式の差が入らない**）
- ``long_short()`` … 分位1 − 分位5（空売り前提。実行可能性は確かめていない）
- ``beta_adjusted()`` … 分位1 − β×ベンチマーク（**仮説に合う指標**）

最後のものが必要な理由は、封印前の検出力計算で分かった。仮説は「**リスク調整後
で**高い」と言っているのに、生のリターン差を主要指標にしていた。低ボラの分位1は
β が 1 より小さいので、市場が動いた月は生の差が必ず一方向に出る。その上下動が
分散のほとんどを作り、必要な差が 年7.0% にまで上がっていた。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from stock_ai.backtest.lowvol_census import formation_dates
from stock_ai.backtest.pead import MIN_TURNOVER, TURNOVER_WINDOW, Period
from stock_ai.backtest.reversal import BENCHMARK, MAX_SESSION_MOVE
from stock_ai.backtest.reversal_census import QUANTILES
from stock_ai.core.logging import get_logger
from stock_ai.data.schema import CLOSE, OPEN, VOLUME, split_adjusted
from stock_ai.database.engine import Database
from stock_ai.database.repository import PriceRepository, list_securities

logger = get_logger(__name__)

#: 測定窓（営業日）。**センサスで決めた値である。**
#:
#: 60/120/250 を数えた結果、観測は 60→250 で 4.6% しか減らない一方、分位1の
#: 月をまたぐ残存率は 76.0% → 88.5% に上がった。実効費用は年 1.15% → 0.55%。
#: **どの基準でも 250 が良い。**
DEFAULT_WINDOW = 250

#: 分位を作るのに必要な最低銘柄数。**1分位あたり20銘柄。**
#:
#: センサスで、1ヶ月あたりの通過銘柄数の最小が 10 だった。5分位にすると2銘柄
#: ずつになり、その月の分位平均は個別銘柄のリターンそのものになる。100 を
#: 下回る月は 289ヶ月中 **1ヶ月**しかないので、落としても失うものは無い。
MIN_SYMBOLS_PER_MONTH = 100

#: 1往復の費用。#6 と同じ 0.40%（ロングオンリー）。
ROUND_TRIP_COST = 0.004

#: 月ごとに入れ替わる割合。**センサスの実測値（250日窓）。**
#:
#: 仮定ではない。分位1の構成が月をまたいで 88.5% 残るので、毎月動くのは
#: 11.5% である。実効費用は ``ROUND_TRIP_COST × MONTHLY_TURNOVER``。
MONTHLY_TURNOVER = 0.115

#: 月あたりの費用のしきい値。0.40% × 11.5% ＝ **0.046%／月**（年 0.55%）。
#: #6 の 0.40%／20営業日（年 5.0%）の9分の1である。
COST_PER_MONTH = ROUND_TRIP_COST * MONTHLY_TURNOVER

#: Newey-West のラグ（月）。窓が重ならないので短くてよいが、0 にはしない。
DEFAULT_LAGS = 3

#: 推定期間（2002-07〜2013-12）で測った分位1のβ。**封印済みの値である。**
#:
#: 判定期間からは取らない。共分散の比なので平均は返らないが、判定期間から
#: 推定すれば「その期間に合う調整」を選んだことになる。
SEALED_BETA = 0.542

#: §8 で事前に計算した「t≥2.0 に必要な差」（α、月あたり）。**封印済み。**
#:
#: 2002–2013 の分散から、判定150ヶ月・Newey-West(3) で求めた。判定期間は
#: 1ヶ月あたりの銘柄数が推定期間の2倍あるので実際の標準誤差はこれより小さく
#: なる方向だが、**見てから下げるのは事後的な緩和**なのでこの値を使う。
DETECTABLE = 0.0033

#: 合格に要する t。
TARGET_T = 2.0

#: #7 の判定で実際に出た t（`PREREG_LOWVOL_JP.md` §18）。**判定には使わない。**
#:
#: 後から作った盤面が同じ universe を通っているかを確かめる検算に使う。
#: 再現しなければ、どこかで違うフィルタを通している。
JUDGED_T = 1.70

PASS = "合格"
FAIL = "不合格"


def verdict(
    alpha: float,
    t_statistic: float,
    cost: float = COST_PER_MONTH,
    detectable: float = DETECTABLE,
    target_t: float = TARGET_T,
) -> tuple[str, str]:
    """封印済みの表に当てはめる（`PREREG_LOWVOL_JP.md` §16）。

    **判定を人が読んで解釈しない。** #3 では結果を見てから基準が動きかけた。
    #6 で当てはめをコードにして、それは正しかった。

    Args:
        alpha: 分位1 − β×ベンチマークの平均（月あたり）。
        t_statistic: Newey-West(3) の t。
        cost: 費用のしきい値。既定は実測から作った 0.046%／月。
        detectable: 事前に計算した必要な差。
        target_t: 合格に要する t。

    Returns:
        ``(合否, 読み方)``。
    """
    if alpha <= 0:
        return FAIL, "効果なし。説を閉じる。"
    if alpha < cost:
        return FAIL, (
            f"α はあるが費用（{cost * 100:.3f}%／月）を賄えない。実行できないので閉じる。"
        )
    if t_statistic >= target_t:
        return PASS, f"α が費用を超え、t≥{target_t} を満たした。"
    if alpha < detectable:
        return FAIL, (
            f"**構造的な帯（{cost * 100:.3f}%〜{detectable * 100:.2f}%／月）に入った。**"
            "儲かる水準だが、この検出力では有意にならない。「効果が無い」ではない。"
            "基準は緩めず、前向きに貯める。"
        )
    return FAIL, (
        f"α は必要な差（{detectable * 100:.2f}%）を超えたが、t が届かなかった。前向き検証へ。"
    )


def break_even_alpha(beta: float, benchmark_mean: float) -> float:
    """指数を上回るのに必要な α。

    分位1のリターン − 指数 ＝ ``α − (1 − β) × 市場``。**α が正でも、上げ相場
    では指数に負ける。** 低ボラの見返りはリターンではなく下落の浅さで来るので、
    これは欠陥ではなく性質である。判定のたびに併記する。
    """
    return (1.0 - beta) * benchmark_mean


def max_drawdown(returns: Sequence[float]) -> float:
    """月次リターンから最大下落率を返す（負の値）。

    **低ボラの見返りは、リターンではなく下落の浅さで来る。** α が帯に入って
    不合格でも、ここが指数より浅ければ、それは記録に値する事実である。
    """
    peak = 1.0
    level = 1.0
    worst = 0.0
    for value in returns:
        level *= 1.0 + value
        peak = max(peak, level)
        worst = min(worst, level / peak - 1.0)
    return worst


@dataclass(frozen=True)
class LowVolSeries:
    """月次の分位リターンと、それがどう作られたかの記録。

    **合否は含まない。** 判定は事前登録の側で行う。
    """

    months: list[dt.date]
    counts: list[int]
    quantiles: list[tuple[float, ...]]
    """分位ごとの平均リターン。**分位1（添字0）が最も低ボラな側＝買う側。**"""
    benchmark: list[float]
    symbols_scanned: int = 0
    excluded_no_history: int = 0
    excluded_thin: int = 0
    excluded_no_window: int = 0
    excluded_discontinuity: int = 0
    excluded_thin_month: int = 0
    """最低銘柄数に届かなかった月。"""
    universe_label: str = "db"
    cross_sections: list[list[tuple[float, float]]] = field(default_factory=list)
    """月ごとの ``(ボラティリティ, 翌月リターン)``。``months`` と同じ並び。

    **分位に潰す前の断面である。** 分位ソートは上下2割しか使わず、真ん中の
    6割を捨てている。ほかの推定量と比べるには、潰す前が要る。

    ここに持たせているのは、**同じ universe・同じフィルタを通った断面で
    なければ、推定量どうしの比較にならない**からである。別経路で組み直すと、
    比が「推定量の差」ではなく「フィルタの差」を含む。
    """

    def long_only(self) -> list[float]:
        """主要指標。分位1 − ベンチマーク。**実行できる形で測る。**"""
        return [row[0] - bench for row, bench in zip(self.quantiles, self.benchmark, strict=True)]

    def vs_average(self) -> list[float]:
        """分位1 − 全分位平均。**加重方式の差が入らない。**

        #6 では主要指標に「等金額 対 時価総額加重」が混ざっていて、判定後に
        気付いた。等金額どうしを比べれば、その混ざりは構造的に入らない。
        """
        return [row[0] - sum(row) / len(row) for row in self.quantiles]

    def long_short(self) -> list[float]:
        """分位1 − 分位5。**空売り前提。実行可能性は確かめていない。**"""
        return [row[0] - row[-1] for row in self.quantiles]

    def beta_to_benchmark(self) -> float:
        """分位1のベンチマークに対するβ（最小二乗）。

        **仮説は「リスク調整後で高い」と言っている。** 生のリターン差を測ると、
        効果と「市場に対する感応度が低いこと」が混ざる。低ボラの分位1は β が
        1 より小さいはずなので、市場が上げた月は生の差が必ずマイナスに出る。
        その上下動が分散のほとんどを作り、検出力を食う。

        β を推定する期間は、**判定に使わない期間に限る。** 共分散の比なので
        平均は返らないが、判定期間から取れば期間を選んだことになる。

        Raises:
            ValueError: 月が2つ未満、またはベンチマークが動かない。
        """
        if len(self.benchmark) < 2:
            raise ValueError("β を推定するには2ヶ月以上が要る。")
        first = [row[0] for row in self.quantiles]
        mean_x = sum(self.benchmark) / len(self.benchmark)
        mean_y = sum(first) / len(first)
        variance = sum((x - mean_x) ** 2 for x in self.benchmark)
        if variance <= 0:
            raise ValueError("ベンチマークが動いていないので β を推定できない。")
        covariance = sum(
            (x - mean_x) * (y - mean_y) for x, y in zip(self.benchmark, first, strict=True)
        )
        return covariance / variance

    def beta_adjusted(self, beta: float) -> list[float]:
        """分位1 − β×ベンチマーク。**仮説に合う指標。**

        β は外から渡す。**この系列自身から推定した β を当てると、判定期間の
        情報でその期間を調整することになる。** 推定は判定に使わない期間で行い、
        固定した値を渡す。
        """
        return [
            row[0] - beta * bench for row, bench in zip(self.quantiles, self.benchmark, strict=True)
        ]

    def summary(self) -> str:
        """1行の要約。**平均リターンは出さない。**"""
        return (
            f"{self.universe_label}: {len(self.months)} ヶ月、"
            f"1ヶ月あたり中央値 {int(np.median(self.counts)) if self.counts else 0} 銘柄"
        )


def build_series(
    database: Database,
    period: Period = Period.ALL,
    symbols: list[str] | None = None,
    benchmark: str = BENCHMARK,
    start: dt.date | None = None,
    end: dt.date | None = None,
    window: int = DEFAULT_WINDOW,
    min_turnover: float = MIN_TURNOVER,
    min_symbols: int = MIN_SYMBOLS_PER_MONTH,
    quantiles: int = QUANTILES,
    snapshots: dict[dt.date, set[str]] | None = None,
    survivors_only: bool = False,
) -> LowVolSeries:
    """月次の分位リターンを作る。

    Args:
        database: 価格の保存先。
        period: IS / OOS / ALL。
        symbols: 対象銘柄。省略時は ``market="JP"`` の全銘柄。
        benchmark: ベンチマーク。**暦もこれに合わせる。**
        start: この日より前の組み替え日を使わない。
        end: **この日より後のデータを1つも使わない。** 組み替え日だけでなく、
            その月の退場日まで見る。組み替え日だけで切ると保有期間が越えて伸び、
            推定期間の最後の1ヶ月が判定期間の最初の月と重なる。
        window: ボラティリティの測定窓（営業日）。
        min_turnover: 流動性の下限（円）。
        min_symbols: 分位を作るのに必要な最低銘柄数。
        quantiles: 分位数。
        snapshots: 日付ごとの名簿。``None`` なら DB にある銘柄をそのまま使う。
        survivors_only: 最新の名簿を全期間に当てる（生存バイアスの対照）。

    Raises:
        ValueError: ベンチマークの価格が無い、または期間に組み替え日が無い。
    """
    with database.session() as session:
        price_repo = PriceRepository(session)
        bench_raw = price_repo.get_raw_prices(benchmark)
        if bench_raw.empty:
            raise ValueError(f"ベンチマーク {benchmark!r} の価格が無い。暦を決められない。")
        bench = split_adjusted(bench_raw)
        calendar = bench.index
        bench_open = bench[OPEN].to_numpy(dtype=float)
        formations = formation_dates(calendar)
        if len(formations) < 2:
            raise ValueError("組み替え日が2つ未満。月次リバランスを作れない。")

        # **``end`` は「この日より後のデータを1つも使わない」という意味である。**
        #
        # 組み替え日だけで切ると、その月の保有期間が ``end`` を越えて伸びる。
        # 実際 2013-12-31 で切ったつもりの推定期間は、最後の1ヶ月の**リターンが
        # 2014年1月まで**入っていた——判定期間の最初の月である。1/138 の重みで
        # しかないが、止め具が漏れていること自体が問題なので、退場日まで見る。
        usable = [
            (index, position)
            for index, position in enumerate(formations[:-1])
            if period.contains(calendar[position].date())
            and (start is None or calendar[position].date() >= start)
            and (
                end is None
                or (
                    formations[index + 1] + 1 < len(calendar)
                    and calendar[formations[index + 1] + 1].date() <= end
                )
            )
        ]
        if not usable:
            raise ValueError("指定した期間に組み替え日が1つも無い。")

        ordered_snapshots = sorted(snapshots) if snapshots else []
        latest = snapshots[ordered_snapshots[-1]] if ordered_snapshots else set()

        if symbols is None:
            symbols = [sym for sym, market in list_securities(session) if market == "JP"]
        targets = [symbol for symbol in symbols if symbol != benchmark]

        buckets: dict[int, list[tuple[float, float]]] = {index: [] for index, _ in usable}
        no_history = thin = no_window = discontinuous = 0

        for symbol in targets:
            raw = price_repo.get_raw_prices(symbol)
            if raw.empty:
                continue
            adjusted = split_adjusted(raw).reindex(calendar)
            close = adjusted[CLOSE].to_numpy(dtype=float)
            opens = adjusted[OPEN].to_numpy(dtype=float)
            floor = (
                (raw[CLOSE] * raw[VOLUME])
                .rolling(TURNOVER_WINDOW)
                .mean()
                .shift(1)
                .reindex(calendar)
                .to_numpy(dtype=float)
            )
            filled = adjusted[CLOSE].ffill().to_numpy(dtype=float)
            with np.errstate(divide="ignore", invalid="ignore"):
                step = filled[1:] / filled[:-1]
            broken = np.zeros(len(calendar), dtype=bool)
            broken[1:] = np.isfinite(step) & (np.abs(step - 1.0) > MAX_SESSION_MOVE)
            breaks = np.concatenate(([0], np.cumsum(broken)))
            returns = np.full(len(calendar), np.nan)
            with np.errstate(divide="ignore", invalid="ignore"):
                returns[1:] = close[1:] / close[:-1] - 1.0

            for index, position in usable:
                if ordered_snapshots and not _listed(
                    symbol,
                    calendar[position].date(),
                    ordered_snapshots,
                    snapshots,
                    survivors_only,
                    latest,
                ):
                    continue
                if position < window:
                    no_history += 1
                    continue
                entry = position + 1
                exit_at = formations[index + 1] + 1
                if exit_at >= len(calendar):
                    no_window += 1
                    continue
                level = floor[position]
                if not np.isfinite(level) or level < min_turnover:
                    thin += 1
                    continue
                if breaks[exit_at + 1] - breaks[position - window + 1] > 0:
                    discontinuous += 1
                    continue
                if not (opens[entry] > 0) or not (opens[exit_at] > 0):
                    no_window += 1
                    continue
                sample = returns[position - window + 1 : position + 1]
                if not np.isfinite(sample).all():
                    no_history += 1
                    continue
                buckets[index].append(
                    (float(np.std(sample, ddof=1)), float(opens[exit_at] / opens[entry] - 1.0))
                )

    months: list[dt.date] = []
    counts: list[int] = []
    rows: list[tuple[float, ...]] = []
    bench_returns: list[float] = []
    sections: list[list[tuple[float, float]]] = []
    thin_month = 0
    for index, position in usable:
        members = buckets[index]
        if len(members) < max(min_symbols, quantiles):
            thin_month += 1
            continue
        exit_at = formations[index + 1] + 1
        entry = bench_open[position + 1]
        leave = bench_open[exit_at]
        if not (entry > 0) or not (leave > 0):
            thin_month += 1
            continue
        members.sort(key=lambda pair: pair[0])
        size = len(members)
        sections.append(list(members))
        means: list[float] = []
        for bucket in range(quantiles):
            lo = bucket * size // quantiles
            hi = (bucket + 1) * size // quantiles
            chunk = [forward for _vol, forward in members[lo:hi]]
            means.append(sum(chunk) / len(chunk))
        months.append(calendar[position].date())
        counts.append(size)
        rows.append(tuple(means))
        bench_returns.append(float(leave / entry - 1.0))

    label = "snapshots" if snapshots and not survivors_only else "db"
    if snapshots and survivors_only:
        label = "survivors"
    series = LowVolSeries(
        months=months,
        counts=counts,
        quantiles=rows,
        benchmark=bench_returns,
        symbols_scanned=len(targets),
        excluded_no_history=no_history,
        excluded_thin=thin,
        excluded_no_window=no_window,
        excluded_discontinuity=discontinuous,
        excluded_thin_month=thin_month,
        universe_label=label,
        cross_sections=sections,
    )
    logger.info("低ボラ月次系列: %s", series.summary())
    return series


def _listed(  # noqa: PLR0913 - 名簿の判定に必要な材料をすべて受け取る
    symbol: str,
    on: dt.date,
    ordered: list[dt.date],
    snapshots: dict[dt.date, set[str]] | None,
    survivors_only: bool,
    latest: set[str],
) -> bool:
    """``on`` の時点で ``symbol`` が上場していたか。

    **その日以前で最も新しい名簿だけを見る。** 未来の名簿を混ぜると、まだ
    上場していない銘柄を過去の分位に入れることになる。
    """
    if survivors_only:
        return symbol in latest
    if snapshots is None:
        return True
    usable = [when for when in ordered if when <= on]
    return bool(usable) and symbol in snapshots[usable[-1]]
