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

除外しているのは**急落を作った5営業日**だけで、**保有する5営業日は見ていない。**
ショートでは配当は払う側なので、**配当を落としていない価格で測ると取り高が
高く出る。** その大きさをここで出す——`#16` の +0.69% と同じ単位で。
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Callable, Sequence
from statistics import fmean, median

import numpy as np

from stock_ai.backtest.knife import HOLDING, KNIFE_DAYS, KNIFE_DROP, MIN_TURNOVER
from stock_ai.core.logging import get_logger
from stock_ai.data.jquants_dividend import ExDividend
from stock_ai.data.schema import CLOSE, OPEN, dividend_adjusted, split_adjusted
from stock_ai.database.engine import Database

logger = get_logger(__name__)

#: 権利落ち日の前後、何営業日ずつ並べるか。
SPAN = 2

#: これを超える利回りは読み違いとして落とす。**1回の配当で株価の半分は落ちない。**
MAX_YIELD = 0.5

#: 下げと利回りが何倍まで違ってよいか。**両向きに当てる。**
#: 3倍にしていたら、2.4倍のずれが通った（2026-09-20）。
EXPLAINED_BAND = 1.5

#: 線の上にちょうど乗っているとみなす幅。**丸めの 1 ulp を吸うだけ**で、
#: 意味のある差はこれよりはるかに大きい（−20% に対して 1e-9）。
_TIE = 1e-9

#: 額 0 の群で、権利落ち日にこれを超える段差が出たら読み違いを疑う。
#: **利回りの中央値（約1.3%）の4分の1。** 無配なら段差は出ない。
ZERO_STEP_LIMIT = 0.003

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
    zero_events: int = 0
    """額 0 と読んだ権利落ちのうち、値を引けた数。"""

    zero_on_the_day: float = 0.0
    """その群の**権利落ち日**の中央値リターン。**0 に近いはず。**"""

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
        if not self.events and not self.zero_events:
            return ["**権利落ちを1件も値付けできなかった。**"]
        if not self.events:
            # **ここで return しない。** 額 0 の検査はこの下に在る——早期
            # return が新しい検査を黙らせた（2026-09-20、自分で踏んだ）。
            found.append(
                f"**額のある権利落ちが1件も無い。** 値付けできた {self.zero_events:,} 件は"
                "すべて額 0 だった。**並べる中央値は作れない。**"
            )
        elif not self.aligned:
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
        # **額 0 のほうも価格で確かめる。** 正の額は 0.99倍で裏が取れたが、
        # **0 と読んだ 31.4% には裏取りが無かった**（2026-09-20）。本当に
        # 無配なら、権利落ち日に段差は出ない。
        if self.zero_events and abs(self.zero_on_the_day) > ZERO_STEP_LIMIT:
            found.append(
                f"**額 0 と読んだ {self.zero_events:,} 件に、権利落ち日の段差が"
                f"ある（{self.zero_on_the_day:+.3%}）。** 本当に無配なら出ない"
                "——**額の読み方が違う。**"
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
class AdjustmentEffect:
    """**配当を落とすと、急落の数がどう変わるか。**

    `#16` は「権利落ちが窓に在れば外す」で機械的な値下がりを避けていたが、
    それは事前登録 §3 の**代理**であって intent そのものではなかった
    ——実データで**本物の急落を 189 件巻き込んでいた**（2026-09-20、
    ユーザーが指摘）。

    **先に落としてから線を当てる**ようにしたので、ここが効き目を数える。
    """

    before: int
    """落とさずに数えた急落（銘柄 × 日）。"""

    after: int
    """落として数えた急落。"""

    lost: int
    """**落としたら事象でなくなった。** 配当が線の向こうに押し出していた。"""

    ties: int
    """**線の上にちょうど乗っていて、丸めで転んだ件数。**

    `1000 → 800` はちょうど −20% で、**日本株ではよくある形**である。
    両辺に同じ倍率を掛けると、`(800f)/(1000f)` は `0.8` からずれることが
    あり、**どちらにも転ぶ**（実測では「事象になる」ほうが多い）。

    **理屈の上では増えないが、浮動小数では増える。** 初め増加そのものを
    禁じていて、実データで落ちた（2026-09-20、ユーザーの PC で 2461）。
    """

    symbols: int

    def __post_init__(self) -> None:
        """増えたぶんが、同点で説明できる範囲に収まること。

        **理屈の上では増えない**——窓の中の権利落ちは分母（基準日）だけを
        下げるので、落とせば下げは必ず浅くなる。**増えるとすれば線の上に
        ちょうど乗っていた分だけ**である。

        Raises:
            ValueError: 数が負、または同点で説明できないほど増えた。
        """
        if min(self.before, self.after, self.lost, self.ties) < 0:
            raise ValueError("件数が負になっている。")
        if self.after > self.before + self.ties:
            raise ValueError(
                f"落とした後 {self.after} 件が、前 {self.before} 件＋同点 "
                f"{self.ties} 件を超えた。**調整の向きが逆である。**"
            )

    def summary(self) -> str:
        """1行のまとめ。"""
        if not self.before:
            return "急落を1件も拾えなかった。**比べていない。**"
        tied = f"（うち線の上にちょうど乗っていたのが {self.ties:,} 件）" if self.ties else ""
        return (
            f"{self.symbols:,} 銘柄。配当を落とす前 {self.before:,} 件、"
            f"落とした後 {self.after:,} 件——**配当が作っていた {self.lost:,} 件"
            f"（{self.lost / self.before:.1%}）が消えた。**{tied}"
        )

    def warnings(self) -> list[str]:
        """気付かなくても目に入るべきこと。**早期 return しない。**"""
        found: list[str] = []
        if not self.before:
            return ["**急落を1件も拾えなかった。**"]
        if not self.lost:
            found.append(
                "**配当を落としても急落の集合が1件も変わらない。** "
                "額が当たっていないかもしれない——`ex_dividends_known_by` の"
                "権利落ち日が価格の日付と揃っているかを見ること。"
            )
        return found


def measure_adjustment(  # noqa: PLR0913 - 事前登録が固定した条件をすべて受け取る
    database: Database,
    rates: dict[str, list[dt.date | float]] | object,
    first: dt.date,
    last: dt.date,
    drop: float = KNIFE_DROP,
    days: int = KNIFE_DAYS,
    min_turnover: float = MIN_TURNOVER,
    progress: Callable[[int, int], None] | None = None,
) -> AdjustmentEffect:
    """配当を落とす前と後で、急落の集合がどう変わるかを数える。

    **効果は1つも計算しない。** 件数だけである。

    Args:
        database: 価格の保存先。
        rates: :func:`~stock_ai.data.jquants_dividend.ex_dividends_known_by` の形。
        first: この日以降の急落だけ。
        last: この日以前の急落だけ。
        drop: 急落と呼ぶ幅。
        days: 急落を測る営業日数。
        min_turnover: 流動性の下限（円）。
        progress: ``(済み, 全体)`` で呼ばれる。

    Returns:
        :class:`AdjustmentEffect`。

    Raises:
        ValueError: 銘柄が1つも無い。
    """
    from stock_ai.backtest.gap_fill import liquid_bars
    from stock_ai.backtest.knife import knife_positions
    from stock_ai.data.schema import VOLUME, dividend_adjusted, split_adjusted
    from stock_ai.database.repository import PriceRepository, list_securities

    with database.session() as session:
        names = [sym for sym, market in list_securities(session) if market == "JP"]
    if not names:
        raise ValueError("銘柄が1つも無い。価格を取り込んでいない。")

    before = after = lost = ties = read = 0
    for position, symbol in enumerate(sorted(names), start=1):
        if progress is not None:
            progress(position, len(names))
        with database.session() as session:
            raw = PriceRepository(session).get_raw_prices(symbol)
        if raw.empty:
            continue
        read += 1
        plain = split_adjusted(raw)
        # **割る相手は調整前の終値。** 調整後で割ると、分割より前の権利落ちが
        # 分割比のぶん余計に落ちる（2026-09-20 に再現）。
        netted, _counted = dividend_adjusted(
            plain,
            rates.get(symbol),  # type: ignore[union-attr]
            base=raw[CLOSE].to_numpy(dtype=float),
        )
        when_of = [stamp.date() for stamp in plain.index]
        volumes = plain[VOLUME].to_numpy(dtype=float)

        # **流動性は落とす前の値で見る。** 両方の枝で同じにしないと、
        # 比較が「下げの変化」ではなく「流動性の変化」を拾う
        # （2026-09-20、ユーザーが件数の食い違いから見つけた）。
        liquid = liquid_bars(plain[CLOSE].to_numpy(dtype=float), volumes, min_turnover)

        def _hits(
            frame: object,
            *,
            _when=when_of,
            _liquid=liquid,
        ) -> tuple[set[int], np.ndarray]:
            closes = frame[CLOSE].to_numpy(dtype=float)  # type: ignore[index]
            fall = np.full(len(closes), np.nan)
            fall[days:] = closes[days:] / np.where(closes[:-days] > 0, closes[:-days], np.nan) - 1
            return (
                {
                    index
                    for index in knife_positions(closes, _liquid, drop, days)
                    if first <= _when[index] <= last
                },
                fall,
            )

        was, was_fall = _hits(plain)
        now, now_fall = _hits(netted)
        # **理屈の上では増えない。** 窓の中の権利落ちは分母（基準日）だけを
        # 下げるので、落とせば下げは必ず浅くなる。**ただし線の上にちょうど
        # 乗っていると、丸めでどちらにも転ぶ**——`1000 → 800` はちょうど
        # −20% で、日本株ではよくある形である（2026-09-20、実データで落ちた）。
        #
        # **だから「増えた」ではなく「本当に深くなった」で見る。**
        deeper = [index for index in now - was if now_fall[index] < was_fall[index] - _TIE]
        if deeper:
            raise ValueError(
                f"{symbol}: 配当を落としたら下げが深くなった（{len(deeper)} 件）。"
                "**調整の向きが逆である。**"
            )
        moved = was ^ now
        tied = {index for index in moved if abs(now_fall[index] - was_fall[index]) <= _TIE}
        before += len(was)
        after += len(now)
        ties += len(tied)
        lost += len((was - now) - tied)

    return AdjustmentEffect(before=before, after=after, lost=lost, ties=ties, symbols=read)


@dataclasses.dataclass(frozen=True)
class HoldingDividends:
    """**保有する窓の中**の権利落ち。外していないほうである。"""

    kept: int
    measured: int = 0
    """``drag`` と ``removed`` を**同じ窓で**数えられた急落の数。

    **``kept`` より少ない。** 窓が最後まで無い足（上場廃止・期間の端）は
    両方から外している——**片方だけ外すと、2つの列の分母が違う。**
    """

    with_ex_date: int = 0
    median_yield: float = 0.0
    drag: float = 0.0
    """**調整しなければ乗っていた**押し上げ。窓の中の配当の大きさである。

    **残っている量ではない。** ここは価格を調整したかどうかを1度も見て
    いない——「消えているはず」と書いていたが、**それは主張であって確認
    ではなかった**（2026-09-20、ユーザーが出力の自己矛盾から指摘）。
    確認するのは :attr:`removed` のほう。
    """

    removed: float = 0.0
    """**調整が実際に抜いた分。** 同じ窓のリターンを、調整の前と後で引いた差。

    :attr:`drag` と一致すれば、**配当ぶんちょうど抜けたことが測れた**ことに
    なる。0 なら調整が当たっていない。2倍なら二重に抜いている。
    """

    @property
    def share(self) -> float:
        """窓の中に権利落ちが在った割合。**分母は測れた数。**"""
        return self.with_ex_date / self.measured if self.measured else 0.0

    def summary(self) -> str:
        """1行のまとめ。"""
        if not self.kept:
            return "使った急落が無い。"
        return (
            f"使った {self.kept:,} 件のうち窓が最後まで在るのは {self.measured:,} 件。"
            f"**保有する {HOLDING} 営業日に権利落ちが在ったのは "
            f"{self.with_ex_date:,} 件（{self.share:.1%}）。** "
            f"利回りの中央値 {self.median_yield:.2%}。"
            f"**調整しなければ {self.drag:+.3%}/件 の押し上げ**になっていたところ、"
            f"**調整が抜いたのは {self.removed:+.3%}/件。**"
        )

    @property
    def matched(self) -> bool:
        """抜けた分が、窓の中の配当と釣り合っているか。

        **ぴったり同じにはならない。** 抜けるのは `利回り × (1 + リターン)`
        で、窓のリターンぶんだけ大きい。**5営業日なら数 % のずれ**なので、
        **4分の1の幅**で見る——0 なら当たっていない、2倍なら二重である。
        """
        if not self.drag:
            return not self.removed
        return abs(self.removed - self.drag) <= 0.25 * abs(self.drag)

    def warnings(self) -> list[str]:
        """気付かなくても目に入るべきこと。**早期 return しない。**"""
        found: list[str] = []
        if not self.matched:
            found.append(
                f"**窓の中の配当は {self.drag:+.3%}/件 なのに、調整が抜いたのは "
                f"{self.removed:+.3%}/件。** 釣り合っていない——"
                "**0 なら当たっていない、2倍なら二重に抜いている。**"
            )
        if self.drag > 0.001 and not self.removed:  # noqa: PLR2004 - 0.1% は線に効く
            found.append(
                f"**押し上げ {self.drag:+.3%}/件 が抜けていない。** ショートでは"
                "配当を払う側なので、**払っていない価格で測っている。**"
            )
        return found


def _sessions(database: Database, symbol: str):
    """``(営業日, 分割調整後の足, 調整前終値)``。**利回りは調整前で割る。**"""
    from stock_ai.database.repository import PriceRepository

    with database.session() as session:
        raw = PriceRepository(session).get_raw_prices(symbol)
    if raw.empty:
        return [], None, np.array([])
    plain = split_adjusted(raw)
    return (
        [stamp.date() for stamp in plain.index],
        plain,
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
    # **額 0 と読んだ群も、別に並べる。** 本当に無配なら段差は出ない。
    zero_day: list[float] = []
    symbols = events = 0

    names = sorted(rates)
    for position, symbol in enumerate(names, start=1):
        if progress is not None:
            progress(position, len(names))
        when_of, plain, raw_closes = _sessions(database, symbol)
        if not when_of:
            continue
        closes = plain[CLOSE].to_numpy(dtype=float)
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
            if ratio is None:
                continue
            if not ratio:
                # **額 0。** 表には混ぜない（中央値が薄まる）が、**段差が
                # 出ないことは確かめる。**
                zero_day.append(closes[index] / closes[index - 1] - 1.0)
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
        zero_events=len(zero_day),
        zero_on_the_day=median(zero_day) if zero_day else 0.0,
    )


def audit_holding_window(  # noqa: PLR0913 - 前後を比べるので材料が多い
    database: Database,
    kept: Sequence[tuple[str, dt.date]],
    rates: Rates,
    paid: dict[str, list[tuple[dt.date, dt.date, float]]] | None = None,
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
        paid: :func:`~stock_ai.data.jquants_dividend.ex_dividends_known_by` の形。
            **測る側が使っているのと同じもの**を渡すこと——渡さなければ
            「抜けた分」は 0 になり、**調整を当てていないのと区別がつかない。**
        holding: 保有営業日数。
        progress: ``(済み, 全体)`` で呼ばれる。

    Returns:
        :class:`HoldingDividends`。
    """
    per_event: list[float] = []
    yields: list[float] = []
    taken: list[float] = []
    touched = 0

    by_symbol: dict[str, list[dt.date]] = {}
    for symbol, when in kept:
        by_symbol.setdefault(symbol, []).append(when)

    for position, symbol in enumerate(sorted(by_symbol), start=1):
        if progress is not None:
            progress(position, len(by_symbol))
        when_of, plain, raw_closes = _sessions(database, symbol)
        if not when_of:
            continue
        # **調整の前と後で、同じ窓のリターンを出す。** 差が窓の中の配当に
        # 一致すれば、**調整が配当ぶんちょうど抜いたことが測れた**ことになる
        # ——「消えているはず」は主張であって確認ではない（2026-09-20、
        # ユーザーが出力の自己矛盾から指摘）。
        netted, _counted = dividend_adjusted(plain, (paid or {}).get(symbol), base=raw_closes)
        opens_plain = plain[OPEN].to_numpy(dtype=float)
        closes_plain = plain[CLOSE].to_numpy(dtype=float)
        opens_net = netted[OPEN].to_numpy(dtype=float)
        closes_net = netted[CLOSE].to_numpy(dtype=float)
        index_of = {day: i for i, day in enumerate(when_of)}
        for when in by_symbol[symbol]:
            index = index_of.get(when)
            if index is None:
                continue
            entry, exit_at = index + 1, index + holding
            # **窓が最後まで無い足は、両方から外す。** 片方だけ数えると
            # `drag` と `removed` の分母が違ってしまう——**同じ行の2つの列が、
            # 別々の標本を指す**形である（`CLAUDE.md`、3度踏んでいる）。
            if exit_at >= len(when_of) or opens_plain[entry] <= 0 or opens_net[entry] <= 0:
                continue
            taken.append(
                (closes_net[exit_at] / opens_net[entry])
                - (closes_plain[exit_at] / opens_plain[entry])
            )
            # **entry 当日（``index + 1``）の権利落ちは数えない。** 株価は
            # その日の**寄付きで**落ちるので、**その寄付きで入るこちらは
            # 落ちた後の値段で入っており、配当は最初から乗っていない。**
            #
            # 倍率の側からも同じことが言える——権利落ちが entry 以前なら
            # entry と exit の**両方に同じ倍率が掛かって相殺する**ので、
            # `taken` は 0 になる。**置き方から確かめた**（2026-09-20）。
            # 揃える前は `drag` だけがこの日を数えていて、**2つの列が1日
            # 違う窓を見ていた。**
            total = 0.0
            for step in range(index + 2, exit_at + 1):
                ratio = _yield_on(rates, symbol, when_of[step], raw_closes[step - 1])
                if not ratio:
                    continue
                total += ratio
                yields.append(ratio)
            if total:
                touched += 1
            per_event.append(total)

    if len(per_event) != len(taken):
        raise ValueError(
            f"drag を {len(per_event)} 件、removed を {len(taken)} 件で数えている。"
            " **同じ窓で数えること。**"
        )
    return HoldingDividends(
        kept=len(kept),
        measured=len(per_event),
        with_ex_date=touched,
        median_yield=median(yields) if yields else 0.0,
        drag=fmean(per_event) if per_event else 0.0,
        removed=fmean(taken) if taken else 0.0,
    )
