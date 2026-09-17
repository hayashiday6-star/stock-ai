"""イベント日から N 営業日の超過リターン。**説をまたいで1つだけ置く。**

#8（増担保）と #5（上方修正）が同じ形を使う——**イベント日の翌営業日の寄付き
で入り、N 営業日後の終値で降り、同じ期間の市場を引く。** 違うのは窓の長さと
向き（買いか売りか）だけである。

**`margin_census` に置いたまま #5 から呼ぶと、そのうち2つ目が書かれる。**
`key_period` を、それを戒める文章を書いた同じ日に2つ目書いた前例がある。

**向きはここで決めない。** 超過リターンをそのまま返し、買いか売りかは呼ぶ側が
反転させる。**反転を2箇所に置くと、どちらで反転したのか分からなくなる。**
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from stock_ai.core.logging import get_logger

logger = get_logger(__name__)


def event_returns(
    database: object,
    events: Sequence[tuple[str, dt.date]],
    holding: int,
    benchmark: str = "1306",
    until: dt.date | None = None,
) -> list[float]:
    """発動イベントごとの**超過リターン**を、公表日ごとにまとめて返す。

    事前登録 §4 のとおり——**公表日 D の翌営業日の寄付き**で入り、
    ``holding`` 営業日後の終値で降りる。同じ期間のベンチマークを引く。

    **D の終値では入れない。** 公表は 16:30 頃で、その日の引けには間に合わない。
    `AppDate` を D に置くのも同じ理由で禁じてある（§4）。

    **同じ日の発動は等加重の1つにまとめる。** まとめないと、発動が重なった日
    だけ重みが増え、独立でない観測を独立として数えることになる。

    **符号はそのまま返す。** 仮説は負を期待するが、ここで反転させない——
    反転を2箇所に置くと、どちらで反転したのか分からなくなる。

    Args:
        database: 価格の保存先。
        events: ``(銘柄, 公表日)``。**絞り込みは済んでいる前提。**
        holding: 保有営業日数（§3 の N）。
        benchmark: 控除するベンチマーク。
        until: この日までのイベントだけ使う。**降りる日が越えてもよい**——
            イベントが IS にあるかどうかで切る。境目を落とすと端が薄くなる。

    Returns:
        公表日ごとの超過リターン。**古い順。**

    Raises:
        ValueError: ``holding`` が 1 未満、またはベンチマークの価格が無い。
    """
    import pandas as pd

    from stock_ai.data.schema import CLOSE, OPEN, split_adjusted
    from stock_ai.database.repository import PriceRepository

    if holding < 1:
        raise ValueError(f"holding must be at least 1; got {holding}.")

    wanted: dict[str, list[dt.date]] = {}
    for symbol, day in events:
        if until is None or day <= until:
            wanted.setdefault(symbol, []).append(day)

    by_day: dict[dt.date, list[float]] = {}
    with database.session() as session:  # type: ignore[attr-defined]
        prices = PriceRepository(session)
        bench = split_adjusted(prices.get_raw_prices(benchmark))
        if bench.empty:
            raise ValueError(f"ベンチマーク {benchmark!r} の価格が無い。")
        bench_open = bench[OPEN].to_numpy(dtype=float)
        bench_close = bench[CLOSE].to_numpy(dtype=float)
        bench_at = {stamp.date(): index for index, stamp in enumerate(bench.index)}

        for symbol, days in wanted.items():
            raw = prices.get_raw_prices(symbol)
            if raw.empty:
                continue
            adjusted = split_adjusted(raw)
            index = adjusted.index
            at = {stamp.date(): position for position, stamp in enumerate(index)}
            opens = adjusted[OPEN].to_numpy(dtype=float)
            close = adjusted[CLOSE].to_numpy(dtype=float)

            for when in days:
                position = at.get(when)
                if position is None or position + holding >= len(index):
                    continue
                entry, leave = opens[position + 1], close[position + holding]
                if not (entry > 0) or not (leave > 0):
                    continue
                if pd.isna(entry) or pd.isna(leave):
                    continue
                # ベンチマークは**同じ日付**で取る。位置で取ると、その銘柄に
                # 足の無い日があったぶんだけずれる。
                mark = bench_at.get(index[position + 1].date())
                out = bench_at.get(index[position + holding].date())
                if mark is None or out is None:
                    continue
                if not (bench_open[mark] > 0) or not (bench_close[out] > 0):
                    continue
                by_day.setdefault(when, []).append(
                    (leave / entry) - (bench_close[out] / bench_open[mark])
                )

    return [sum(values) / len(values) for _day, values in sorted(by_day.items())]
