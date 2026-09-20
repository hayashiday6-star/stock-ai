"""外した権利落ちの中身を見る。**警告が「見ること」と言った相手である。**

`#16`（落ちるナイフ）が急落 819 件を権利落ちで外し、警告が鳴った。
**そこで止まっていた**——「中身を見ること」と書いてあるだけで、見る道具が
無かった（2026-09-20、ユーザーが指摘）。

## 説明は3つあり、見分けはつく

| 説明 | 見分け方 |
|---|---|
| `ExDate` の読み違い | **権利落ち日に値が下がっていない。** ずれていれば前後の日に下がる |
| 特別配当 | 利回りが大きい（`SpecDivRate` が入っている） |
| 窓が広いだけ | 利回りは1〜2%。**配当を戻しても −20% を超える** |

**3つ目なら、外すべきでない急落を外している。**

## 並べるのは中央値で、隣と比べる

権利落ち日は3月末・9月末に固まるので、**相場そのものが動いていた日**が
混ざる。ここでは**前後2営業日と比べる**ので、その差は消える——隣り合う日
だから、相場の動きはどのオフセットにもほぼ同じだけ乗る。

**引く相手を作らない理由でもある。** 引くと「どの宇宙を引いたか」が新しい
前提になる。ここで要るのは**どの日に段差が在るか**だけである。

## 保有窓の中の権利落ちは、外していない

除外しているのは**急落の6営業日**だけで、**保有する5営業日は見ていない。**
ショートでは配当は払う側なので、**配当を落としていない価格で測ると取り高が
高く出る。** その大きさをここで出す——`#16` の +0.69% と同じ単位で。
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Callable, Sequence
from statistics import fmean, median

import numpy as np

from stock_ai.backtest.knife import HOLDING, KNIFE_DAYS, KNIFE_DROP
from stock_ai.core.logging import get_logger
from stock_ai.data.jquants_dividend import ExDividend
from stock_ai.data.schema import CLOSE, split_adjusted
from stock_ai.database.engine import Database

logger = get_logger(__name__)

#: 権利落ち日の前後、何営業日ずつ並べるか。
SPAN = 2

#: これを超える利回りは読み違いとして落とす。**1回の配当で株価の半分は落ちない。**
MAX_YIELD = 0.5

#: 下げと利回りが何倍まで違ってよいか。**両向きに当てる。**
#: 3倍にしていたら、2.4倍のずれが通った（2026-09-20）。
EXPLAINED_BAND = 1.5

Rates = dict[str, dict[dt.date, ExDividend]]


@dataclasses.dataclass(frozen=True)
class Alignment:
    """権利落ち日の前後で、値がどこで下がっているか。"""

    offsets: tuple[int, ...]
    medians: tuple[float, ...]
    """各オフセットの中央値リターン。**平均ではない**——裾が重い。"""

    counts: tuple[int, ...]
    median_yield: float
    events: int
    symbols: int

    def __post_init__(self) -> None:
        """並びの長さが揃っていること。

        Raises:
            ValueError: オフセット・中央値・件数の長さが違う。
        """
        if not (len(self.offsets) == len(self.medians) == len(self.counts)):
            raise ValueError(
                f"長さが揃っていない: {len(self.offsets)} / "
                f"{len(self.medians)} / {len(self.counts)}。"
            )

    @property
    def lowest(self) -> int | None:
        """中央値がいちばん低いオフセット。**ここに段差が在る。**"""
        if not self.medians:
            return None
        return self.offsets[min(range(len(self.medians)), key=lambda i: self.medians[i])]

    @property
    def aligned(self) -> bool:
        """段差が権利落ち日そのものに在るか。"""
        return self.lowest == 0

    def summary(self) -> str:
        """1行のまとめ。"""
        if not self.events:
            return "権利落ちを1件も値付けできなかった。**比べていない。**"
        return (
            f"{self.symbols:,} 銘柄・{self.events:,} 件の権利落ちを並べた。"
            f"配当利回りの中央値 {self.median_yield:.2%}。"
            f"いちばん下がるのは **{self.lowest:+d} 日目**"
            f"（下げは利回りの {self.explained:.2f}倍）。"
        )

    def warnings(self) -> list[str]:
        """気付かなくても目に入るべきこと。**早期 return しない。**"""
        found: list[str] = []
        if not self.events:
            return ["**権利落ちを1件も値付けできなかった。**"]
        if not self.aligned:
            found.append(
                f"**`ExDate` がずれている。** 値が下がるのは {self.lowest:+d} 日目で、"
                "権利落ち日そのものではない。**この列を使って外した件は、"
                "外す日を間違えている**——`#15` も同じ列で外している。"
            )
        # **両向きに見る。** 片側だけだと、読み違いの向きが逆のときに黙る。
        # **幅は 1.5倍。** 3倍にしていたら 2.4倍のずれが通った（2026-09-20）。
        share = self.explained
        if share is not None and not (1 / EXPLAINED_BAND <= share <= EXPLAINED_BAND):
            drop = dict(zip(self.offsets, self.medians, strict=True))[0]
            found.append(
                f"**権利落ち日の下げ（{drop:+.2%}）と配当利回り"
                f"（{self.median_yield:.2%}）が合わない（{share:.2f}倍）。** "
                "**どちらかの読み方が違う。** 落ちるのは配当ぶんのはずである"
                "（税で少し小さくなることはあるが、倍の違いにはならない）。"
            )
        return found

    @property
    def explained(self) -> float | None:
        """権利落ち日の下げが、配当利回りの何倍か。**1 に近いはず。**"""
        if not self.events or self.median_yield <= 0:
            return None
        drop = dict(zip(self.offsets, self.medians, strict=True))[0]
        return -drop / self.median_yield


@dataclasses.dataclass(frozen=True)
class Exclusions:
    """権利落ちで外した急落の内訳。**足して合う形で持つ。**"""

    excluded: int
    still_qualifies: int
    """配当を戻しても −20% を超える。**外すべきでなかった。**"""

    rescued: int
    """戻すと届かない。**外して正しい。**"""

    outside_window: int
    """権利落ちが**下げに効かない位置**に在った。**外すべきでなかった。**

    除外の窓は急落の ``days + 1`` 営業日だが、**下げに効くのは ``days``
    日ぶんだけ**である——基準日（``index - days``）に落ちた配当は、
    ``closes[index] ÷ closes[index - days]`` のどちらにも同じだけ乗って
    いるので、比を1つも動かさない。**除外の窓が1日広い。**
    """

    zero_rate: int
    """外した理由の配当が**額 0**だった。**外すべきでなかった。**

    無配の公表にも `ExDate` は入る。`ex_dates_known_by` は額を見ないので、
    **落ちるものが無い日で急落を外していた。**
    """

    revised: int
    """公表時の権利落ち日が、最終データに無い。**訂正された。** 判定しない。"""

    undecided: int
    """額が読めないので判定できない。**分母に入れない。**"""

    special: int
    """特別配当が乗っていた件数。"""

    median_yield: float
    by_month: tuple[tuple[int, int], ...]

    def __post_init__(self) -> None:
        """内訳が外した件数に足し合うこと。

        **数と中身を別々に数えると、片方だけ直したときに黙ってずれる**
        （`ExDateCoverage` と同じ作り）。

        Raises:
            ValueError: 内訳の合計が外した件数に合わない。
        """
        parts = (
            self.still_qualifies
            + self.rescued
            + self.outside_window
            + self.zero_rate
            + self.revised
            + self.undecided
        )
        if parts != self.excluded:
            raise ValueError(
                f"内訳 {parts} 件（{self.still_qualifies} + {self.rescued} + "
                f"{self.outside_window} + {self.zero_rate} + {self.revised} + "
                f"{self.undecided}）が、外した {self.excluded} 件に合わない。"
            )

    @property
    def decided(self) -> int:
        """判定できた件数。"""
        return self.kept_by_mistake + self.rescued

    @property
    def kept_by_mistake(self) -> int:
        """外すべきでなかった件数。"""
        return self.still_qualifies + self.outside_window + self.zero_rate

    @property
    def wrongly_excluded(self) -> float | None:
        """外すべきでなかった割合。**判定できなかった件を分母に入れない。**"""
        return self.kept_by_mistake / self.decided if self.decided else None

    def summary(self) -> str:
        """1行のまとめ。"""
        if not self.excluded:
            return "権利落ちで外した急落は無い。"
        share = self.wrongly_excluded
        told = "判定できた件が無い" if share is None else f"**{share:.1%} は外すべきでなかった**"
        return (
            f"外した {self.excluded:,} 件のうち、判定できたのは {self.decided:,} 件。"
            f"{told}。配当利回りの中央値 {self.median_yield:.2%}、"
            f"特別配当は {self.special:,} 件。"
        )

    def warnings(self) -> list[str]:
        """気付かなくても目に入るべきこと。**早期 return しない。**"""
        found: list[str] = []
        if not self.excluded:
            return []
        if self.undecided:
            share = self.undecided / self.excluded
            found.append(
                f"**{self.undecided:,} 件（{share:.1%}）は判定できなかった。** "
                "配当の額か価格が引けない。**割合の分母に入れていない。**"
            )
        if self.zero_rate:
            share = self.zero_rate / self.excluded
            found.append(
                f"**{self.zero_rate:,} 件（{share:.1%}）は、額 0 の権利落ちで外していた。** "
                "無配の公表にも `ExDate` は入る。**落ちるものが無い日で外している。**"
            )
        if self.revised:
            found.append(
                f"**{self.revised:,} 件は、外したときの権利落ち日が最終データに無い。** "
                "**訂正された日で外している。** 判定していない。"
            )
        if self.outside_window:
            found.append(
                f"**{self.outside_window:,} 件は、配当が下げに効かない位置"
                f"（基準日そのもの）に在った。** 除外の窓が {KNIFE_DAYS + 1} 営業日"
                f"あるが、**比を動かせるのは {KNIFE_DAYS} 日ぶんだけ**である。"
                "**窓が1日広い。**"
            )
        share = self.wrongly_excluded
        if share is not None and share > 0.5:  # noqa: PLR2004 - 過半なら規則のほうが悪い
            found.append(
                f"**判定できた件の {share:.1%} は、配当を戻しても急落である。** "
                "外しているのは配当が作った下げではなく、**本物の急落**である"
                "——除外の窓が6営業日あるので、配当は線の向こうに押し出す役"
                "しかしていない。"
            )
        return found


@dataclasses.dataclass(frozen=True)
class HoldingDividends:
    """**保有する窓の中**の権利落ち。外していないほうである。"""

    kept: int
    with_ex_date: int
    median_yield: float
    drag: float
    """1イベントあたり、ショートの取り高を押し上げている分。"""

    @property
    def share(self) -> float:
        """窓の中に権利落ちが在った割合。"""
        return self.with_ex_date / self.kept if self.kept else 0.0

    def summary(self) -> str:
        """1行のまとめ。"""
        if not self.kept:
            return "使った急落が無い。"
        return (
            f"使った {self.kept:,} 件のうち、**保有する {HOLDING} 営業日に"
            f"権利落ちが在ったのは {self.with_ex_date:,} 件（{self.share:.1%}）。** "
            f"利回りの中央値 {self.median_yield:.2%}、"
            f"ショートの取り高を **{self.drag:+.3%}/件** 押し上げている。"
        )

    def warnings(self) -> list[str]:
        """気付かなくても目に入るべきこと。"""
        if self.drag > 0.001:  # noqa: PLR2004 - 0.1% は 1.2% の線に対して効く
            return [
                f"**押し上げ {self.drag:+.3%}/件 は無視できない。** ショートでは"
                "配当を払う側なので、**払っていない価格で測っている。**"
            ]
        return []


def _sessions(database: Database, symbol: str) -> tuple[list[dt.date], np.ndarray, np.ndarray]:
    """``(営業日, 調整後終値, 調整前終値)``。**利回りは調整前で割る。**"""
    from stock_ai.database.repository import PriceRepository

    with database.session() as session:
        raw = PriceRepository(session).get_raw_prices(symbol)
    if raw.empty:
        return [], np.array([]), np.array([])
    adjusted = split_adjusted(raw)
    return (
        [stamp.date() for stamp in adjusted.index],
        adjusted[CLOSE].to_numpy(dtype=float),
        raw[CLOSE].to_numpy(dtype=float),
    )


def _yield_on(rates: Rates, symbol: str, when: dt.date, before: float) -> float | None:
    """その権利落ちの利回り。**読めない・ありえない値なら ``None``。**"""
    found = rates.get(symbol, {}).get(when)
    if found is None or before <= 0:
        return None
    ratio = found.rate / before
    # **0 は「読めない」ではなく「落ちるものが無い」。** 呼ぶ側が分ける。
    if ratio < 0 or ratio > MAX_YIELD:
        return None
    return ratio


def measure_alignment(  # noqa: PLR0913 - 期間と刻みを全部受け取る
    database: Database,
    rates: Rates,
    first: dt.date,
    last: dt.date,
    span: int = SPAN,
    progress: Callable[[int, int], None] | None = None,
) -> Alignment:
    """権利落ち日の前後で、値がどこで下がっているかを並べる。

    **中央値で見る。** 1日のリターンは裾が重いので、平均では外れ値が勝つ。

    Args:
        database: 価格の保存先。
        rates: :func:`~stock_ai.data.jquants_dividend.ex_dividend_rates` の形。
        first: この日以降の権利落ちだけ。
        last: この日以前の権利落ちだけ。
        span: 前後何営業日ずつ並べるか。
        progress: ``(済み, 全体)`` で呼ばれる。**1行に収めること。**

    Returns:
        :class:`Alignment`。

    Raises:
        ValueError: ``span`` が 1 未満。
    """
    if span < 1:
        raise ValueError(f"span must be at least 1; got {span}.")

    offsets = tuple(range(-span, span + 1))
    buckets: dict[int, list[float]] = {offset: [] for offset in offsets}
    yields: list[float] = []
    symbols = events = 0

    names = sorted(rates)
    for position, symbol in enumerate(names, start=1):
        if progress is not None:
            progress(position, len(names))
        when_of, closes, raw_closes = _sessions(database, symbol)
        if not when_of:
            continue
        symbols += 1
        index_of = {day: i for i, day in enumerate(when_of)}
        for when in sorted(rates[symbol]):
            if when < first or when > last:
                continue
            index = index_of.get(when)
            # **前日と、前後 span 本が要る。** 足りなければ数に入れない。
            if index is None or index - span - 1 < 0 or index + span >= len(closes):
                continue
            ratio = _yield_on(rates, symbol, when, raw_closes[index - 1])
            # **並べるのは実際に落ちた配当だけ。** 無配を混ぜると中央値が薄まる。
            if not ratio:
                continue
            events += 1
            yields.append(ratio)
            for offset in offsets:
                here = index + offset
                buckets[offset].append(closes[here] / closes[here - 1] - 1.0)

    return Alignment(
        offsets=offsets,
        medians=tuple(median(buckets[offset]) if buckets[offset] else 0.0 for offset in offsets),
        counts=tuple(len(buckets[offset]) for offset in offsets),
        median_yield=median(yields) if yields else 0.0,
        events=events,
        symbols=symbols,
    )


def audit_exclusions(  # noqa: PLR0913, PLR0912, PLR0915 - 処分を1件ずつ数えるので分岐が多い
    database: Database,
    excluded: Sequence[tuple[str, dt.date]],
    rates: Rates,
    announced: dict[str, list[tuple[dt.date, dt.date]]] | None = None,
    drop: float = KNIFE_DROP,
    days: int = KNIFE_DAYS,
    progress: Callable[[int, int], None] | None = None,
) -> Exclusions:
    """外した急落を、**配当を戻して**測り直す。

    戻しても ``drop`` を超えるなら、**その急落は配当が作ったものではない。**

    **外した理由になった日を、外したときと同じ引き方で作り直す**
    （``announced`` を渡したとき）。最終データの権利落ち日で代用すると、
    **訂正された日で外した件が「基準日の配当」に化ける。**

    Args:
        database: 価格の保存先。
        excluded: 権利落ちで外した ``(銘柄, 日)``。
        rates: :func:`~stock_ai.data.jquants_dividend.ex_dividend_rates` の形。
        announced: ``(公表日, 権利落ち日)`` の並び。**外したときの引き方。**
        drop: 急落と呼ぶ幅。
        days: 急落を測る営業日数。
        progress: ``(済み, 全体)`` で呼ばれる。

    Returns:
        :class:`Exclusions`。
    """
    from stock_ai.backtest.gap_fill import known_ex_dates

    still = saved = outside = zero = revised = undecided = special = 0
    yields: list[float] = []
    months: dict[int, int] = {}

    by_symbol: dict[str, list[dt.date]] = {}
    for symbol, when in excluded:
        by_symbol.setdefault(symbol, []).append(when)

    for position, symbol in enumerate(sorted(by_symbol), start=1):
        if progress is not None:
            progress(position, len(by_symbol))
        when_of, closes, raw_closes = _sessions(database, symbol)
        index_of = {day: i for i, day in enumerate(when_of)}
        own = (announced or {}).get(symbol)
        for when in by_symbol[symbol]:
            months[when.month] = months.get(when.month, 0) + 1
            index = index_of.get(when)
            if index is None or index - days < 0 or closes[index - days] <= 0:
                undecided += 1
                continue

            # **外した理由になった日を、外したときと同じ引き方で作り直す。**
            triggers = known_ex_dates(own, when) if own is not None else set(rates.get(symbol, {}))
            # **下げに効くのは ``days`` 日ぶんだけ。** 基準日に落ちた配当は
            # 比のどちらにも同じだけ乗るので、1つも動かさない。
            here = [
                when_of[step]
                for step in range(index - days + 1, index + 1)
                if when_of[step] in triggers
            ]
            if not here:
                outside += 1
                continue

            known = rates.get(symbol, {})
            if any(day not in known for day in here):
                # **外したときの日が、最終データに無い。** 訂正されている。
                revised += 1
                continue

            factor = 1.0
            unreadable = False
            paid: list[float] = []
            for day in here:
                ratio = _yield_on(rates, symbol, day, raw_closes[index_of[day] - 1])
                if ratio is None:
                    unreadable = True
                    break
                if ratio > 0:
                    paid.append(ratio)
                    factor /= 1.0 - ratio
                if known[day].has_special:
                    special += 1
            if unreadable:
                undecided += 1
                continue
            if not paid:
                # **額が 0。** 落ちるものが無い日で外していた。
                zero += 1
                continue

            yields.extend(paid)
            fell = closes[index] / closes[index - days] - 1.0
            without = (1.0 + fell) * factor - 1.0
            if without <= -drop:
                still += 1
            else:
                saved += 1

    return Exclusions(
        excluded=len(excluded),
        still_qualifies=still,
        rescued=saved,
        outside_window=outside,
        zero_rate=zero,
        revised=revised,
        undecided=undecided,
        special=special,
        median_yield=median(yields) if yields else 0.0,
        by_month=tuple(sorted(months.items())),
    )


def audit_holding_window(
    database: Database,
    kept: Sequence[tuple[str, dt.date]],
    rates: Rates,
    holding: int = HOLDING,
    progress: Callable[[int, int], None] | None = None,
) -> HoldingDividends:
    """**保有する窓の中**の権利落ちを数える。外していないほうである。

    ショートでは配当は払う側なので、配当を落としていない価格で測ると
    **取り高が高く出る。** その大きさを1イベントあたりで出す。

    Args:
        database: 価格の保存先。
        kept: 実際に使った ``(銘柄, 日)``。
        rates: :func:`~stock_ai.data.jquants_dividend.ex_dividend_rates` の形。
        holding: 保有営業日数。
        progress: ``(済み, 全体)`` で呼ばれる。

    Returns:
        :class:`HoldingDividends`。
    """
    per_event: list[float] = []
    yields: list[float] = []
    touched = 0

    by_symbol: dict[str, list[dt.date]] = {}
    for symbol, when in kept:
        by_symbol.setdefault(symbol, []).append(when)

    for position, symbol in enumerate(sorted(by_symbol), start=1):
        if progress is not None:
            progress(position, len(by_symbol))
        when_of, _closes, raw_closes = _sessions(database, symbol)
        index_of = {day: i for i, day in enumerate(when_of)}
        for when in by_symbol[symbol]:
            index = index_of.get(when)
            if index is None:
                per_event.append(0.0)
                continue
            total = 0.0
            for step in range(index + 1, min(index + holding + 1, len(when_of))):
                ratio = _yield_on(rates, symbol, when_of[step], raw_closes[step - 1])
                if not ratio:
                    continue
                total += ratio
                yields.append(ratio)
            if total:
                touched += 1
            per_event.append(total)

    return HoldingDividends(
        kept=len(kept),
        with_ex_date=touched,
        median_yield=median(yields) if yields else 0.0,
        drag=fmean(per_event) if per_event else 0.0,
    )
