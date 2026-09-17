"""イベント日から N 営業日の超過リターン。**説をまたいで1つだけ置く。**

#8（増担保）と #5（上方修正）が同じ形を使う——**イベント日の翌営業日の寄付き
で入り、N 営業日後の終値で降り、同じ期間の市場を引く。** 違うのは窓の長さと
向き（買いか売りか）だけである。

**`margin_census` に置いたまま #5 から呼ぶと、そのうち2つ目が書かれる。**
`key_period` を、それを戒める文章を書いた同じ日に2つ目書いた前例がある。

**向きはここで決めない。** 超過リターンをそのまま返し、買いか売りかは呼ぶ側が
反転させる。**反転を2箇所に置くと、どちらで反転したのか分からなくなる。**

## 落ちた件数を黙って捨てない（2026-09-17）

陰性対照をこの管に通したら、**乱数で選んだ銘柄と日で `t` の平均が +0.49** 出た。
情報が何も無いのに、である。出どころを探そうとして、**この関数が何件を、どの
理由で捨てたのかを一度も出していない**ことに気付いた。

`event_sample` が**処分の内訳**を返す。内訳は足すと引いた件数に一致する
（`EventSample.__post_init__` が確かめる）——**どこにも数えられずに消える経路を
作らない**ためである。

そして**銘柄側と指数側を別々に返す。** 超過リターンだけを見ていると、
「銘柄が上がった」のか「引く相手が上がらなかった」のかが分からない。
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Sequence
from statistics import fmean

from stock_ai.core.logging import get_logger

logger = get_logger(__name__)


@dataclasses.dataclass(frozen=True)
class EventSample:
    """窓を当てた結果と、**捨てた件数の内訳。**

    件数はすべて**イベント件数**である（日数ではない）。`values` だけが
    公表日ごとにまとめた後の並びで、こちらは**日数**になる。

    **`used` と捨てた4つを足すと `drawn` になる。** 成り立たない経路を書いたら
    生成時に落ちる。
    """

    values: list[float]
    """公表日ごとの超過リターン。**古い順。**"""

    drawn: int
    """渡されたイベント数（`until` で切った後）。"""

    used: int
    no_prices: int
    """銘柄の価格が1本も無い。"""

    ended_early: int
    """窓が価格の終わりを越える。**その銘柄の足が全体の最終日より前で切れている**
    ——上場廃止・長期停止。**落ちるのは悪く終わった側に偏りうる。**"""

    too_recent: int
    """窓が価格の終わりを越える。**銘柄の足は最後まで在る**——単に期間の端に近い。
    こちらは偏らない。"""

    bad_leg: int
    """入る値か降りる値が 0 か欠測。"""

    no_benchmark: int
    """対応する日がベンチマークに無い。"""

    stock_leg: float
    """銘柄側の素のリターンの平均（日ごとにまとめた後）。"""

    bench_leg: float
    """指数側の素のリターンの平均（同じ日で取る）。"""

    truncated: list[float]
    """`ended_early` で落ちた分を、**足の在るところまで**で測った超過リターン。

    **落ちた側がどれだけ悪かったか**を測る。空なら、その経路は測れていない。
    """

    def __post_init__(self) -> None:
        """内訳が引いた件数に足し合わさることを確かめる。"""
        parts = (
            self.used,
            self.no_prices,
            self.ended_early,
            self.too_recent,
            self.bad_leg,
            self.no_benchmark,
        )
        if sum(parts) != self.drawn:
            raise ValueError(
                f"処分の内訳 {sum(parts)} が引いた件数 {self.drawn} に合わない。"
                "**どこにも数えられずに消えた経路がある。**"
            )

    @property
    def dropped(self) -> int:
        """捨てた件数。"""
        return self.drawn - self.used

    @property
    def dropped_share(self) -> float:
        """捨てた割合。**件数ではなく割合で見る。**"""
        return self.dropped / self.drawn if self.drawn else 0.0

    def survivorship_bias(self) -> float | None:
        """**上場廃止で落ちた分が、平均をどれだけ押し上げているか。**

        落とした側の平均が測れているときだけ出す。押し上げは
        ``割合 × (残った平均 − 落ちた平均)`` である。

        Returns:
            1イベントあたりの押し上げ。測れないときは ``None``。
        """
        if not self.drawn or not self.truncated or not self.values:
            return None
        share = self.ended_early / self.drawn
        return share * (fmean(self.values) - fmean(self.truncated))

    def warnings(self) -> list[str]:
        """気付かなくても目に入るべきこと。**表は読む側が気付く必要がある。**"""
        found: list[str] = []
        if not self.drawn:
            return ["**イベントが1件も無い。**"]
        if not self.used:
            # **ここで return しない。** 全部捨てたときこそ、その理由が要る。
            # 手前の検査が全部を弾いたとき後ろの数字が 0 のまま「異常なし」の
            # 顔をする形を、#5 で踏んでいる。
            found.append(f"**{self.drawn:,} 件すべて捨てた。** 窓が一度も当たっていない。")
        elif self.dropped_share > 0.05:
            found.append(
                f"**引いた {self.drawn:,} 件のうち {self.dropped:,} 件"
                f"（{self.dropped_share:.1%}）を捨てている。**"
            )
        if self.ended_early:
            share = self.ended_early / self.drawn
            lifted = self.survivorship_bias()
            told = f"、押し上げ **{lifted:+.2%}/件**" if lifted is not None else ""
            found.append(
                f"**上場廃止・停止で {self.ended_early:,} 件（{share:.1%}）が落ちている**"
                f"{told}。**落ちるのは悪く終わった側に偏る。**"
            )
        return found


def event_sample(  # noqa: PLR0912, PLR0915 - 処分を1件ずつ数えるので分岐が多い
    database: object,
    events: Sequence[tuple[str, dt.date]],
    holding: int,
    benchmark: str = "1306",
    until: dt.date | None = None,
) -> EventSample:
    """窓を当てて、**使えた分と捨てた分の両方**を返す。

    事前登録 §4 のとおり——**公表日 D の翌営業日の寄付き**で入り、
    ``holding`` 営業日後の終値で降りる。同じ期間のベンチマークを引く。

    **D の終値では入れない。** 公表は 16:30 頃で、その日の引けには間に合わない。
    `AppDate` を D に置くのも同じ理由で禁じてある（§4）。

    **同じ日の発動は等加重の1つにまとめる。** まとめないと、発動が重なった日
    だけ重みが増え、独立でない観測を独立として数えることになる。

    **符号はそのまま返す。** 仮説は負を期待するが、ここで反転させない。

    Args:
        database: 価格の保存先。
        events: ``(銘柄, 公表日)``。**絞り込みは済んでいる前提。**
        holding: 保有営業日数（§3 の N）。
        benchmark: 控除するベンチマーク。
        until: この日までのイベントだけ使う。**降りる日が越えてもよい。**

    Returns:
        :class:`EventSample`。

    Raises:
        ValueError: ``holding`` が 1 未満、またはベンチマークの価格が無い。
    """
    import pandas as pd

    from stock_ai.data.schema import CLOSE, OPEN, split_adjusted
    from stock_ai.database.repository import PriceRepository

    if holding < 1:
        raise ValueError(f"holding must be at least 1; got {holding}.")

    wanted: dict[str, list[dt.date]] = {}
    drawn = 0
    for symbol, day in events:
        if until is None or day <= until:
            wanted.setdefault(symbol, []).append(day)
            drawn += 1

    by_day: dict[dt.date, list[float]] = {}
    stock_by_day: dict[dt.date, list[float]] = {}
    bench_by_day: dict[dt.date, list[float]] = {}
    truncated: list[float] = []
    no_prices = ended_early = too_recent = bad_leg = no_benchmark = 0

    with database.session() as session:  # type: ignore[attr-defined]
        prices = PriceRepository(session)
        bench = split_adjusted(prices.get_raw_prices(benchmark))
        if bench.empty:
            raise ValueError(f"ベンチマーク {benchmark!r} の価格が無い。")
        bench_open = bench[OPEN].to_numpy(dtype=float)
        bench_close = bench[CLOSE].to_numpy(dtype=float)
        bench_at = {stamp.date(): index for index, stamp in enumerate(bench.index)}
        last_session = bench.index[-1].date()

        for symbol, days in wanted.items():
            raw = prices.get_raw_prices(symbol)
            if raw.empty:
                no_prices += len(days)
                continue
            adjusted = split_adjusted(raw)
            index = adjusted.index
            at = {stamp.date(): position for position, stamp in enumerate(index)}
            opens = adjusted[OPEN].to_numpy(dtype=float)
            close = adjusted[CLOSE].to_numpy(dtype=float)
            # **足が全体の最終日より前で切れているか。** 切れていれば、窓が
            # 越えた分は上場廃止・停止であって、期間の端ではない。
            stops_early = index[-1].date() < last_session

            for when in days:
                position = at.get(when)
                if position is None:
                    no_prices += 1
                    continue
                if position + holding >= len(index):
                    if stops_early:
                        ended_early += 1
                        _partial = _truncated_excess(
                            position, index, opens, close, bench_at, bench_open, bench_close
                        )
                        if _partial is not None:
                            truncated.append(_partial)
                    else:
                        too_recent += 1
                    continue
                entry, leave = opens[position + 1], close[position + holding]
                if not (entry > 0) or not (leave > 0) or pd.isna(entry) or pd.isna(leave):
                    bad_leg += 1
                    continue
                # ベンチマークは**同じ日付**で取る。位置で取ると、その銘柄に
                # 足の無い日があったぶんだけずれる。
                mark = bench_at.get(index[position + 1].date())
                out = bench_at.get(index[position + holding].date())
                if mark is None or out is None:
                    no_benchmark += 1
                    continue
                if not (bench_open[mark] > 0) or not (bench_close[out] > 0):
                    no_benchmark += 1
                    continue
                stock = leave / entry
                market = bench_close[out] / bench_open[mark]
                by_day.setdefault(when, []).append(stock - market)
                stock_by_day.setdefault(when, []).append(stock - 1.0)
                bench_by_day.setdefault(when, []).append(market - 1.0)

    values = [sum(day) / len(day) for _when, day in sorted(by_day.items())]
    return EventSample(
        values=values,
        drawn=drawn,
        used=sum(len(day) for day in by_day.values()),
        no_prices=no_prices,
        ended_early=ended_early,
        too_recent=too_recent,
        bad_leg=bad_leg,
        no_benchmark=no_benchmark,
        stock_leg=_day_mean(stock_by_day),
        bench_leg=_day_mean(bench_by_day),
        truncated=truncated,
    )


def _day_mean(by_day: dict[dt.date, list[float]]) -> float:
    """日ごとにまとめてから平均する。**超過と同じまとめ方で取る。**

    同じ形で取るので、``stock_leg - bench_leg`` は超過の平均に一致する。
    別のまとめ方にすると、差が合わなくなって比べられない。
    """
    if not by_day:
        return float("nan")
    return fmean([sum(day) / len(day) for _when, day in sorted(by_day.items())])


def _truncated_excess(  # noqa: PLR0913 - 価格の配列をそのまま受け取る
    position: int,
    index: object,
    opens: object,
    close: object,
    bench_at: dict[dt.date, int],
    bench_open: object,
    bench_close: object,
) -> float | None:
    """窓が越えた分を、**足の在る最後の日まで**で測る。

    **落とした側がどれだけ悪かったか**を測るためだけに使う。判定には入れない
    ——窓の長さが揃っていないので、他と同じ尺度ではない。

    Returns:
        超過リターン。入る値も取れないときは ``None``。
    """
    if position + 1 >= len(index):  # type: ignore[arg-type]
        return None
    entry = opens[position + 1]  # type: ignore[index]
    leave = close[-1]  # type: ignore[index]
    if not (entry > 0) or not (leave > 0):
        return None
    mark = bench_at.get(index[position + 1].date())  # type: ignore[index]
    out = bench_at.get(index[-1].date())  # type: ignore[index]
    if mark is None or out is None:
        return None
    if not (bench_open[mark] > 0) or not (bench_close[out] > 0):  # type: ignore[index]
        return None
    return (leave / entry) - (bench_close[out] / bench_open[mark])  # type: ignore[index]


def event_returns(
    database: object,
    events: Sequence[tuple[str, dt.date]],
    holding: int,
    benchmark: str = "1306",
    until: dt.date | None = None,
) -> list[float]:
    """公表日ごとの超過リターンだけを返す。**中身は `event_sample` 1つだけ。**

    Args:
        database: 価格の保存先。
        events: ``(銘柄, 公表日)``。
        holding: 保有営業日数。
        benchmark: 控除するベンチマーク。
        until: この日までのイベントだけ使う。

    Returns:
        公表日ごとの超過リターン。**古い順。**
    """
    return event_sample(database, events, holding, benchmark=benchmark, until=until).values
