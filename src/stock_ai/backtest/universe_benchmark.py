"""引く相手を、**持ち方と同じ加重で**作る。

## なぜ要るか

イベント型の陰性対照で、**乱数で選んだ銘柄と日を20営業日持つと指数に勝った**
——情報が何も無いのに、である（2026-09-17、400回・800,000件）。

分解したら出どころははっきりしていた。

| | 1イベントあたり |
|---|---|
| 銘柄側 | +1.11% |
| 指数側（`1306`） | +0.88% |
| **差** | **+0.22%** |
| うち生存フィルタ | −0.00% |

**バグではない。** `1306` は時価総額加重で、イベントのバスケットは等加重である。
**加重が違うものを引き算していた。** 年におよそ +2.8%、小型株の割増として
ありふれた大きさで、**ロングの説には下駄を、ショートの説には重しを**付けていた。

## ここが作るもの

各営業日 `D` について、**その日に窓を開けられた銘柄すべての等加重平均**
——`D+1` の寄付きで入り、`D+holding` の終値で降りたときのリターン。

**イベント側とまったく同じ数え方をする。** 銘柄ごとに自分の足で `D+1` と
`D+holding` を取り、足りない銘柄はその日の平均に入らない。片方だけ別の数え方を
すると、差が「説の効果」ではなく「数え方の違い」になる。

## 分かっていて残している食い違い

**説の側には流動性の絞り込みが掛かっているが、ここには掛かっていない。**
`eligible` で同じ絞り込みを渡せるようにしてあるが、#5・#8 は候補イベントに
ついてしか流動性を測っていないので、宇宙全体には当てられない。

**絞り込みを掛けないほうが小型に寄る**ので、ロングの説には**辛く**出る。
`1306` のときと向きが逆である。**0 になったわけではない**——小さくして向きを
変えただけで、そう書いておく。
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Callable, Iterator

from stock_ai.core.logging import get_logger

logger = get_logger(__name__)

#: 1日の平均を作るのに要る最低銘柄数。
#:
#: **少ない日の平均は、平均ではない。** 3銘柄の等加重は「宇宙」ではなく、
#: たまたま足の在った3社である。下回る日は値を出さず、**そこに落ちたイベントは
#: `no_benchmark` に数えられる**——黙って消えない。
#:
#: 30 は「指数と呼べる最低限」の目安であって、**測って出した値ではない。**
#: そう書いておく。
MIN_SYMBOLS_PER_DAY = 30


@dataclasses.dataclass(frozen=True)
class UniverseBenchmark:
    """日ごとの等加重平均リターンと、**その元になった数。**"""

    holding: int
    window: dict[dt.date, float]
    """``D`` → ``D+1`` 寄付きから ``D+holding`` 終値までの等加重平均リターン。"""

    counted: dict[dt.date, int]
    """その日の平均に入った銘柄数。**平均だけ見て、何社の平均かを見ない形を作らない。**"""

    symbols: int
    """足を読んだ銘柄数。**間引いたあとの数である。**"""

    fraction: float
    """銘柄をどれだけ使ったか。`1.0` なら全部。"""

    min_symbols: int
    """その日の平均を出すのに要った最低銘柄数。**間引けば一緒に下がる。**

    **下げないと、間引きが日を落としてしまう。** それでは期間が変わり、
    「精度だけを動かす」という実験にならない——最初そうなって、テストが
    **全日を落として**落ちた（2026-09-18）。
    """

    last_session: dt.date
    """読んだ中でいちばん新しい日。**上場廃止と期間の端を分けるのに使う。**"""

    thin_days: int
    """銘柄数が足りず、値を出さなかった日。"""

    def get(self, when: dt.date) -> float | None:
        """その日の等加重平均。**無ければ `None`**（0 ではない）。"""
        return self.window.get(when)

    def warnings(self) -> list[str]:
        """気付かなくても目に入るべきこと。"""
        found: list[str] = []
        if not self.window:
            # **ここで return しない。** 1日も作れなかったときこそ理由が要る。
            # 手前の検査が全部を弾くと後ろが黙る形を、`EventSample` で踏んでいる。
            found.append("**1日も平均が作れなかった。** 引く相手が無い。")
        if self.fraction < 1.0:
            found.append(
                f"**引く相手を {self.fraction:.0%} の銘柄で作っている**"
                f"（{self.symbols:,} 銘柄、足切り {self.min_symbols} 社）。"
                "**診断用である。判定には使わない。**"
            )
        if self.thin_days:
            found.append(
                f"**{self.thin_days:,} 日は銘柄が {self.min_symbols} 社に届かず、"
                "値を出していない。** そこに落ちたイベントは判定に入らない。"
            )
        if not self.counted:
            return found
        thinnest = min(self.counted.values())
        if thinnest < self.min_symbols * 2:
            found.append(f"**いちばん薄い日で {thinnest:,} 社。** 期間の初めが薄いことが多い。")
        return found


def equal_weighted_windows(  # noqa: PLR0913 - 何で作ったかを全部受け取る
    database: object,
    holding: int,
    *,
    symbols: list[str] | None = None,
    eligible: Callable[[str, dt.date], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
    fraction: float = 1.0,
    seed: int = 0,
    min_symbols: int | None = None,
) -> UniverseBenchmark:
    """日ごとの**等加重平均の窓リターン**を作る。

    Args:
        database: 価格の保存先。
        holding: 保有営業日数。**イベント側と同じ値を渡すこと。**
        symbols: 対象の銘柄。省くと JP の全銘柄。
        eligible: ``(銘柄, 日)`` で絞る。省くと絞らない。
        progress: ``(済み, 全体)`` で呼ばれる。**1行に収めること。**
        fraction: 銘柄をこの割合だけ使う。**引く相手の精度だけを落とす**ための
            口である（`1.0` なら全部）。**日は1日も減らない**ので、
            `MIN_SYMBOLS_PER_DAY` を上げるのと違って**期間が変わらない。**
        seed: 間引きの種。**記録すれば手で再現できる。**
        min_symbols: その日の平均を出すのに要る最低銘柄数。省くと
            `MIN_SYMBOLS_PER_DAY` を ``fraction`` で割り引いた値になる——
            **割り引かないと、間引きが日まで落としてしまう。**

    Returns:
        :class:`UniverseBenchmark`。

    Raises:
        ValueError: ``holding`` が 1 未満、``fraction`` が 0〜1 の外、
            または銘柄が1つも無い。
    """
    import numpy as np

    from stock_ai.data.schema import CLOSE, OPEN, split_adjusted
    from stock_ai.database.repository import PriceRepository, list_securities

    if holding < 1:
        raise ValueError(f"holding must be at least 1; got {holding}.")
    if not 0.0 < fraction <= 1.0:
        raise ValueError(f"fraction must be in (0, 1]; got {fraction}.")
    # **間引いたら足切りも一緒に下げる。** 下げないと薄い日が落ちて期間が変わり、
    # 「引く相手の精度だけを動かす」という実験でなくなる。
    floor = min_symbols if min_symbols is not None else round(MIN_SYMBOLS_PER_DAY * fraction)
    floor = max(1, floor)

    total: dict[dt.date, float] = {}
    counted: dict[dt.date, int] = {}
    last_session: dt.date | None = None

    with database.session() as session:  # type: ignore[attr-defined]
        wanted = symbols
        if wanted is None:
            wanted = [sym for sym, market in list_securities(session) if market == "JP"]
        if not wanted:
            raise ValueError("銘柄が1つも無い。等加重の平均を作れない。")
        if fraction < 1.0:
            # **日ではなく銘柄を間引く。** 日を減らすと期間が変わり、散らばりが
            # 動いた理由が「精度」か「期間」か分からなくなる。
            rng = np.random.default_rng(seed)
            keep = max(1, round(len(wanted) * fraction))
            picked = rng.choice(len(wanted), size=keep, replace=False)
            wanted = [wanted[int(index)] for index in sorted(picked)]

        prices = PriceRepository(session)
        for done, symbol in enumerate(wanted, start=1):
            if progress is not None:
                progress(done, len(wanted))
            raw = prices.get_raw_prices(symbol)
            if raw.empty:
                continue
            adjusted = split_adjusted(raw)
            index = adjusted.index
            opens = adjusted[OPEN].to_numpy(dtype=float)
            close = adjusted[CLOSE].to_numpy(dtype=float)
            if last_session is None or index[-1].date() > last_session:
                last_session = index[-1].date()
            for when, value in _windows(index, opens, close, holding):
                if eligible is not None and not eligible(symbol, when):
                    continue
                total[when] = total.get(when, 0.0) + value
                counted[when] = counted.get(when, 0) + 1

    if last_session is None:
        raise ValueError("価格の在る銘柄が1つも無い。等加重の平均を作れない。")

    # **薄い日は値を出さない。** 出すと「3社の等加重」が「宇宙」を名乗る。
    window = {when: total[when] / count for when, count in counted.items() if count >= floor}
    thin = sum(1 for count in counted.values() if count < floor)
    kept = {when: count for when, count in counted.items() if when in window}
    logger.info(
        "等加重の引く相手: %d 日（%d 銘柄、薄くて外した日 %d）",
        len(window),
        len(kept),
        thin,
    )
    return UniverseBenchmark(
        holding=holding,
        window=window,
        counted=kept,
        symbols=len(wanted),
        fraction=fraction,
        min_symbols=floor,
        last_session=last_session,
        thin_days=thin,
    )


def _windows(
    index: object,
    opens: object,
    close: object,
    holding: int,
) -> Iterator[tuple[dt.date, float]]:
    """1銘柄ぶんの ``(D, D+1 寄付き → D+holding 終値)`` を流す。

    **イベント側と同じ形で取る。** `D+holding` が足の外に出る日は流さない
    ——イベント側も同じ理由で落としている。

    Yields:
        ``(D, リターン)``。取れない日は飛ばす。
    """
    length = len(index)  # type: ignore[arg-type]
    for position in range(length):
        if position + holding >= length:
            break
        entry = opens[position + 1]  # type: ignore[index]
        leave = close[position + holding]  # type: ignore[index]
        if not (entry > 0) or not (leave > 0):
            continue
        yield index[position].date(), (leave / entry) - 1.0  # type: ignore[index]


def equal_weighted_daily(
    database: object,
    *,
    symbols: list[str] | None = None,
    min_symbols: int = MIN_SYMBOLS_PER_DAY,
    progress: Callable[[int, int], None] | None = None,
) -> dict[dt.date, float]:
    """日ごとの**等加重平均の日次リターン**。

    ## `equal_weighted_windows` と別物である

    | | 返すもの |
    |---|---|
    | `equal_weighted_windows` | 日 → **その日から `holding` 営業日の窓**の平均 |
    | ここ | 日 → **その日1日**の平均 |

    **窓のほうから日次は取り出せない。** `holding=1` にしても
    「翌日の寄付き → 翌日の終値」であって、**終値どうしの変化ではない。**
    #13（月替わり）が要るのは後者である。

    **共有するのは薄い日の足切りだけ。** 3社の等加重は「宇宙」ではない、
    という理由は同じである。

    Args:
        database: 価格の保存先。
        symbols: 対象の銘柄。省くと JP の全銘柄。
        min_symbols: その日の平均を出すのに要る最低銘柄数。
        progress: ``(済み, 全体)`` で呼ばれる。**1行に収めること。**

    Returns:
        日付 → 等加重平均の日次リターン。**薄い日は入らない**（0 ではない）。

    Raises:
        ValueError: 銘柄が1つも無い、または ``min_symbols`` が 1 未満。
    """
    from stock_ai.data.schema import CLOSE, split_adjusted
    from stock_ai.database.repository import PriceRepository, list_securities

    if min_symbols < 1:
        raise ValueError(f"min_symbols must be at least 1; got {min_symbols}.")

    total: dict[dt.date, float] = {}
    counted: dict[dt.date, int] = {}

    with database.session() as session:  # type: ignore[attr-defined]
        wanted = symbols
        if wanted is None:
            wanted = [sym for sym, market in list_securities(session) if market == "JP"]
        if not wanted:
            raise ValueError("銘柄が1つも無い。等加重の平均を作れない。")

        prices = PriceRepository(session)
        for done, symbol in enumerate(wanted, start=1):
            if progress is not None:
                progress(done, len(wanted))
            raw = prices.get_raw_prices(symbol)
            if raw.empty:
                continue
            adjusted = split_adjusted(raw)
            closes = adjusted[CLOSE].to_numpy(dtype=float)
            index = adjusted.index
            for position in range(1, len(index)):
                before, after = closes[position - 1], closes[position]
                if not (before > 0) or not (after > 0):
                    continue
                when = index[position].date()
                total[when] = total.get(when, 0.0) + (after / before - 1.0)
                counted[when] = counted.get(when, 0) + 1

    # **薄い日は値を出さない。** 出すと「3社の等加重」が「宇宙」を名乗る。
    found = {when: total[when] / count for when, count in counted.items() if count >= min_symbols}
    logger.info("等加重の日次: %d 日（薄くて外した日 %d）", len(found), len(counted) - len(found))
    return found
