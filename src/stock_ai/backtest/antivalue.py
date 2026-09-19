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

from stock_ai.backtest.pead import MIN_TURNOVER, Period
from stock_ai.backtest.quantile_series import (
    QuantileSeries,
    build_panel,
    monthly_values,
    value_on,
)
from stock_ai.backtest.reversal import BENCHMARK
from stock_ai.backtest.reversal_census import QUANTILES
from stock_ai.core.logging import get_logger
from stock_ai.database.engine import Database

logger = get_logger(__name__)

#: 1往復の費用。**正本は `quantile_series` にある。** ここは名前を残すためだけ
#: （`tests/test_antivalue.py` が import している）。
ROUND_TRIP_COST = QuantileSeries.round_trip_cost

#: 分位を作るのに要る最低銘柄数。#7 と同じ。
MIN_SYMBOLS_PER_MONTH = 100

#: `pbr` が 92% 以上埋まるのは 2009年から。2008年は 63% しかない。
#:
#: **埋まっている銘柄だけが選ばれると、断面が歪む。** 事前登録 §6 でそう決めた。
USABLE_FROM = dt.date(2009, 1, 1)


@dataclasses.dataclass
class AntiValueSeries(QuantileSeries):
    """月ごとの分位リターンと、入れ替わり。**添字0が最も割安、末尾が最も割高。**

    **読み方は `QuantileSeries` に置いてある**——スプレッド・入れ替わり・β・
    α・費用・裾は #12（モメンタム）と同じ式である。**並べる材料しか違わない。**
    ここに残すのは、この説に固有の数え落としだけ。
    """

    skipped_thin: int = 0
    skipped_no_pbr: int = 0

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


def pbr_by_month(frame: pd.DataFrame) -> dict[tuple[str, pd.Period], tuple[dt.date, float]]:
    """（銘柄, 月）→（その月末の日付, PBR）。

    **正本は `quantile_series.monthly_values` にある。** #14（時価総額で並べる）
    が同じ処理を要るので、列名だけを渡す形に移した。**ここは名前を残すため
    だけ**である（`tests/test_antivalue.py` が import している）。

    **1つだけ振る舞いが変わった。** 空の `pbr` は、以前は `nan` として表に
    入っていた（`float(nan)`）。いまは**入らない。** `valuation_monthly` が
    書く前に落としているので実データでは出ないが、**並べ替えの鍵に `nan` が
    混じる形が消えた**——その月の分位が黙って狂う形である。
    """
    return monthly_values(frame, "pbr")


def pbr_on(
    pbr_of: dict[tuple[str, pd.Period], tuple[dt.date, float]],
    symbol: str,
    on: dt.date,
) -> float | None:
    """組み替え日 ``on`` の時点で使ってよい PBR。無ければ ``None``。

    **正本は `quantile_series.value_on` にある。**
    """
    return value_on(pbr_of, symbol, on)


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

    **組み立ては `quantile_series.build_panel` に置いてある。** ここが渡すのは
    **並べる材料（PBR）だけ**である。

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
    panel = build_panel(
        database,
        pbr_by_month(valuation),
        period=period,
        symbols=symbols,
        benchmark=benchmark,
        start=USABLE_FROM if start is None else max(start, USABLE_FROM),
        end=end,
        min_turnover=min_turnover,
        min_symbols=min_symbols,
        quantiles=quantiles,
        snapshots=snapshots,
    )
    return AntiValueSeries(
        months=panel.months,
        quantiles=panel.quantiles,
        members=panel.members,
        counts=panel.counts,
        benchmark=panel.benchmark,
        skipped_thin=panel.skipped_thin,
        skipped_no_pbr=panel.skipped_no_value,
    )
