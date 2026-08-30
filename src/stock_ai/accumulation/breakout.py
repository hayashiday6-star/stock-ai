"""Phase 3: has the base actually broken, and where does the trade stop working.

The five tests are sequential in time, not independent, and one of them can be
neither passed nor failed on the day it matters: "the next session opened no
lower" has no answer while the breakout *is* the latest bar. That is recorded
as insufficient rather than failed, because failing it would downgrade the
freshest signal in the run purely for being fresh.

Every unmet test carries the number that would meet it, and every symbol
carries stop levels as prices rather than adjectives. A breakout call without a
level at which it is wrong is not a call.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pandas as pd

from stock_ai.accumulation.types import Measure, Missing, insufficient, is_value
from stock_ai.data.schema import CLOSE, LOW, OPEN, VOLUME
from stock_ai.technical.indicators import atr, bollinger_bands, on_balance_volume, sma

#: How far back a breakout still counts as "the" breakout being judged.
LOOKBACK_BARS = 20

#: The brief's volume test on the breakout day.
BREAKOUT_VOLUME_MULTIPLE = 3.0


@dataclass
class Check:
    """One of the five breakout tests."""

    label: str
    met: bool | Missing
    detail: str
    needed: str = ""

    @property
    def achieved(self) -> bool:
        """Whether it counts towards the n/5."""
        return self.met is True

    @property
    def mark(self) -> str:
        """○ / × / - for the report."""
        if isinstance(self.met, Missing):
            return "-"
        return "○" if self.met else "×"


@dataclass
class Breakout:
    """The phase-3 verdict for one symbol, with the levels it hangs on."""

    symbol: str
    checks: list[Check]
    breakout_date: dt.date | Missing
    bb_upper: Measure
    stop_bb_middle: Measure
    stop_20d_low: Measure
    stop_atr: Measure
    last_close: Measure

    @property
    def score(self) -> int:
        """How many of the five are met."""
        return sum(1 for check in self.checks if check.achieved)

    @property
    def confidence(self) -> str:
        """The brief's n/5."""
        return f"{self.score}/5"


def _flow_on(flow: pd.DataFrame | None, day: dt.date) -> Measure:
    """Large-order net inflow on one date, from a moomoo capital-flow frame."""
    if flow is None or flow.empty or "capital_flow_item_time" not in flow.columns:
        return insufficient("資金フローが無い")
    stamps = pd.to_datetime(flow["capital_flow_item_time"], errors="coerce").dt.date
    row = flow[stamps == day]
    if row.empty:
        return insufficient(f"{day.isoformat()} の資金フロー行が無い")
    try:
        return float(row["super_in_flow"].iloc[0]) + float(row["big_in_flow"].iloc[0])
    except (KeyError, TypeError, ValueError):
        return insufficient("super/big が読めない")


def evaluate(symbol: str, prices: pd.DataFrame, flow: pd.DataFrame | None) -> Breakout:
    """Run the five breakout tests and compute the stop levels."""
    short = insufficient("60本に満たない")
    if len(prices) < 60:
        return Breakout(symbol, [], short, short, short, short, short, short)

    bands = bollinger_bands(prices, window=20)
    upper = bands["upper"]
    close = prices[CLOSE]
    last_close = float(close.iloc[-1])
    bb_upper = float(upper.iloc[-1])

    # The most recent close above the upper band, within the lookback.
    crossed = [
        position
        for position in range(len(prices) - LOOKBACK_BARS, len(prices))
        if position >= 0
        and pd.notna(upper.iloc[position])
        and float(close.iloc[position]) > float(upper.iloc[position])
    ]
    checks: list[Check] = []

    if not crossed:
        gap = bb_upper - last_close
        checks.append(
            Check(
                "① 終値がBB上限を上抜け",
                False,
                f"終値 ${last_close:,.2f} / BB上限 ${bb_upper:,.2f}",
                f"終値 ${bb_upper:,.2f} 以上（あと ${gap:,.2f}、{gap / last_close * 100:.1f}%）",
            )
        )
        breakout_date: dt.date | Missing = insufficient("直近20本に上抜けなし")
        for label in (
            "② 上抜け日の出来高が20日平均3倍以上",
            "③ 上抜け日に Large order が NetIn プラス",
            "④ 翌日の寄付きが前日終値以上",
        ):
            checks.append(
                Check(label, insufficient("上抜け日が無い"), "上抜け未発生", "①の達成が前提")
            )
    else:
        index = crossed[-1]
        day = prices.index[index].date()
        breakout_date = day
        checks.append(
            Check(
                "① 終値がBB上限を上抜け",
                True,
                f"{day.isoformat()} 終値 ${float(close.iloc[index]):,.2f} > BB上限 "
                f"${float(upper.iloc[index]):,.2f}",
            )
        )

        prior = prices[VOLUME].iloc[max(0, index - 20) : index]
        average = float(prior.mean()) if len(prior) else 0.0
        volume = float(prices[VOLUME].iloc[index])
        if average > 0:
            multiple = volume / average
            checks.append(
                Check(
                    "② 上抜け日の出来高が20日平均3倍以上",
                    multiple >= BREAKOUT_VOLUME_MULTIPLE,
                    f"{multiple:.2f}倍（{volume:,.0f} / 20日平均 {average:,.0f}）",
                    ""
                    if multiple >= BREAKOUT_VOLUME_MULTIPLE
                    else f"出来高 {average * BREAKOUT_VOLUME_MULTIPLE:,.0f} 株以上",
                )
            )
        else:
            checks.append(
                Check(
                    "② 上抜け日の出来高が20日平均3倍以上", insufficient("20日平均が0"), "算出不能"
                )
            )

        large = _flow_on(flow, day)
        checks.append(
            Check(
                "③ 上抜け日に Large order が NetIn プラス",
                large if isinstance(large, Missing) else float(large) > 0,
                str(large)
                if isinstance(large, Missing)
                else f"Large NetIn {float(large) / 1e6:+,.2f}M",
                ""
                if is_value(large) and float(large) > 0
                else "上抜け日の Large NetIn がプラスであること",
            )
        )

        if index + 1 < len(prices):
            next_open = float(prices[OPEN].iloc[index + 1])
            breakout_close = float(close.iloc[index])
            checks.append(
                Check(
                    "④ 翌日の寄付きが前日終値以上",
                    next_open >= breakout_close,
                    f"寄付き ${next_open:,.2f} / 前日終値 ${breakout_close:,.2f}",
                    "" if next_open >= breakout_close else f"寄付き ${breakout_close:,.2f} 以上",
                )
            )
        else:
            checks.append(
                Check(
                    "④ 翌日の寄付きが前日終値以上",
                    insufficient("上抜けが最新足。翌日がまだ無い"),
                    "翌営業日待ち",
                    f"翌日の寄付きが ${float(close.iloc[index]):,.2f} 以上",
                )
            )

    obv = on_balance_volume(prices)
    obv_now = float(obv.iloc[-1])
    obv_high = float(obv.iloc[-LOOKBACK_BARS:].max())
    checks.append(
        Check(
            "⑤ OBVが直近20日高値を更新",
            obv_now >= obv_high,
            f"OBV {obv_now:,.0f} / 20日高値 {obv_high:,.0f}",
            "" if obv_now >= obv_high else f"OBV {obv_high:,.0f} 超え",
        )
    )

    middle = float(sma(prices, window=20).iloc[-1])
    low20 = float(prices[LOW].iloc[-LOOKBACK_BARS:].min())
    atr14 = float(atr(prices, window=14).iloc[-1])
    return Breakout(
        symbol=symbol,
        checks=checks,
        breakout_date=breakout_date,
        bb_upper=bb_upper,
        stop_bb_middle=middle,
        stop_20d_low=low20,
        stop_atr=last_close - 2.0 * atr14,
        last_close=last_close,
    )


def classify(completion_percent: float, score: int) -> str:
    """The brief's A/B/C/D bucket."""
    if completion_percent < 60.0:
        return "D=見送り"
    if score >= 5:
        return "A=ブレイクアウト確定"
    if score >= 3:
        return "B=初動確認"
    return "C=仕込み継続中"
