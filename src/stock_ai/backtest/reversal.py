"""短期リバーサルの日次系列を作る。分位ごとの平均フォワードリターンまで。

`reversal_census` が「何件あるか」を数えたのに対し、こちらは**日ごとの
リターンの列**を作る。ただし合否は出さない。この列は2つの用途に使う。

1. **検出力の事前計算**（`stock_ai.backtest.power`）。判定に使わない期間
   （2020年まで）の分散だけを見て、「OOS で t≥2.0 に必要な差」を封印前に
   出す。平均は見ない。
2. **生存バイアスの実測**。同じ期間を「日付ごとの名簿」と「いま生き残って
   いる銘柄だけ」の2通りで回し、差を取る。差がバイアスの大きさと符号になる。

## 窓の決め方

判定日 D の終値で分位を作り、**D+1 の寄付きで入り、D+1+保有日数 の寄付きで
出る**。寄り引けを混ぜないのは、片側だけ一晩ぶんずれた引き算がベンチマーク
との差に一方向の偏りを作るからである（`pead._benchmark_return` と同じ理由）。
D の終値と D+1 の寄付きの間が1日空いているので、終値で並べて終値で買う形の
微細構造は入らない。

## 営業日はベンチマークの暦で決める

センサスは銘柄ごとの暦で数えていたが、こちらはベンチマーク（1306）の暦に
そろえる。そうしないと、売買停止などで暦がずれた銘柄の「D+21」が他の銘柄と
別の日になり、同じ日の分位を足し合わせているつもりで別の窓を混ぜる。

**ずれた分は捨てるのではなく数える。** `excluded_calendar` が、D には値が
あるのに入退場の日に値が無かった銘柄日である。センサスの件数との差はここに
出る。推測で済ませない。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from stock_ai.backtest.pead import MIN_TURNOVER, TURNOVER_WINDOW, Period
from stock_ai.backtest.reversal_census import HOLDING_DAYS, LOOKBACK_DAYS, QUANTILES
from stock_ai.core.logging import get_logger
from stock_ai.data.schema import CLOSE, OPEN, VOLUME, split_adjusted
from stock_ai.database.engine import Database
from stock_ai.database.repository import PriceRepository, list_securities

logger = get_logger(__name__)

#: ベンチマーク。TOPIX連動ETF。暦もこれに合わせる。
BENCHMARK = "1306"

#: 判定に使える最初の日。**API が名指しした境界であって、逆算ではない。**
#:
#: 2026-09-04 に 2021-09-01 の名簿を頼んだところ、こう断られた。
#:
#:   ``Your subscription covers the following dates: 2021-09-04 ~``
#:
#: 30日刻みで集めた名簿の最も古いものは 2021-10-01 だったが、**それは刻みが
#: たまたまそこに乗っただけ**で、境界ではない。刻みから逆算していたら4週間ぶん
#: 取り逃していた。
#:
#: **これより前は今後も直せない。** 廃止銘柄の株価も同じ窓の中にしか無い。
#: しかも窓は解約を待たずに毎日後ろへ動く。
JUDGMENT_FROM = dt.date(2021, 9, 4)

#: 1往復の費用。ロングオンリーなので、両建て前提の 0.8% の半分。
#: 保有20営業日あたりの数字である。
COST_ROUND_TRIP = 0.004

#: エントリーまでに空ける営業日数。1 なら D+1 の寄付き。
#:
#: 主要指標は 1（D の終値で分位、D+1 の寄付きで入る）。2 を渡した版は副次で、
#: 微細構造由来かどうかの切り分けに使う（`PREREG_REVERSAL_JP.md` §5）。
DEFAULT_SKIP = 1

#: §7 で事前に計算した「t≥2.0 に必要な差」。**封印済みの値である。**
#:
#: 2001–2020 の分散から、OOS 630営業日・Newey-West(20) で求めた。判定期間は
#: 1分位あたりの銘柄数が当時より多いので、実際の標準誤差はこれより小さくなる
#: 方向だが、**見てから下げるのは事後的な緩和**なのでこの値を使う。
DETECTABLE = 0.0085

#: 1営業日でこれを超える動きは、日本株では値動きではない。
#:
#: **東証には値幅制限がある。** 制限は株価帯ごとに決まっていて、いちばん緩い
#: 低位株でも1日で ±50% を超えることはまず無い。超えているなら、価格の系列が
#: そこで**不連続**になっている——分割・併合か、売買停止をまたいだ再開である。
#:
#: 実測（8308、2005年）:
#:
#:   2005-07-26  終値     197   調整後     2.0   調整係数 0.0100
#:   （2005-07-27 〜 08-01 は足が無い＝併合による売買停止）
#:   2005-08-02  終値 204,000   調整後 2,040.0   調整係数 0.0100
#:
#: **調整係数は前後とも 0.0100 のまま**である。1:1000 の株式併合を、系列が
#: またいでいない。``adj_close`` 自体が不連続なので、``split_adjusted`` を
#: 通しても直らない。
#:
#: 検出は「直前に値のあった日」と比べる。暦に載せ替えたあとの NaN と比べると、
#: **売買停止を挟んだ併合が必ず素通りする**（8308 がまさにこれ）。
MAX_SESSION_MOVE = 0.5

#: これを超えるフォワードリターンは、20営業日の値動きとしては説明がつかない。
#: 実測では 8308 の 2005-07-25 が **+125,028%** で、これ1件だけで162銘柄の
#: 分位平均が +772% 動く。株価の動きではなく、分割・併合の調整漏れである。
#:
#: **除外はしない。数えるだけにする。** 黙って落とすと、同じ欠陥が別の場所で
#: 効いているときに気付けなくなる。
IMPLAUSIBLE_FORWARD = 1.0

#: 日付を受けて、その日に入場を許す銘柄集合を返す呼び出し。
UniverseAt = Callable[[dt.date], set[str]]


@dataclass(frozen=True)
class ReversalSeries:
    """日次の分位リターンと、それがどう作られたかの記録。

    **合否は含まない。** 判定は事前登録の側で行う。
    """

    days: list[dt.date]
    counts: list[int]
    """その日に分位を作れた銘柄数。"""
    quantiles: list[tuple[float, ...]]
    """分位ごとの平均フォワードリターン。**分位1（添字0）が最も下げた側。**"""
    benchmark: list[float]
    """同じ窓でベンチマークが動いた分。"""
    symbols_scanned: int = 0
    symbols_without_prices: int = 0
    excluded_no_lookback: int = 0
    excluded_thin: int = 0
    excluded_calendar: int = 0
    """D には値があるが、入退場の日にベンチマークの暦で値が無かった銘柄日。"""
    excluded_thin_day: int = 0
    """分位を作れる銘柄数に届かなかった営業日。"""
    universe_label: str = "db"
    """どの universe で回したか。生存バイアスの実測で取り違えないため。"""
    forward_percentiles: list[tuple[str, float]] = field(default_factory=list)
    """銘柄日ごとのフォワードリターンの分位。**日次系列の分散が説明できるか
    を確かめるためだけに持つ。**

    分位1に約160銘柄入るなら、平均のばらつきは個別のばらつきよりずっと
    小さくなるはずである。そうなっていなければ、平均が数件の極端値に
    引っ張られている——つまり測っているのは現象ではなくデータの傷である。
    """
    excluded_discontinuity: int = 0
    """窓が価格系列の不連続をまたいでいた銘柄日。

    **極端なリターンを捨てているのではなく、尺度の変わり目をまたぐ窓を捨てて
    いる。** 併合の前後で価格の単位が違うので、その2点を割り算した値は
    リターンではない。判定日側（5日リターン）と保有側の両方を見る——前者が
    汚れれば「大きく下げた」の中身が変わり、後者が汚れれば結果が変わる。
    """
    implausible: int = 0
    """フォワードリターンが ±100% を超えた銘柄日の数。

    **0 でなければ、この系列から出した分散は使えない。** 20営業日でそこまで
    動くのは値動きではなく、分割・併合の調整漏れである。
    """
    extremes: list[tuple[str, dt.date, float, float]] = field(default_factory=list)
    """(銘柄, 判定日, 5日リターン, フォワード) を絶対値の大きい順に。

    銘柄ごとに最悪の1件だけを見る。同じ銘柄で埋まると他が見えなくなるため。
    **+900% のような値が出たら分割・併合の調整漏れを疑う。**
    """

    def long_only(self) -> list[float]:
        """主要指標。**分位1 − ベンチマーク。**

        空売りを前提にしないのは、貸借銘柄が 60.1% しかなく、しかも
        `sSinyouC` が現在値なので過去に当てると先読みになるためである
        （`docs/HYPOTHESES.md`）。実行できない戦略で合格を出さない。
        """
        return [row[0] - bench for row, bench in zip(self.quantiles, self.benchmark, strict=True)]

    def long_short(self) -> list[float]:
        """副次指標。分位1 − 分位5。**実行可能性は確かめていない。**"""
        return [row[0] - row[-1] for row in self.quantiles]

    def summary(self) -> str:
        """1行の要約。**平均リターンは出さない。**"""
        return (
            f"{self.universe_label}: {len(self.days)} 営業日、"
            f"1日あたり中央値 {int(np.median(self.counts)) if self.counts else 0} 銘柄"
        )


def dated_universe(snapshots: dict[dt.date, set[str]]) -> UniverseAt:
    """日付ごとの名簿を使う（生存バイアスを直した側）。

    **その日以前で最も新しい名簿**を使う。名簿より前の日は空集合を返す。
    未来の名簿を混ぜると、まだ上場していない銘柄を過去の分位に入れることに
    なり、生存バイアスを直したつもりで先読みを持ち込む。
    """
    ordered = sorted(snapshots)

    def at(on: dt.date) -> set[str]:
        usable = [when for when in ordered if when <= on]
        return snapshots[usable[-1]] if usable else set()

    return at


def survivors_universe(snapshots: dict[dt.date, set[str]]) -> UniverseAt:
    """いま生き残っている銘柄だけを使う（バイアスが乗っている側）。

    最新の名簿を全期間に当てる。**これは意図的に先読みである。** 生存バイアス
    そのものを測るための対照であって、判定には使わない。
    """
    latest = snapshots[max(snapshots)] if snapshots else set()

    def at(on: dt.date) -> set[str]:  # noqa: ARG001 - 日付に依らないのが対照の定義
        return latest

    return at


def _snapshot_positions(
    calendar: pd.DatetimeIndex, ordered: list[dt.date], survivors_only: bool
) -> np.ndarray:
    """暦の各位置に、どの名簿を当てるかの添字。``-1`` は名簿より前。"""
    if survivors_only:
        return np.full(len(calendar), len(ordered) - 1, dtype=np.int64)
    boundaries = np.array([np.datetime64(when) for when in ordered], dtype="datetime64[ns]")
    return np.searchsorted(boundaries, calendar.to_numpy(), side="right") - 1


def build_series(
    database: Database,
    period: Period = Period.ALL,
    symbols: list[str] | None = None,
    benchmark: str = BENCHMARK,
    start: dt.date | None = None,
    end: dt.date | None = None,
    min_turnover: float = MIN_TURNOVER,
    lookback: int = LOOKBACK_DAYS,
    holding: int = HOLDING_DAYS,
    quantiles: int = QUANTILES,
    snapshots: dict[dt.date, set[str]] | None = None,
    survivors_only: bool = False,
    extremes: int = 12,
    skip: int = DEFAULT_SKIP,
) -> ReversalSeries:
    """日次の分位リターンを作る。

    Args:
        database: 価格の保存先。
        period: IS / OOS / ALL。
        symbols: 対象銘柄。省略時は ``market="JP"`` の全銘柄。
        benchmark: ベンチマーク。**暦もこれに合わせる。**
        start: この日より前の判定日を使わない。
        end: この日より後の判定日を使わない。**検出力の推定で判定期間に
            入り込まないための止め具。**
        min_turnover: 流動性の下限（円）。
        lookback: 「大きく下げた」を測る営業日数。
        holding: 保有営業日数。
        quantiles: 分位数。
        snapshots: 日付ごとの名簿。``None`` なら DB にある銘柄をそのまま使う。
        survivors_only: 最新の名簿を全期間に当てる（生存バイアスの対照）。
        extremes: フォワードリターンの絶対値が大きい銘柄日を何件持ち帰るか。
            **0 にしない。** 分散が説明できるかを確かめる唯一の手掛かりになる。
        skip: 判定日からエントリーまでに空ける営業日数。主要指標は 1。

    Raises:
        ValueError: ベンチマークの価格が無い。暦が決められない。
    """
    with database.session() as session:
        price_repo = PriceRepository(session)
        bench_raw = price_repo.get_raw_prices(benchmark)
        if bench_raw.empty:
            raise ValueError(
                f"ベンチマーク {benchmark!r} の価格が無い。暦を決められないので進めない。"
            )
        bench = split_adjusted(bench_raw)
        calendar = bench.index
        bench_open = bench[OPEN].to_numpy(dtype=float)

        total = len(calendar)
        if skip < 1:
            raise ValueError(f"skip must be at least 1; got {skip}.")
        exit_offset = skip + holding
        first = lookback
        last = total - exit_offset  # この位置までが退場日を持つ
        if last <= first:
            raise ValueError("ベンチマークの営業日が、1つの窓を作るにも足りない。")

        positions = np.arange(first, last, dtype=np.int64)
        day_dates = [calendar[index].date() for index in positions]
        keep = np.array(
            [
                period.contains(when)
                and (start is None or when >= start)
                and (end is None or when <= end)
                for when in day_dates
            ],
            dtype=bool,
        )
        positions = positions[keep]
        if positions.size == 0:
            raise ValueError("指定した期間に判定日が1日も無い。")

        ordered_snapshots = sorted(snapshots) if snapshots else []
        snapshot_at = (
            _snapshot_positions(calendar, ordered_snapshots, survivors_only)
            if ordered_snapshots
            else None
        )

        if symbols is None:
            symbols = [sym for sym, market in list_securities(session) if market == "JP"]
        targets = [symbol for symbol in symbols if symbol != benchmark]

        chunks_pos: list[np.ndarray] = []
        chunks_lb: list[np.ndarray] = []
        chunks_fwd: list[np.ndarray] = []
        no_prices = no_lookback = thin = calendar_gap = discontinuous = 0
        # 銘柄ごとに最悪の1件。同じ銘柄で枠が埋まると他の銘柄が見えなくなる。
        worst: list[tuple[float, str, int, float, float]] = []

        for symbol in targets:
            raw = price_repo.get_raw_prices(symbol)
            if raw.empty:
                no_prices += 1
                continue
            # 売買代金は生値で測る。調整済み終値に実出来高を掛けると、分割前の
            # バーを分割比率のぶん過小に見積もる。銘柄自身の暦で転がしてから
            # ベンチマークの暦に載せ替える。
            turnover = (raw[CLOSE] * raw[VOLUME]).rolling(TURNOVER_WINDOW).mean().shift(1)
            adjusted = split_adjusted(raw).reindex(calendar)
            close = adjusted[CLOSE].to_numpy(dtype=float)
            opens = adjusted[OPEN].to_numpy(dtype=float)
            floor = turnover.reindex(calendar).to_numpy(dtype=float)

            # **不連続の検出は「直前に値のあった日」と比べる。** 暦に載せ替えた
            # あとの NaN と比べると、売買停止を挟んだ併合が素通りする。
            filled = adjusted[CLOSE].ffill().to_numpy(dtype=float)
            with np.errstate(divide="ignore", invalid="ignore"):
                step = filled[1:] / filled[:-1]
            broken = np.zeros(len(calendar), dtype=bool)
            broken[1:] = np.isfinite(step) & (np.abs(step - 1.0) > MAX_SESSION_MOVE)
            # breaks[i] = 位置 i より前にある不連続の数。区間 [a, b] の個数は
            # breaks[b + 1] - breaks[a] で取れる。
            breaks = np.concatenate(([0], np.cumsum(broken)))

            allowed = positions
            if snapshot_at is not None:
                flags = np.array(
                    [symbol in snapshots[when] for when in ordered_snapshots], dtype=bool
                )
                indices = snapshot_at[allowed]
                inside = (indices >= 0) & flags[np.clip(indices, 0, None)]
                allowed = allowed[inside]
                if allowed.size == 0:
                    continue

            before = close[allowed - lookback]
            here = close[allowed]
            usable = np.isfinite(before) & np.isfinite(here) & (before > 0)
            no_lookback += int((~usable).sum())
            allowed = allowed[usable]
            if allowed.size == 0:
                continue

            level = floor[allowed]
            liquid = np.isfinite(level) & (level >= min_turnover)
            thin += int((~liquid).sum())
            allowed = allowed[liquid]
            if allowed.size == 0:
                continue

            entry = opens[allowed + skip]
            exit_price = opens[allowed + exit_offset]
            tradeable = (
                np.isfinite(entry) & np.isfinite(exit_price) & (entry > 0) & (exit_price > 0)
            )
            calendar_gap += int((~tradeable).sum())
            allowed = allowed[tradeable]
            if allowed.size == 0:
                continue

            # 5日リターンにも保有リターンにも入る移動を見る。片方だけ調べると、
            # 併合で「大きく下げた」ことにされた銘柄が分位1に入り続ける。
            spanned = (breaks[allowed + exit_offset + 1] - breaks[allowed - lookback + 1]) > 0
            discontinuous += int(spanned.sum())
            allowed = allowed[~spanned]
            if allowed.size == 0:
                continue

            symbol_lookback = close[allowed] / close[allowed - lookback] - 1.0
            symbol_forward = opens[allowed + exit_offset] / opens[allowed + skip] - 1.0
            chunks_pos.append(allowed)
            chunks_lb.append(symbol_lookback)
            chunks_fwd.append(symbol_forward)
            if extremes:
                pick = int(np.argmax(np.abs(symbol_forward)))
                worst.append(
                    (
                        float(abs(symbol_forward[pick])),
                        symbol,
                        int(allowed[pick]),
                        float(symbol_lookback[pick]),
                        float(symbol_forward[pick]),
                    )
                )

    if not chunks_pos:
        raise ValueError("条件を通る銘柄日が1つも無い。フィルタか期間を見直す。")

    pos = np.concatenate(chunks_pos)
    lookbacks = np.concatenate(chunks_lb)
    forwards = np.concatenate(chunks_fwd)

    days, counts, rows, bench_returns, thin_days = _aggregate(
        pos, lookbacks, forwards, calendar, bench_open, exit_offset, quantiles, skip
    )

    cuts = (1, 5, 25, 50, 75, 95, 99)
    percentiles = [
        (f"p{cut}", float(value))
        for cut, value in zip(cuts, np.percentile(forwards, cuts), strict=True)
    ]
    implausible = int((np.abs(forwards) > IMPLAUSIBLE_FORWARD).sum())
    worst.sort(reverse=True)
    biggest = [
        (symbol, calendar[position].date(), back, ahead)
        for _size, symbol, position, back, ahead in worst[:extremes]
    ]

    label = "snapshots" if snapshots and not survivors_only else "db"
    if snapshots and survivors_only:
        label = "survivors"
    series = ReversalSeries(
        days=days,
        counts=counts,
        quantiles=rows,
        benchmark=bench_returns,
        symbols_scanned=len(targets),
        symbols_without_prices=no_prices,
        excluded_no_lookback=no_lookback,
        excluded_thin=thin,
        excluded_calendar=calendar_gap,
        excluded_discontinuity=discontinuous,
        excluded_thin_day=thin_days,
        universe_label=label,
        forward_percentiles=percentiles,
        implausible=implausible,
        extremes=biggest,
    )
    logger.info("リバーサル日次系列: %s", series.summary())
    return series


def _aggregate(
    pos: np.ndarray,
    lookbacks: np.ndarray,
    forwards: np.ndarray,
    calendar: pd.DatetimeIndex,
    bench_open: np.ndarray,
    exit_offset: int,
    quantiles: int,
    skip: int,
) -> tuple[list[dt.date], list[int], list[tuple[float, ...]], list[float], int]:
    """銘柄日を営業日ごとの分位平均にまとめる。

    同順位は元の並び順で割る（``rank(method="first")`` と同じ）。日ごとに
    独立して割るので、ある日の分位1と別の日の分位1が同じ下落率とは限らない
    ——それが日次で分位を組むということである。
    """
    order = np.lexsort((lookbacks, pos))
    pos_sorted = pos[order]
    forward_sorted = forwards[order]

    starts = np.flatnonzero(np.r_[True, pos_sorted[1:] != pos_sorted[:-1]])
    group_sizes = np.diff(np.r_[starts, len(pos_sorted)])
    rank = np.arange(len(pos_sorted)) - np.repeat(starts, group_sizes)
    sizes = np.repeat(group_sizes, group_sizes)
    bucket = (rank * quantiles) // sizes

    frame = pd.DataFrame({"pos": pos_sorted, "bucket": bucket, "fwd": forward_sorted})
    means = frame.groupby(["pos", "bucket"])["fwd"].mean().unstack("bucket")

    days: list[dt.date] = []
    counts: list[int] = []
    rows: list[tuple[float, ...]] = []
    bench_returns: list[float] = []
    thin_days = 0
    unique_pos = pos_sorted[starts]
    for index, size in zip(unique_pos, group_sizes, strict=True):
        if size < quantiles:
            thin_days += 1
            continue
        values = means.loc[index]
        if values.isna().any():
            thin_days += 1
            continue
        entry = bench_open[index + skip]
        exit_price = bench_open[index + exit_offset]
        if not (entry > 0) or not (exit_price > 0):
            thin_days += 1
            continue
        days.append(calendar[index].date())
        counts.append(int(size))
        rows.append(tuple(float(value) for value in values.to_numpy()))
        bench_returns.append(float(exit_price / entry - 1.0))
    return days, counts, rows, bench_returns, thin_days


#: 封印済みの読み方の表（`PREREG_REVERSAL_JP.md` §8）。
#:
#: ``(合否, 読み方)`` を返す。**判定を人が読んで解釈しない。** 表は結果を
#: 見る前に確定させてあるので、当てはめるだけにする。SUE版では「惜しかった」
#: と基準が動きかけたので、当てはめをコードにした。
PASS = "合格"
FAIL = "不合格"


def verdict(
    mean: float,
    t_statistic: float,
    cost: float = COST_ROUND_TRIP,
    detectable: float = DETECTABLE,
    target_t: float = 2.0,
) -> tuple[str, str]:
    """封印済みの表に当てはめる。**解釈の余地を残さない。**

    Args:
        mean: OOS の分位1 − ベンチの平均（20営業日あたり）。
        t_statistic: Newey-West(20) の t。
        cost: 費用のしきい値。既定は1往復 0.40%。
        detectable: §7 で事前に計算した「t≥2.0 に必要な差」。
        target_t: 合格に要する t。

    Returns:
        ``(合否, 読み方)``。
    """
    if mean <= 0:
        return FAIL, "効果なし。説を閉じる。"
    if mean < cost:
        return FAIL, (
            f"効果はあるかもしれないが費用（{cost * 100:.2f}%）を賄えない。実行できないので閉じる。"
        )
    if t_statistic >= target_t:
        return PASS, f"点推定が費用を超え、t≥{target_t} を満たした。"
    if mean < detectable:
        return FAIL, (
            f"**構造的な帯（{cost * 100:.2f}%〜{detectable * 100:.2f}%）に入った。**"
            "儲かる水準だが、この検出力では有意にならない。"
            "「効果が無い」ではない。基準は緩めず、前向きに貯める。"
        )
    return FAIL, (
        f"点推定は必要な差（{detectable * 100:.2f}%）を超えたが、t が届かなかった。前向き検証へ。"
    )


def survivorship_gap(clean: Sequence[float], survivors: Sequence[float]) -> list[float]:
    """同じ日で揃えた「名簿あり − 生存者のみ」の差。

    **これがバイアスの大きさと符号である。** 片方にしか無い日を混ぜると、
    差ではなく期間の違いを測ることになるので、長さが違えば拒否する。

    Raises:
        ValueError: 長さが違う。
    """
    if len(clean) != len(survivors):
        raise ValueError(
            f"日数が違う（{len(clean)} 対 {len(survivors)}）。"
            "同じ日で揃っていない差は、バイアスではなく期間の違いを測っている。"
        )
    return [a - b for a, b in zip(clean, survivors, strict=True)]
