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

from stock_ai.backtest.lowvol import ROUND_TRIP_COST
from stock_ai.core.logging import get_logger

logger = get_logger(__name__)


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
        """いちばん悪かった月のスプレッド。**平均が同じでも、ここが違えば別物。**"""
        return min(self.spread()) if self.months else float("nan")

    def left_tail(self, share: float = 0.05) -> float:
        """下位 ``share`` の月の平均。**裾を、1点ではなく帯で見る。**

        いちばん悪い月だけを見ると、**1回の事故か、そういう性質かが分からない。**

        Args:
            share: 下から取る割合。

        Returns:
            下位の平均。月が無ければ ``nan``。

        Raises:
            ValueError: ``share`` が 0〜1 の外。
        """
        if not 0.0 < share <= 1.0:
            raise ValueError(f"share must be in (0, 1]; got {share}.")
        if not self.months:
            return float("nan")
        ordered = sorted(self.spread())
        take = max(1, round(len(ordered) * share))
        return float(np.mean(ordered[:take]))

    def hit_rate(self) -> float:
        """スプレッドが正だった月の割合。**平均だけで語らない。**"""
        if not self.months:
            return float("nan")
        values = self.spread()
        return sum(1 for value in values if value > 0) / len(values)
