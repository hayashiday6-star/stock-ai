"""複数の因子を、同じフィルタを通した1枚の盤面に載せる。

**これは判定ではない。** 合成の利得（r）を測る校正で、閾値は
`docs/HYPOTHESES.md` に**測る前から**書いてある。

### なぜ盤面を作り直すのか

`lowvol.build_series` は断面を ``(ボラティリティ, 翌月リターン)`` で返す。
合成には因子が複数要るので、`(signal の組, 翌月リターン)` を持つ盤面が要る。

**`lowvol.py` を書き換えないのは、#7 の判定を再現できなくなるからである。**
判定は閉じている。閉じた計算の入力を後から触らない。

### 代わりに、再現するかどうかで揃っていることを確かめる

フィルタ（流動性・不連続・履歴・最低銘柄数）は `lowvol` と同じ定数を使う。
**低ボラだけの盤面が #7 の判定（α +0.242%、t +1.70）を再現すれば、揃っている。**
再現しなければ、どこかで違うフィルタを通している。

### 因子ごとに要る履歴が違う

モメンタムは252営業日、低ボラは250営業日を見る。**要る履歴を全因子の最大に
そろえると、低ボラだけの盤面が #7 と違う universe になる。** そこで要求する
履歴は「頼まれた因子」から決める。低ボラだけなら250のままで、再現できる。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from stock_ai.backtest.lowvol import (
    DEFAULT_WINDOW,
    MIN_SYMBOLS_PER_MONTH,
)
from stock_ai.backtest.lowvol_census import formation_dates
from stock_ai.backtest.pead import MIN_TURNOVER, TURNOVER_WINDOW, Period
from stock_ai.backtest.reversal import BENCHMARK, MAX_SESSION_MOVE
from stock_ai.core.logging import get_logger
from stock_ai.data.schema import CLOSE, OPEN, VOLUME, split_adjusted
from stock_ai.database.engine import Database
from stock_ai.database.repository import PriceRepository, list_securities

logger = get_logger(__name__)

#: モメンタムが見る営業日数と、直近で飛ばす日数。
#:
#: 12ヶ月から直近1ヶ月を除く形。**直近を含めると短期リバーサルと逆向きに
#: 重なる**ので、2つを合成する意味が薄れる。
MOMENTUM_WINDOW = 252
MOMENTUM_SKIP = 21

#: 短期リバーサルが見る営業日数。
REVERSAL_WINDOW = 21

#: 因子の名前と、それが要る履歴（営業日）。
#:
#: **要る履歴は「頼まれた因子」から決める。** 全因子の最大でそろえると、
#: 低ボラだけの盤面が #7 と違う universe になり、再現による検算ができない。
FACTOR_HISTORY: dict[str, int] = {
    "低ボラ": DEFAULT_WINDOW,
    "モメンタム": MOMENTUM_WINDOW,
    "短期リバーサル": REVERSAL_WINDOW,
}

DEFAULT_FACTORS: tuple[str, ...] = ("低ボラ", "モメンタム", "短期リバーサル")


@dataclass(frozen=True)
class Panel:
    """月ごとの断面。**因子は signal の組で持つ。**

    signal は**大きいほど買う側**にそろえてある。低ボラなら符号を反転して
    あるので、どの因子も「大きい＝良い」で読める。**そろえていないと、
    合成のときに符号を1つ間違えても例外が出ない。**
    """

    factors: tuple[str, ...]
    months: list[dt.date]
    benchmark: list[float]
    sections: list[list[tuple[tuple[float, ...], float]]]
    """月ごとの ``((signal の組), 翌月リターン)``。"""
    symbols_scanned: int = 0
    excluded_thin_month: int = 0
    excluded_discontinuity: int = 0
    excluded_no_history: int = 0
    excluded_thin: int = 0

    def column(self, factor: str) -> list[list[tuple[float, float]]]:
        """1因子だけを取り出して、``(signal, 翌月リターン)`` の断面にする。

        **単一因子の t を、合成と同じ universe で測るために要る。** 別々に
        組むと、比が「合成の利得」ではなく「universe の差」を含む。

        Raises:
            ValueError: その因子を持っていない。
        """
        if factor not in self.factors:
            raise ValueError(f"{factor!r} はこの盤面に無い（{self.factors}）。")
        index = self.factors.index(factor)
        return [
            [(signals[index], forward) for signals, forward in month] for month in self.sections
        ]

    def composite(self, weights: Sequence[float] | None = None) -> list[list[tuple[float, float]]]:
        """因子を標準化して足し合わせた合成 signal の断面。

        **各因子を月ごとに標準化してから足す。** 生の値のまま足すと、単位の
        大きい因子（ボラティリティは 0.01 の桁、モメンタムは 0.1 の桁）が
        重みを独占する。**例外は出ないので、標準化を飛ばしても動いてしまう。**

        Args:
            weights: 因子ごとの重み。省略すると等加重。

        Returns:
            ``(合成 signal, 翌月リターン)`` の断面。標準化できない因子がある
            月は落とす。
        """
        from stock_ai.backtest.cross_section import zscores

        count = len(self.factors)
        share = list(weights) if weights is not None else [1.0] * count
        if len(share) != count:
            raise ValueError(f"重みの数が因子の数と違う（{len(share)} 対 {count}）。")

        built: list[list[tuple[float, float]]] = []
        for month in self.sections:
            if not month:
                built.append([])
                continue
            standardized: list[list[float] | None] = [
                zscores([signals[index] for signals, _forward in month]) for index in range(count)
            ]
            if any(column is None for column in standardized):
                # **0 で埋めない。** ばらつかない因子を 0 にすると、その月だけ
                # 因子が1本減った合成になる。落として数えるほうが読める。
                built.append([])
                continue
            rows: list[tuple[float, float]] = []
            for position, (_signals, forward) in enumerate(month):
                blended = sum(
                    weight * column[position]  # type: ignore[index]
                    for weight, column in zip(share, standardized, strict=True)
                )
                rows.append((blended, forward))
            built.append(rows)
        return built


def build_panel(
    database: Database,
    period: Period = Period.ALL,
    factors: Sequence[str] = DEFAULT_FACTORS,
    symbols: list[str] | None = None,
    benchmark: str = BENCHMARK,
    start: dt.date | None = None,
    end: dt.date | None = None,
    window: int = DEFAULT_WINDOW,
    min_turnover: float = MIN_TURNOVER,
    min_symbols: int = MIN_SYMBOLS_PER_MONTH,
) -> Panel:
    """複数因子の月次断面を、`lowvol` と同じフィルタで作る。

    Args:
        database: 価格の保存先。
        period: IS / OOS / ALL。
        factors: 載せる因子。**要る履歴はここから決まる。**
        symbols: 対象銘柄。省略時は ``market="JP"`` の全銘柄。
        benchmark: ベンチマーク。暦もこれに合わせる。
        start: この日より前の組み替え日を使わない。
        end: **この日より後のデータを1つも使わない。**
        window: 低ボラの測定窓。
        min_turnover: 流動性の下限。
        min_symbols: 分位を作るのに必要な最低銘柄数。

    Returns:
        月ごとの断面。

    Raises:
        ValueError: 知らない因子、ベンチマークの価格が無い、組み替え日が無い。
    """
    chosen = tuple(factors)
    unknown = [name for name in chosen if name not in FACTOR_HISTORY]
    if unknown:
        raise ValueError(f"知らない因子: {unknown}。使えるのは {sorted(FACTOR_HISTORY)}。")
    if not chosen:
        raise ValueError("因子が1つも指定されていない。")

    history = max(window if name == "低ボラ" else FACTOR_HISTORY[name] for name in chosen)

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

        usable: list[tuple[int, int]] = []
        for index, position in enumerate(formations[:-1]):
            day = calendar[position].date()
            if start is not None and day < start:
                continue
            # **退場日まで見て切る。** 組み替え日だけで切ると保有期間が越えて
            # 伸びる（`PREREG_LOWVOL_JP.md` §7-2 で見つけた漏れと同じ形）。
            if end is not None:
                exit_at = formations[index + 1] + 1
                if exit_at >= len(calendar) or calendar[exit_at].date() > end:
                    continue
            usable.append((index, position))
        if not usable:
            raise ValueError("その期間に組み替え日が無い。")

        targets = symbols or [row.symbol for row in list_securities(session, market="JP")]
        buckets: dict[int, list[tuple[tuple[float, ...], float]]] = {i: [] for i, _ in usable}
        no_history = thin = discontinuous = 0

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
                if position < history:
                    no_history += 1
                    continue
                entry = position + 1
                exit_at = formations[index + 1] + 1
                if exit_at >= len(calendar):
                    no_history += 1
                    continue
                level = floor[position]
                if not np.isfinite(level) or level < min_turnover:
                    thin += 1
                    continue
                if breaks[exit_at + 1] - breaks[position - history + 1] > 0:
                    discontinuous += 1
                    continue
                if not (opens[entry] > 0) or not (opens[exit_at] > 0):
                    no_history += 1
                    continue
                sample = returns[position - history + 1 : position + 1]
                if not np.isfinite(sample).all():
                    no_history += 1
                    continue

                signals = _signals(chosen, sample, window, close, position)
                if signals is None:
                    no_history += 1
                    continue
                buckets[index].append((signals, float(opens[exit_at] / opens[entry] - 1.0)))

    months: list[dt.date] = []
    bench_returns: list[float] = []
    sections: list[list[tuple[tuple[float, ...], float]]] = []
    thin_month = 0
    for index, position in usable:
        members = buckets[index]
        if len(members) < min_symbols:
            thin_month += 1
            continue
        exit_at = formations[index + 1] + 1
        entry = bench_open[position + 1]
        leave = bench_open[exit_at]
        if not (entry > 0) or not (leave > 0):
            thin_month += 1
            continue
        months.append(calendar[position].date())
        bench_returns.append(float(leave / entry - 1.0))
        sections.append(members)

    panel = Panel(
        factors=chosen,
        months=months,
        benchmark=bench_returns,
        sections=sections,
        symbols_scanned=len(targets),
        excluded_thin_month=thin_month,
        excluded_discontinuity=discontinuous,
        excluded_no_history=no_history,
        excluded_thin=thin,
    )
    logger.info(
        "因子盤面: %s、%d ヶ月、1ヶ月あたり中央値 %d 銘柄",
        "+".join(chosen),
        len(months),
        int(np.median([len(month) for month in sections])) if sections else 0,
    )
    return panel


def _signals(
    factors: tuple[str, ...],
    sample: np.ndarray,
    window: int,
    close: np.ndarray,
    position: int,
) -> tuple[float, ...] | None:
    """1銘柄・1ヶ月ぶんの signal の組。**大きいほど買う側にそろえる。**

    そろえておかないと、合成のときに符号を1つ間違えても例外が出ない。
    """
    values: list[float] = []
    for name in factors:
        if name == "低ボラ":
            # **符号を反転する。** 低ボラが買う側なので、大きいほど良い。
            values.append(-float(np.std(sample[-window:], ddof=1)))
        elif name == "モメンタム":
            start = position - MOMENTUM_WINDOW + 1
            stop = position - MOMENTUM_SKIP
            if start < 0 or stop <= start:
                return None
            past, recent = close[start], close[stop]
            if not (past > 0) or not (recent > 0):
                return None
            values.append(float(recent / past - 1.0))
        elif name == "短期リバーサル":
            start = position - REVERSAL_WINDOW
            if start < 0:
                return None
            past, latest = close[start], close[position]
            if not (past > 0) or not (latest > 0):
                return None
            # **符号を反転する。** 直近が下げた銘柄を買う側にする。
            values.append(-float(latest / past - 1.0))
        else:  # pragma: no cover - build_panel が先に弾く
            raise ValueError(f"知らない因子: {name}")
    return tuple(values)
