"""Phase 2: what the flow, the float and the chart say about one symbol.

Three of the brief's four blocks are only partly answerable, and the parts that
are not answerable are not close calls - dark-pool share, block prints and
borrow fees are sold, not published, and nothing this project can reach carries
them. They are recorded as :class:`~stock_ai.accumulation.types.Missing` rather
than omitted, because a section that simply disappears reads as "nothing
notable" instead of "not measured".

One metric deserves singling out. The brief asks for 大口比率 as *Large ÷ total
volume*. moomoo reports funding flow as a net currency amount per order-size
band, not as the share volume those bands traded, so that ratio cannot be
formed from it at all - the numerator and denominator are different quantities.
Rather than compute something adjacent and let it be read as the thing asked
for, the specified metric is marked unavailable and a clearly labelled
substitute (large net inflow against dollar turnover) is offered beside it.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from stock_ai.accumulation.types import (
    Measure,
    Missing,
    insufficient,
    is_value,
    not_implemented,
    unavailable,
)
from stock_ai.broker.moomoo import MoomooConfig, capital_flow
from stock_ai.core.exceptions import BrokerError
from stock_ai.data.schema import CLOSE, HIGH, LOW, VOLUME
from stock_ai.technical.indicators import (
    bollinger_bands,
    macd,
    on_balance_volume,
    rsi,
    sma,
)

#: moomoo allows 30 capital-flow calls per 30 seconds. One second between
#: symbols keeps a five-symbol run comfortably inside that without making the
#: command feel stalled.
FLOW_CALL_INTERVAL_SECONDS = 1.0

FLOW_WINDOW_DAYS = 10


@dataclass
class FlowMetrics:
    """Funding flow over the last :data:`FLOW_WINDOW_DAYS` sessions."""

    large_net_in: Measure
    medium_net_in: Measure
    small_net_in: Measure
    total_net_in: Measure
    #: The brief's 大口比率. Not formable from this source - see module docstring.
    large_share_of_volume: Measure
    #: Offered in its place, and labelled as a substitute wherever it is shown.
    large_net_in_over_turnover: Measure
    open30_close30_skew: Measure
    prepost_abnormal_volume: Measure
    sessions: int = 0


@dataclass
class InstitutionalMetrics:
    """Ownership and print-level activity. Almost none of it is reachable."""

    dark_pool_index: Measure
    block_trades: Measure
    form_13f_change: Measure
    form_4_activity: Measure


@dataclass
class ShortMetrics:
    """Short side, as far as a delayed public feed carries it."""

    short_interest_of_float: Measure
    short_interest_prior: Measure
    short_interest_change: Measure
    short_interest_change_prior: Measure
    days_to_cover: Measure
    borrow_fee: Measure


@dataclass
class TechnicalMetrics:
    """The chart block."""

    bollinger_width: Measure
    sma_max_divergence: Measure
    rsi14: Measure
    macd_histogram: Measure
    obv_change_10d: Measure
    ad_line_change_10d: Measure
    vwap20: Measure
    price_vs_vwap: Measure
    bb_upper: Measure
    obv_is_20d_high: bool | Missing


@dataclass
class Condition:
    """One of the seven accumulation tests."""

    label: str
    met: bool | Missing
    detail: str

    @property
    def achieved(self) -> bool:
        """Whether it counts towards the score. A Missing never does."""
        return self.met is True


@dataclass
class Completion:
    """The accumulation score: seven equal conditions."""

    conditions: list[Condition] = field(default_factory=list)

    @property
    def achieved(self) -> int:
        """How many conditions are met."""
        return sum(1 for c in self.conditions if c.achieved)

    @property
    def judgeable(self) -> int:
        """How many could be evaluated at all."""
        return sum(1 for c in self.conditions if not isinstance(c.met, Missing))

    @property
    def percent(self) -> float:
        """Completion as the brief defines it: achieved out of all seven."""
        return 100.0 * self.achieved / len(self.conditions) if self.conditions else 0.0

    @property
    def percent_of_judgeable(self) -> Measure:
        """Completion over only the conditions that could be evaluated.

        Reported beside the headline figure because an unmeasurable condition
        drags the headline down in a way that looks like evidence against the
        symbol. It is not evidence of anything.
        """
        if not self.judgeable:
            return insufficient("no condition could be evaluated")
        return 100.0 * self.achieved / self.judgeable


@dataclass
class Deep:
    """Everything phase 2 found for one symbol."""

    symbol: str
    flow: FlowMetrics
    institutional: InstitutionalMetrics
    short: ShortMetrics
    technical: TechnicalMetrics
    completion: Completion


# --------------------------------------------------------------------------
# Flow
# --------------------------------------------------------------------------


def _sum_column(frame: pd.DataFrame, column: str) -> Measure:
    if column not in frame.columns:
        return insufficient(f"{column} not returned")
    series = pd.to_numeric(frame[column], errors="coerce").dropna()
    return float(series.sum()) if len(series) else insufficient(f"{column} empty")


def flow_metrics(frame: pd.DataFrame | None, turnover_10d: Measure) -> FlowMetrics:
    """Reduce a moomoo capital-flow frame to the phase-2 figures.

    Large is super plus big. That grouping is the one the API's own
    ``main_in_flow`` uses - confirmed against 22 live daily rows where
    ``super + big`` equalled it exactly - so it is read from those two columns
    directly rather than trusting a field the docs leave undefined.
    """
    unreachable = unavailable("moomoo は場中データのみ、板・約定単位の情報は提供しない")
    if frame is None or frame.empty:
        nothing = insufficient("資金フローが取得できなかった")
        return FlowMetrics(
            large_net_in=nothing,
            medium_net_in=nothing,
            small_net_in=nothing,
            total_net_in=nothing,
            large_share_of_volume=unavailable("moomooは金額ベースの純流入のみ。出来高比は形成不能"),
            large_net_in_over_turnover=nothing,
            open30_close30_skew=unavailable(
                "INTRADAYは当日のみ。過去10日の寄付き/引け偏りは提供されない"
            ),
            prepost_abnormal_volume=unreachable,
        )

    tail = frame.tail(FLOW_WINDOW_DAYS)
    super_in = _sum_column(tail, "super_in_flow")
    big_in = _sum_column(tail, "big_in_flow")
    large: Measure
    if is_value(super_in) and is_value(big_in):
        large = float(super_in) + float(big_in)
    else:
        large = insufficient("super/big のいずれかが欠落")

    over_turnover: Measure
    if is_value(large) and is_value(turnover_10d) and float(turnover_10d) > 0:
        over_turnover = float(large) / float(turnover_10d)
    else:
        over_turnover = insufficient("売買代金が計算できない")

    return FlowMetrics(
        large_net_in=large,
        medium_net_in=_sum_column(tail, "mid_in_flow"),
        small_net_in=_sum_column(tail, "sml_in_flow"),
        total_net_in=_sum_column(tail, "in_flow"),
        large_share_of_volume=unavailable("moomooは金額ベースの純流入のみ。出来高比は形成不能"),
        large_net_in_over_turnover=over_turnover,
        open30_close30_skew=unavailable(
            "INTRADAYは当日のみ。過去10日の寄付き/引け偏りは提供されない"
        ),
        prepost_abnormal_volume=unreachable,
        sessions=len(tail),
    )


def fetch_flow(
    config: MoomooConfig,
    symbol: str,
    *,
    fetcher: Callable[..., Any] = capital_flow,
) -> pd.DataFrame | None:
    """Ask OpenD for a symbol's daily flow, returning ``None`` if it refuses.

    Refusal is expected rather than exceptional: moomoo grants quote access per
    market and does not serve every market this screen can name. It is turned
    into a missing metric rather than an aborted run.
    """
    try:
        return fetcher(config, symbol, period_type="DAY")
    except BrokerError:
        return None


# --------------------------------------------------------------------------
# Institutional, short
# --------------------------------------------------------------------------


def institutional_metrics() -> InstitutionalMetrics:
    """None of this block is reachable; each item says why in its own terms."""
    return InstitutionalMetrics(
        dark_pool_index=unavailable("FINRA ATS(週次・遅延)か有料フィードが必要"),
        block_trades=unavailable("約定単位のティックデータが必要"),
        form_13f_change=not_implemented("SEC EDGAR から取得可能。四半期・最大45日遅延"),
        form_4_activity=not_implemented("SEC EDGAR から取得可能"),
    )


def short_metrics(info: dict[str, Any] | Missing) -> ShortMetrics:
    """Short interest from the delayed public feed the price provider carries.

    Two snapshots is all it publishes, so "the last two changes" can only be
    one change. The second is reported as insufficient rather than repeated.
    """
    borrow = unavailable("借株コストはブローカー/有料フィードのみ")
    if isinstance(info, Missing):
        return ShortMetrics(info, info, info, info, info, borrow)

    def number(key: str) -> Measure:
        raw = info.get(key)
        if raw is None or isinstance(raw, bool):
            return insufficient(f"{key} が提供されていない")
        try:
            return float(raw)
        except (TypeError, ValueError):
            return insufficient(f"{key} を数値にできない")

    shares_short, prior, float_shares = (
        number("sharesShort"),
        number("sharesShortPriorMonth"),
        number("floatShares"),
    )

    def of_float(value: Measure) -> Measure:
        if is_value(value) and is_value(float_shares) and float(float_shares) > 0:
            return float(value) / float(float_shares)
        return insufficient("floatShares が無い")

    current, previous = of_float(shares_short), of_float(prior)
    change: Measure = (
        float(current) - float(previous)
        if is_value(current) and is_value(previous)
        else insufficient("2時点そろわない")
    )
    return ShortMetrics(
        short_interest_of_float=current,
        short_interest_prior=previous,
        short_interest_change=change,
        short_interest_change_prior=insufficient(
            "公開されるのは直近2時点のみ。1回前の増減は算出不能"
        ),
        days_to_cover=number("shortRatio"),
        borrow_fee=borrow,
    )


# --------------------------------------------------------------------------
# Technicals
# --------------------------------------------------------------------------


def accumulation_distribution(prices: pd.DataFrame) -> pd.Series:
    """The A/D line: volume weighted by where the close sat inside the bar."""
    high, low, close = prices[HIGH], prices[LOW], prices[CLOSE]
    span = (high - low).replace(0.0, np.nan)
    multiplier = ((close - low) - (high - close)) / span
    return (multiplier.fillna(0.0) * prices[VOLUME]).cumsum().rename("ad_line")


def technical_metrics(prices: pd.DataFrame) -> TechnicalMetrics:
    """The chart block for one symbol, from its canonical OHLCV frame."""
    short = insufficient("20日に満たない")
    if len(prices) < 60:
        return TechnicalMetrics(
            short, short, short, short, short, short, short, short, short, short
        )

    close = prices[CLOSE]
    price = float(close.iloc[-1])

    bands = bollinger_bands(prices, window=20)
    middle = float(bands["middle"].iloc[-1])
    upper = float(bands["upper"].iloc[-1])
    width: Measure = (upper - float(bands["lower"].iloc[-1])) / middle if middle else short

    averages = [float(sma(prices, window=w).iloc[-1]) for w in (5, 10, 20, 50)]
    divergence: Measure = (max(averages) - min(averages)) / price if price else short

    obv = on_balance_volume(prices)
    ad = accumulation_distribution(prices)
    macd_frame = macd(prices)

    window = prices.iloc[-20:]
    typical = (window[HIGH] + window[LOW] + window[CLOSE]) / 3.0
    volume_sum = float(window[VOLUME].sum())
    vwap: Measure = float((typical * window[VOLUME]).sum() / volume_sum) if volume_sum else short

    return TechnicalMetrics(
        bollinger_width=width,
        sma_max_divergence=divergence,
        rsi14=float(rsi(prices, window=14).iloc[-1]),
        macd_histogram=float(macd_frame["histogram"].iloc[-1]),
        obv_change_10d=float(obv.iloc[-1] - obv.iloc[-11]),
        ad_line_change_10d=float(ad.iloc[-1] - ad.iloc[-11]),
        vwap20=vwap,
        price_vs_vwap=(price - float(vwap)) / float(vwap) if is_value(vwap) else short,
        bb_upper=upper,
        obv_is_20d_high=bool(obv.iloc[-1] >= obv.iloc[-20:].max()),
    )


# --------------------------------------------------------------------------
# Completion score
# --------------------------------------------------------------------------


def completion_score(
    *,
    flow: FlowMetrics,
    technical: TechnicalMetrics,
    above_52w_low: float,
    range_20d: float,
    volume_multiple: float,
    institutional: InstitutionalMetrics,
) -> Completion:
    """Score the seven accumulation conditions from the brief.

    A condition whose inputs are missing is recorded as missing, not as failed.
    Both cost the symbol the same 14.3% in the headline figure - the brief
    defines it that way - but only one of them is a statement about the symbol,
    and the report shows which is which.
    """

    def flag(
        value: Measure, test: Callable[[float], bool], detail: Callable[[float], str]
    ) -> tuple[bool | Missing, str]:
        if isinstance(value, Missing):
            return value, str(value)
        return test(float(value)), detail(float(value))

    conditions: list[Condition] = []

    met, detail = flag(
        flow.large_net_in, lambda v: v > 0, lambda v: f"Large NetIn {v / 1e6:+,.2f}M"
    )
    conditions.append(Condition("Large order の累積NetInがプラス（最重要）", met, detail))

    # Measured, not assumed. The relaxation ladder can admit a symbol at 3x,
    # and a condition that reads "confirmed in phase 1" would then report a 5x
    # day that never happened - in the score that decides the whole verdict.
    conditions.append(
        Condition(
            "出来高5倍以上の異常日あり",
            volume_multiple >= 5.0,
            f"直近出来高 {volume_multiple:.2f}倍",
        )
    )
    conditions.append(
        Condition(
            "52週安値+15%以内",
            above_52w_low <= 0.15,
            f"安値比 +{above_52w_low * 100:.1f}%",
        )
    )
    conditions.append(
        Condition("20日レンジ10%以内", range_20d <= 0.10, f"レンジ {range_20d * 100:.1f}%")
    )

    met, detail = flag(
        technical.bollinger_width, lambda v: v <= 0.05, lambda v: f"BW {v * 100:.2f}%"
    )
    conditions.append(Condition("ボリンジャーバンド幅 5%以下", met, detail))

    met, detail = flag(
        technical.sma_max_divergence, lambda v: v <= 0.05, lambda v: f"最大乖離 {v * 100:.2f}%"
    )
    conditions.append(Condition("SMA 5/10/20/50 の乖離率5%以内", met, detail))

    # The seventh condition asks for either of two numbers, and neither exists:
    # the volume share cannot be formed from a net-currency feed, and the
    # dark-pool index is not published. Recorded as unmeasurable so it is never
    # read as "the large orders were not there".
    conditions.append(
        Condition(
            "大口比率50%以上 または DPI 45%以上",
            unavailable("出来高比は形成不能、DPIは非公開"),
            f"大口比率: {flow.large_share_of_volume} / DPI: {institutional.dark_pool_index}",
        )
    )
    return Completion(conditions)


def analyse(
    symbol: str,
    prices: pd.DataFrame,
    flow_frame: pd.DataFrame | None,
    info: dict[str, Any] | Missing,
    *,
    above_52w_low: float,
    range_20d: float,
    volume_multiple: float,
) -> Deep:
    """Run every phase-2 block for one symbol and score it."""
    window = prices.iloc[-FLOW_WINDOW_DAYS:]
    turnover: Measure = (
        float((window[CLOSE] * window[VOLUME]).sum()) if len(window) else insufficient("価格が無い")
    )

    flow = flow_metrics(flow_frame, turnover)
    technical = technical_metrics(prices)
    institutional = institutional_metrics()
    return Deep(
        symbol=symbol,
        flow=flow,
        institutional=institutional,
        short=short_metrics(info),
        technical=technical,
        completion=completion_score(
            flow=flow,
            technical=technical,
            above_52w_low=above_52w_low,
            range_20d=range_20d,
            volume_multiple=volume_multiple,
            institutional=institutional,
        ),
    )


def pace_flow_calls(index: int) -> None:
    """Sleep between flow calls so a run stays inside moomoo's rate limit."""
    if index:
        time.sleep(FLOW_CALL_INTERVAL_SECONDS)
