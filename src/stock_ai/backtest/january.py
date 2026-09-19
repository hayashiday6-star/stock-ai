"""#14「1月効果」— 小型 − 大型のスプレッドが、1月だけ大きいか。

**格言は「1月は上がる」と言うが、測るのはサイズの傾きである。**
`docs/PREREG_JANUARY_JP.md` に封印前の取り決めがある。

## なぜ「1月は上がる」ではないのか

市場全体の話にすると #13 と同じ管（1本の系列の中で日どうし）になり、市場の
月次リターンの散らばり（月 4〜6%）をそのまま背負う。**9観測では何も見分け
られない。**

**差を取れば市場全体の動きは消える。** 節税売りの反動という説明も、
「12月に売られた小型株が1月に戻る」という**サイズの話**をしている。

## #9・#12 と何を共有し、何を分けたか

**いつ組み替えていつ降りるか**（`monthly_grid`）、**その日に上場していたか**
（`listed_on`）、**分位が出来たあとの読み方**（`QuantileSeries`）、そして
**組み立てそのもの**（`quantile_series.build_panel`）は同じものを使う。

**並べる材料だけが違う。** #9 は PBR、#12 は過去12ヶ月のリターン、ここは
**月末の時価総額**である。

## 並べるのは「小ささ」である

`QuantileSeries.spread()` は **上端 − 下端**と決まっている。**上端を最小の
時価総額にしないと、スプレッドが「大型 − 小型」になって符号が逆になる。**

だから並べる材料を **`smallness = -market_cap`** にする。時価総額そのもので
はない。

**この値を表示しない。** 順位を作るためだけの数で、負の百万円である。

## `market_cap` は百万円単位である

**円ではない**（`CLAUDE.md`「出した数字を、自分で見る」）。ここでは順位しか
使わないので尺度は結果を変えないが、**円として表示すれば百万倍間違える。**

## 観測は年に1回しかない

```
1観測 =（その年の1月のスプレッド）
     −（同じ年の、1月以外の月のスプレッドの平均）
```

IS（2009-01〜2017-11）で **9回**、OOS（2018-01〜2026-08）で **9回**。

**「その年の1月」とは、12月の最終営業日を組み替え日とする月次リターン**で
ある。12月末の翌営業日の始値で入り、1月末の翌営業日の始値で降りる。
**1つずれれば12月を1月と呼ぶことになり、例外は出ない**ので、
`tests/test_january.py` が札の付き方を固定している。

## 引く相手に、1月を入れない

入れれば本物が引く側に混ざる。**#13 で踏んだ形**である。
"""

from __future__ import annotations

import dataclasses
import datetime as dt

import numpy as np
import pandas as pd

from stock_ai.backtest import tails
from stock_ai.backtest.lowvol import MIN_SYMBOLS_PER_MONTH
from stock_ai.backtest.pead import MIN_TURNOVER, Period
from stock_ai.backtest.quantile_series import (
    QuantileSeries,
    build_panel,
    monthly_values,
)
from stock_ai.backtest.reversal import BENCHMARK
from stock_ai.backtest.reversal_census import QUANTILES
from stock_ai.core.logging import get_logger
from stock_ai.database.engine import Database

logger = get_logger(__name__)

#: いちばん早い組み替え日。**2008-12 である。**
#:
#: #9・#12・#13 は 2009-01 からだが、ここは **2009年1月のリターンを作るのに
#: 2008-12 の断面が要る。** 期間は「実現した月」で切る（事前登録 §6）。
#:
#: **2008年は `pbr` が 63% しか埋まっていない**ので、この表も 2008-12 は薄い。
#: **2009年1月の断面の銘柄数を、他の年と並べて出す。**
USABLE_FROM = dt.date(2008, 12, 1)

#: 1月。**札の付き方を1箇所に置く。**
JANUARY = 1

#: 1月以外の月の数。**12 − 1。**
OTHER_MONTHS = 11


@dataclasses.dataclass
class SizeSeries(QuantileSeries):
    """月ごとの分位リターン。**添字0が最大の時価総額、末尾が最小。**

    **読み方は `QuantileSeries` に置いてある。** ここに残すのは、この説に固有の
    数え落としだけ。

    **`spread()` は「小型 − 大型」になる**（上端 − 下端で、上端が最小）。
    """

    skipped_thin: int = 0
    skipped_no_cap: int = 0
    """月末の `market_cap` が無くて外した銘柄月。**小型ほど落ちやすい。**"""

    illiquid_in_small_range: int = 0
    """流動性で外した銘柄月のうち、**残った最小分位と同じ大きさだったもの。**

    **削った側は、残ったものからは見えない。** 日商1億円の線は小型株をまるごと
    削るので、何を削ったのかを数で出す（事前登録 §2）。
    """

    illiquid: int = 0
    """流動性で外した銘柄月（全体）。"""

    def summary(self) -> str:
        """1行のまとめ。**平均は出さない**——§0 が判定を先食いしないため。"""
        if not self.months:
            return "分位を作れた月が1つも無い。**比べていない。**"
        return (
            f"{len(self.months)} ヶ月（{self.months[0]} 〜 {self.months[-1]}）、"
            f"1ヶ月あたり {int(np.mean(self.counts)):,} 銘柄。"
            f"入れ替わり {self.turnover():.1%}／月 → 費用 {self.cost_per_month():.3%}／月"
            f"（年 {self.cost_per_month() * 12:.2%}）。"
            f"薄くて飛ばした月 {self.skipped_thin}、"
            f"時価総額が無くて外した銘柄月 {self.skipped_no_cap:,}、"
            f"流動性で外した銘柄月 {self.illiquid:,}。"
        )

    def warnings(self) -> list[str]:
        """気付かなくても目に入るべきこと。**表は読む側が気付く必要がある。**

        **早期 return しない。** 理由を隠す形を2度踏んでいる（`CLAUDE.md`）。
        """
        found: list[str] = []
        if not self.months:
            return ["**分位を作れた月が1つも無い。**"]
        if self.skipped_thin:
            found.append(f"**{self.skipped_thin} ヶ月は銘柄が足りず、飛ばしている。**")
        if self.illiquid_in_small_range:
            found.append(
                f"**流動性で外した {self.illiquid:,} 銘柄月のうち、"
                f"{self.illiquid_in_small_range:,} は残った最小分位と同じ大きさだった。** "
                "**日商1億円に届かない小型株については、何も主張しない**（事前登録 §9）。"
            )
        if self.turnover() > 0.5:
            found.append(
                f"**入れ替わりが {self.turnover():.1%}／月ある。** "
                f"費用は年 {self.cost_per_month() * 12:.2%} になる。"
            )
        return found


def smallness_by_month(frame: pd.DataFrame) -> dict[tuple[str, pd.Period], tuple[dt.date, float]]:
    """（銘柄, 月）→（その月末の日付, **小ささ**）。

    **小ささ ＝ −時価総額。** `build_panel` は小さい順に並べるので、負号を付け
    ないと上端が大型になり、**スプレッドの符号が逆になる。**

    Args:
        frame: :func:`~stock_ai.data.valuation_monthly.read` が返す表。

    Returns:
        （銘柄, 月）で引ける表。

    Raises:
        KeyError: `market_cap` の列が無い。
    """
    caps = monthly_values(frame, "market_cap")
    return {key: (when, -value) for key, (when, value) in caps.items()}


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
) -> SizeSeries:
    """月末の時価総額で並べ、翌月のリターンを分位ごとに集める。

    **組み立ては `quantile_series.build_panel` に置いてある。** ここが渡すのは
    **並べる材料（小ささ）だけ**である。

    Args:
        database: 価格の保存先。
        valuation: :func:`~stock_ai.data.valuation_monthly.read` が返す表。
        period: IS / OOS / ALL。
        symbols: 対象銘柄。省略時は ``market="JP"`` の全銘柄。
        benchmark: ベンチマーク。**暦もこれに合わせる。**
        start: この日より前の組み替え日を使わない。既定は 2008-12-01。
        end: **この日より後のデータを1つも使わない。**
        min_turnover: 流動性の下限（円）。
        min_symbols: 分位を作るのに必要な最低銘柄数。
        quantiles: 分位数。
        snapshots: 日付ごとの名簿。**渡さないと生存バイアスが入る。**

    Returns:
        :class:`SizeSeries`。

    Raises:
        ValueError: ベンチマークの価格が無いか、組み替え日が足りない。
    """
    panel = build_panel(
        database,
        smallness_by_month(valuation),
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
    # **削った側を、残った側の物差しで数える。** 最小分位に入るのに要った
    # 「小ささ」より小さくない（＝同じかもっと小型の）銘柄を数える。
    in_range = sum(
        sum(1 for value in rejected if value >= cutoff)
        for cutoff, rejected in zip(panel.top_cutoff, panel.rejected_illiquid, strict=True)
    )
    return SizeSeries(
        months=panel.months,
        quantiles=panel.quantiles,
        members=panel.members,
        counts=panel.counts,
        benchmark=panel.benchmark,
        skipped_thin=panel.skipped_thin,
        skipped_no_cap=panel.skipped_no_value,
        illiquid_in_small_range=in_range,
        illiquid=sum(len(rejected) for rejected in panel.rejected_illiquid),
    )


def realised_month(formation: dt.date) -> tuple[int, int]:
    """その組み替えのリターンが実現する月。**組み替え日の翌月である。**

    12月末に仕込んだものは**翌年の1月**に実現する。**ここを1つ間違えると、
    12月を1月と呼ぶ。例外は出ない。**

    Args:
        formation: 組み替え日（月の最終営業日）。

    Returns:
        ``(年, 月)``。
    """
    if formation.month == 12:  # noqa: PLR2004 - 12月は翌年の1月になる
        return formation.year + 1, JANUARY
    return formation.year, formation.month + 1


@dataclasses.dataclass
class JanuarySeries:
    """年ごとの「1月 − その年の他の月」。**単位は「1月あたり」。**"""

    years: list[int]
    episodes: list[float]
    januaries: list[float]
    """その年の1月のスプレッド。**分けて持つ**——`t` だけ見ないため。"""

    others: list[float]
    """その年の、1月以外の月のスプレッドの平均。"""

    other_counts: list[int]
    january_symbols: list[int]
    """1月の断面の銘柄数。**薄い年が在れば、それは偏りである。**"""

    skipped_no_january: list[int]
    """1月が無くて捨てた年。"""

    skipped_no_others: list[int]
    """1月以外の月が1つも無くて捨てた年。"""

    def worst_year(self) -> float:
        """いちばん悪かった年。**式は `tails` に1つだけ置いてある。**"""
        return tails.worst(self.episodes)

    def left_tail(self, share: float = tails.DEFAULT_TAIL_SHARE) -> float:
        """下位 ``share`` の年の平均。

        **9観測の 5% は 0.45年で、1点に丸まる。** 最悪の年と一緒に読むこと。

        Args:
            share: 下から取る割合。

        Returns:
            下位の平均。

        Raises:
            ValueError: ``share`` が 0〜1 の外。
        """
        return tails.left_tail(self.episodes, share)

    def hit_rate(self) -> float:
        """差が正だった年の割合。**平均だけで語らない。**"""
        return tails.hit_rate(self.episodes)

    def autocorrelation(self) -> float:
        """1次の自己相関。**n=9 では Newey-West が不安定なので、これを出す。**

        Returns:
            ラグ1の相関。観測が3つ未満、または動かないなら ``0.0``。
        """
        if len(self.episodes) < 3:  # noqa: PLR2004 - 相関を作るのに最低3点要る
            return 0.0
        values = np.asarray(self.episodes, dtype=float)
        centred = values - values.mean()
        bottom = float(np.dot(centred, centred))
        if bottom <= 0:
            return 0.0
        return float(np.dot(centred[:-1], centred[1:]) / bottom)

    def summary(self) -> str:
        """1行のまとめ。**平均は出さない**——§0 が判定を先食いしないため。"""
        if not self.years:
            return "1月の観測を1回も作れなかった。**比べていない。**"
        return (
            f"{len(self.years)} 年（{self.years[0]} 〜 {self.years[-1]}）。"
            f"引く相手は1年あたり {int(np.mean(self.other_counts))} ヶ月、"
            f"1月の断面は {int(np.mean(self.january_symbols)):,} 銘柄。"
            f"1月が無くて捨てた年 {len(self.skipped_no_january)}、"
            f"他の月が無くて捨てた年 {len(self.skipped_no_others)}。"
        )

    def warnings(self) -> list[str]:
        """気付かなくても目に入るべきこと。**早期 return しない。**"""
        found: list[str] = []
        if not self.years:
            return ["**1月の観測を1回も作れなかった。**"]
        short = [
            (year, count)
            for year, count in zip(self.years, self.other_counts, strict=True)
            if count < OTHER_MONTHS
        ]
        if short:
            listed = "、".join(f"{year}年 {count}ヶ月" for year, count in short)
            found.append(
                f"**引く相手が {OTHER_MONTHS} ヶ月に満たない年がある**（{listed}）。"
                "期間の端では、そうなる。"
            )
        if self.skipped_no_january:
            found.append(
                f"**1月が無くて {len(self.skipped_no_january)} 年ぶん捨てた**"
                f"（{'、'.join(str(year) for year in self.skipped_no_january)}）。"
            )
        if self.skipped_no_others:
            found.append(
                f"**他の月が無くて {len(self.skipped_no_others)} 年ぶん捨てた**"
                f"（{'、'.join(str(year) for year in self.skipped_no_others)}）。"
            )
        median = float(np.median(self.january_symbols)) if self.january_symbols else 0.0
        thin = [
            (year, count)
            for year, count in zip(self.years, self.january_symbols, strict=True)
            if median > 0 and count < 0.7 * median
        ]
        if thin:
            listed = "、".join(f"{year}年 {count:,}銘柄" for year, count in thin)
            found.append(
                f"**1月の断面が薄い年がある**（{listed}。中央値 {median:,.0f}銘柄）。"
                "**薄ければ、それは偏りである。**"
            )
        return found


def annual_episodes(
    months: list[dt.date],
    spread: list[float],
    counts: list[int] | None = None,
    first_year: int | None = None,
    last_year: int | None = None,
) -> JanuarySeries:
    """年ごとに「1月 − その年の他の月の平均」を作る。

    **年を切るのは、実現した月のほうである。** 組み替え日ではない
    （`realised_month`）。

    Args:
        months: 組み替え日（`SizeSeries.months`）。
        spread: そのときのスプレッド（`SizeSeries.spread()`）。
        counts: そのときの銘柄数（`SizeSeries.counts`）。省略可。
        first_year: この年より前を使わない。
        last_year: この年より後を使わない。

    Returns:
        :class:`JanuarySeries`。

    Raises:
        ValueError: ``months`` と ``spread`` の長さが違う。
    """
    if len(months) != len(spread):
        raise ValueError(f"months {len(months)} と spread {len(spread)} の長さが違う。")
    if counts is not None and len(counts) != len(months):
        raise ValueError(f"counts {len(counts)} と months {len(months)} の長さが違う。")

    january: dict[int, float] = {}
    january_size: dict[int, int] = {}
    others: dict[int, list[float]] = {}
    for index, (when, value) in enumerate(zip(months, spread, strict=True)):
        year, month = realised_month(when)
        if first_year is not None and year < first_year:
            continue
        if last_year is not None and year > last_year:
            continue
        if month == JANUARY:
            january[year] = value
            january_size[year] = counts[index] if counts is not None else 0
        else:
            others.setdefault(year, []).append(value)

    years: list[int] = []
    episodes: list[float] = []
    inside: list[float] = []
    outside: list[float] = []
    other_counts: list[int] = []
    sizes: list[int] = []
    no_january: list[int] = []
    no_others: list[int] = []

    for year in sorted(set(january) | set(others)):
        if year not in january:
            no_january.append(year)
            continue
        rest = others.get(year, [])
        if not rest:
            no_others.append(year)
            continue
        years.append(year)
        inside.append(january[year])
        outside.append(float(np.mean(rest)))
        episodes.append(january[year] - float(np.mean(rest)))
        other_counts.append(len(rest))
        sizes.append(january_size.get(year, 0))

    logger.info(
        "1月の観測: %d 年、引く相手は1年あたり平均 %.1f ヶ月",
        len(years),
        float(np.mean(other_counts)) if other_counts else 0.0,
    )
    return JanuarySeries(
        years=years,
        episodes=episodes,
        januaries=inside,
        others=outside,
        other_counts=other_counts,
        january_symbols=sizes,
        skipped_no_january=no_january,
        skipped_no_others=no_others,
    )
