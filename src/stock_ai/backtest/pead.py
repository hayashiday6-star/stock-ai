"""決算後ドリフト（PEAD）の検証。事前登録は `docs/PREREG_PEAD_JP.md`。

**この登録は封印済みである（2026-09-02）。** ここに書く計算式は登録の
セクション3・4・5をそのまま実装したものであり、結果を見てから変えてはならない。
変えた場合は事後変更として登録に明記し、その分析は探索的扱いになる。

このモジュールが守っている、間違えると黙って別のものを測ってしまう点：

- **反応日 R は開示時刻で決まる。** 実測で8割が引け後開示だった。引け後開示の
  当日の値動きにはニュースが入っていないので、場中と同じに扱うと、並べ替えの
  基準が発表前のノイズになり、エントリーは発表による窓開けの後に来る。反応
  そのものをドリフトとして数えることになる（セクション3-1）
- **イベントは決算短信だけ。** 種類は名前で判定する（セクション3-2）
- **分位は暦月ごとに切る。** 市場全体のボラティリティが時期で違う（セクション3-3）
- **OOS は合否判定まで見ない。** 期間の指定を必須にしてあるのはそのため
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum

import pandas as pd

from stock_ai.data.schema import CLOSE, OPEN, VOLUME, split_adjusted
from stock_ai.database.engine import Database
from stock_ai.database.repository import (
    FinancialStatementRepository,
    PriceRepository,
    list_securities,
)

#: 東証の後場が終わる時刻。これ以降の開示は当日の値動きに入っていない。
SESSION_CLOSE = dt.time(15, 0)

#: 立会時間が 15:30 まで延びた日。これ以降は 15:30 が引け。
SESSION_EXTENDED_FROM = dt.date(2024, 11, 5)
EXTENDED_CLOSE = dt.time(15, 30)

#: 保有期間（営業日）。エントリーは R+1 の寄付き、決済は R+HOLDING_DAYS の終値。
HOLDING_DAYS = 60

#: 副次で記録する短い保有期間。
SECONDARY_HOLDING_DAYS = 20

#: 売買代金を平均する営業日数。反応日 R 自身は含めない。
TURNOVER_WINDOW = 20

#: ユニバースの流動性下限（円）。セクション2。
MIN_TURNOVER = 100_000_000.0

#: 分位の数。セクション3-3。
QUANTILES = 5

#: 片道コスト。ロング・ショートは両建てなので、差には往復2本ぶんが乗る。
ONE_WAY_COST = 0.0015

#: この文字列を ``DocType`` に含むものだけがイベント（セクション3-2）。
#: 件数ではなく名前で絞るのは、上位に出てこない変種（US基準・REIT など）を
#: 取りこぼさないためである。
EARNINGS_DOC_MARKER = "FinancialStatements"

#: 並べ替える変数。**これが2つの事前登録を分ける唯一の箇所である。**
#:
#: ``"reaction"`` は ``docs/PREREG_PEAD_JP.md``（不合格）が封印した、R 当日の
#: 市場対比リターン。``"sue"`` は ``docs/PREREG_SUE_JP.md`` が封印する、通期
#: 実績と直前に公表済みだった会社予想との乖離。**それ以外は全部同じ経路を
#: 通る。** そうしないと、結果の差が並べ替え方の違いなのか実装の違いなのかを
#: 分離できない。
SORT_REACTION = "reaction"
SORT_SUE = "sue"

#: IS と OOS の境界。セクション6。**リターンを見て決めたものではない。**
OOS_FROM = dt.date(2024, 1, 1)


class Period(StrEnum):
    """どの期間を計算するか。

    OOS を既定にしない。**合否判定はOOSで一度だけ行う**ものであり、
    「とりあえず全部出す」を既定にすると、その一度が失われる。
    """

    IS = "is"
    OOS = "oos"
    ALL = "all"

    def contains(self, when: dt.date) -> bool:
        """``when`` がこの期間に入るか。"""
        if self is Period.ALL:
            return True
        return (when >= OOS_FROM) if self is Period.OOS else (when < OOS_FROM)


@dataclass(frozen=True)
class Event:
    """1件の決算発表と、そこから測れるもの。

    リターンはすべて **TOPIX対比の超過リターン**。ベンチマークが取れない
    場合は :func:`build_events` が ``None`` を渡し、素のリターンになる
    （どちらであったかは :class:`EventSet` が記録する）。
    """

    symbol: str
    disclosed_on: dt.date
    reaction_on: dt.date
    """反応日 R。場中開示なら開示日、引け後開示なら翌営業日。"""
    intraday: bool
    """場中開示だったか。副次の分割軸（セクション5）。"""
    surprise: float
    """R の超過リターン。並べ替えの基準（セクション3-3）。"""
    forward: float
    """R+1 の寄付きから R+60 の終値までの超過リターン。"""
    forward_short: float
    """同じく R+20 まで。副次（セクション5）。"""
    turnover_20d: float
    """R を除く直近20営業日の平均売買代金。"""
    same_day_count: int = 0
    """同じ日に決算を出した会社数。混雑度（セクション3-4）。"""
    market_forward: float | None = None
    """同じ窓でベンチマークが動いた分。**判定には使わない。**

    超過リターンの水準が偏っているとき、それが銘柄側の話なのか
    ベンチマーク側の話なのかを、引き算の内訳として読めるようにする。
    """

    @property
    def month(self) -> str:
        """分位を切る単位。暦月（セクション3-3）。"""
        return f"{self.reaction_on.year:04d}-{self.reaction_on.month:02d}"


def is_earnings(doc_type: str | None) -> bool:
    """その開示が決算短信か（セクション3-2）。

    種類が分からない行は**イベントにしない**。「短信でなかった」ではなく
    「短信だと確認できていない」ので、予想修正を紛れ込ませる余地を残さない。
    """
    return bool(doc_type) and EARNINGS_DOC_MARKER in str(doc_type)


def session_close_on(when: dt.date) -> dt.time:
    """その日の引け時刻。2024-11-05 に 15:00 から 15:30 へ延びた。"""
    return EXTENDED_CLOSE if when >= SESSION_EXTENDED_FROM else SESSION_CLOSE


def reaction_position(
    index: pd.DatetimeIndex, disclosed_on: dt.date, disclosed_at: dt.time | None
) -> int | None:
    """反応日 R の位置を返す。判定できなければ ``None``（セクション3-1）。

    - 場中開示（引け前）: R は開示日そのもの。ニュースは当日の値動きに入る
    - 引け後開示: R は開示日の次の営業日。当日の値動きにニュースは入っていない
    - 時刻が無い: **除外**。既定値で埋めると、8割を占める引け後開示の反応日を
      1日取り違えたまま先に進む

    開示日が非営業日なら、その日以降の最初の営業日を開示日とみなす。
    """
    if disclosed_at is None:
        return None
    when = pd.Timestamp(disclosed_on)
    if len(index) == 0 or when < index[0]:
        # 価格が始まる前の開示には R が無い。searchsorted は 0 を返すので、
        # 弾かないと最初のバーを R と見なしてしまう。
        return None
    position = int(index.searchsorted(when, side="left"))
    if position >= len(index):
        return None
    if disclosed_at >= session_close_on(disclosed_on):
        position += 1
    return position if position < len(index) else None


def _excess(stock: float, benchmark: float | None) -> float:
    """個別のリターンからベンチマークを引く。無ければ素のまま返す。"""
    return stock if benchmark is None else stock - benchmark


def _benchmark_return(
    bench: pd.DataFrame | None,
    index: pd.DatetimeIndex,
    a: int,
    b: int,
    start_column: str = CLOSE,
) -> float | None:
    """``index[a]`` から ``index[b]`` までのベンチマークのリターン。

    ``start_column`` は**銘柄側と同じ足**を指す必要がある。銘柄のリターンが
    R+1 の寄付き起点なら、ベンチマークも R+1 の寄付き起点でなければ、
    R から R+1 への一晩ぶんだけベンチマーク側に余計に乗る。片側だけずれた
    引き算は、超過リターンに一方向の偏りを作る。

    日付で引き当てる。銘柄の営業日とベンチマークの営業日がずれている日は
    ``None`` を返し、そのイベントを落とす。
    """
    if bench is None:
        return None
    try:
        start = float(bench[start_column].loc[index[a]])
        end = float(bench[CLOSE].loc[index[b]])
    except KeyError:
        return None
    if not start or pd.isna(start) or pd.isna(end):
        return None
    return end / start - 1.0


@dataclass(frozen=True)
class EventSet:
    """イベントの集合と、それがどう作られたかの記録。

    除外の内訳を持ち回るのは、件数が想定と違ったときに「どこで落ちたか」を
    推測せずに済ませるためである。前回の検証では、この内訳が無いまま件数の
    ずれを何度も推測して外した。
    """

    events: list[Event]
    period: Period
    benchmark: str | None
    """使ったベンチマークの銘柄コード。``None`` なら素のリターン。"""
    symbols_scanned: int = 0
    excluded_not_earnings: int = 0
    excluded_no_time: int = 0
    excluded_no_window: int = 0
    excluded_thin: int = 0
    excluded_no_benchmark: int = 0
    excluded_not_annual: int = 0
    """``sue`` で並べ替えるときに、通期短信でないため落とした数。"""
    excluded_no_forecast: int = 0
    """通期短信だが、直前の短信に通期予想が無いため驚きを出せなかった数。"""

    @property
    def total(self) -> int:
        """イベント数。"""
        return len(self.events)

    @property
    def unique_days(self) -> int:
        """独立した反応日の数。日次クラスタの有効サンプルサイズ。"""
        return len({e.reaction_on for e in self.events})

    def frame(self) -> pd.DataFrame:
        """集計しやすい形に直す。"""
        return pd.DataFrame(
            [
                {
                    "symbol": e.symbol,
                    "reaction_on": e.reaction_on,
                    "month": e.month,
                    "intraday": e.intraday,
                    "surprise": e.surprise,
                    "forward": e.forward,
                    "forward_short": e.forward_short,
                    "turnover_20d": e.turnover_20d,
                    "same_day_count": e.same_day_count,
                    "market_forward": e.market_forward,
                }
                for e in self.events
            ]
        )


def build_events(
    database: Database,
    period: Period,
    symbols: list[str] | None = None,
    benchmark: str | None = None,
    min_turnover: float = MIN_TURNOVER,
    sort: str = SORT_REACTION,
) -> EventSet:
    """封印した定義どおりにイベントを組み立てる。

    Args:
        database: 価格と開示の保存先。
        period: IS / OOS / ALL。**既定を置かない。** 合否判定はOOSで一度だけ
            行うものなので、うっかり見てしまう経路を作らない。
        symbols: 対象銘柄。省略時は ``market="JP"`` の全銘柄。
        benchmark: ベンチマークの銘柄コード（TOPIX連動ETFなど）。``None`` なら
            素のリターンになる。主要指標は上位分位と下位分位の**差**なので
            ベンチマークは相殺されるが、驚きの並べ替えには効く。
        min_turnover: セクション2の流動性下限（円）。
        sort: 並べ替える変数。``SORT_REACTION`` なら R 当日の市場対比リターン
            （``docs/PREREG_PEAD_JP.md``）、``SORT_SUE`` なら通期実績と直前に
            公表済みだった会社予想との乖離（``docs/PREREG_SUE_JP.md``）。
            **これ以外は共通の経路を通る。** 別々に書くと、結果の違いが
            並べ替え方の違いなのか実装の違いなのか分離できなくなる。

    Returns:
        イベント集合。除外の内訳つき。
    """
    if sort not in (SORT_REACTION, SORT_SUE):
        raise ValueError(f"unknown sort: {sort!r}")
    # 局所インポート。forecast_revision がこのモジュールの定数と関数を使う
    # ので、上に置くと循環する。
    from stock_ai.backtest.forecast_revision import find_sue_events

    with database.session() as session:
        if symbols is None:
            symbols = [sym for sym, market in list_securities(session) if market == "JP"]
        price_repo = PriceRepository(session)
        statement_repo = FinancialStatementRepository(session)

        bench_frame: pd.DataFrame | None = None
        if benchmark is not None:
            bench_raw = price_repo.get_raw_prices(benchmark)
            if not bench_raw.empty:
                bench_frame = split_adjusted(bench_raw)

        events: list[Event] = []
        not_earnings = no_time = no_window = thin = no_bench = 0
        not_annual = no_forecast = 0

        for symbol in symbols:
            if symbol == benchmark:
                continue  # ベンチマーク自身はイベントにしない
            reports = statement_repo.get_reports(symbol, period=None)
            if not reports:
                continue
            raw = price_repo.get_raw_prices(symbol)
            if raw.empty:
                continue
            adjusted = split_adjusted(raw)
            index = adjusted.index
            # 売買代金は生値で測る。調整済み終値に実出来高を掛けると、分割前の
            # バーで売買代金を分割比率のぶん過小に見積もる。
            turnover = (raw[CLOSE] * raw[VOLUME]).rolling(TURNOVER_WINDOW).mean().shift(1)

            # 会社予想との乖離は、その銘柄の短信の並びを見ないと出せない。
            # 開示日で引けるようにしておく。予想はどれも開示日より前に公表
            # 済みの値なので、先読みにならない。
            sue_by_day: dict[dt.date, float] = {}
            if sort == SORT_SUE:
                sue_by_day = {
                    event.disclosed_on: event.surprise for event in find_sue_events(reports).events
                }

            for report in reports:
                if report.disclosed_on is None:
                    continue
                if not is_earnings(report.doc_type):
                    not_earnings += 1
                    continue
                if report.disclosed_at is None:
                    no_time += 1
                    continue
                position = reaction_position(index, report.disclosed_on, report.disclosed_at)
                if position is None:
                    no_time += 1
                    continue
                reaction_on = index[position].date()
                if not period.contains(reaction_on):
                    continue
                # 驚きは R-1 の終値から R の終値、リターンは R+1 の寄付きから
                # R+HOLDING_DAYS の終値。どちらも R より後の情報を使わない。
                exit_at = position + 1 + HOLDING_DAYS
                if position < 1 or exit_at >= len(index):
                    no_window += 1
                    continue

                floor = turnover.iloc[position]
                if pd.isna(floor) or floor < min_turnover:
                    thin += 1
                    continue

                if sort == SORT_SUE:
                    # 四半期では驚きを定義できない。会社予想は通期12ヶ月ぶん、
                    # 実績は期中累計なので、そのまま引くと季節性を測ることに
                    # なる（PREREG_SUE_JP.md セクション3-4）。
                    if str(report.period) != "FY":
                        not_annual += 1
                        continue
                    if report.disclosed_on not in sue_by_day:
                        no_forecast += 1
                        continue

                stock_surprise = (
                    float(adjusted[CLOSE].iloc[position])
                    / float(adjusted[CLOSE].iloc[position - 1])
                    - 1.0
                )
                entry = float(adjusted[OPEN].iloc[position + 1])
                if not entry or pd.isna(entry):
                    no_window += 1
                    continue
                stock_forward = float(adjusted[CLOSE].iloc[exit_at]) / entry - 1.0
                short_exit = position + 1 + SECONDARY_HOLDING_DAYS
                stock_short = float(adjusted[CLOSE].iloc[short_exit]) / entry - 1.0

                # 驚きは R-1 の終値起点、リターンは R+1 の寄付き起点。
                # ベンチマークも同じ足から測る。
                bench_surprise = _benchmark_return(bench_frame, index, position - 1, position)
                bench_forward = _benchmark_return(
                    bench_frame, index, position + 1, exit_at, start_column=OPEN
                )
                bench_short = _benchmark_return(
                    bench_frame, index, position + 1, short_exit, start_column=OPEN
                )
                if bench_frame is not None and None in (
                    bench_surprise,
                    bench_forward,
                    bench_short,
                ):
                    # 銘柄とベンチマークで営業日がずれている。ずらしたまま
                    # 引くと、ずれの分だけ超過リターンに偏りが乗る。
                    no_bench += 1
                    continue

                events.append(
                    Event(
                        symbol=symbol,
                        disclosed_on=report.disclosed_on,
                        reaction_on=reaction_on,
                        intraday=report.disclosed_at < session_close_on(report.disclosed_on),
                        surprise=(
                            sue_by_day[report.disclosed_on]
                            if sort == SORT_SUE
                            else _excess(stock_surprise, bench_surprise)
                        ),
                        forward=_excess(stock_forward, bench_forward),
                        forward_short=_excess(stock_short, bench_short),
                        turnover_20d=float(floor),
                        market_forward=bench_forward,
                    )
                )

    return EventSet(
        events=_with_same_day_counts(events),
        period=period,
        benchmark=benchmark,
        symbols_scanned=len(symbols),
        excluded_not_earnings=not_earnings,
        excluded_no_time=no_time,
        excluded_no_window=no_window,
        excluded_thin=thin,
        excluded_no_benchmark=no_bench,
        excluded_not_annual=not_annual,
        excluded_no_forecast=no_forecast,
    )


def _with_same_day_counts(events: list[Event]) -> list[Event]:
    """同じ反応日のイベント数を各イベントに書き込む（セクション3-4）。

    混雑度は**ユニバースで絞った後の**件数で測る。市場全体の発表社数では
    なく、実際に検証対象になっている会社の数を見るほうが、注意分散という
    機構の測り方として素直である。
    """
    from collections import Counter

    per_day = Counter(e.reaction_on for e in events)
    return [
        Event(
            symbol=e.symbol,
            disclosed_on=e.disclosed_on,
            reaction_on=e.reaction_on,
            intraday=e.intraday,
            surprise=e.surprise,
            forward=e.forward,
            forward_short=e.forward_short,
            turnover_20d=e.turnover_20d,
            same_day_count=per_day[e.reaction_on],
            market_forward=e.market_forward,
        )
        for e in events
    ]


@dataclass(frozen=True)
class Explanation:
    """1件のイベントの計算過程を、手計算と突き合わせられる形で並べたもの。

    事前登録セクション9の最後の項目「既知の3銘柄について、驚きと R+60
    リターンを手計算と突き合わせた」のためにある。**集計を眺めても、
    集計の作り方が間違っている場合には気付けない。** 使った日付と価格を
    全部出して、電卓で追えるようにする。
    """

    symbol: str
    disclosed_on: dt.date
    disclosed_at: dt.time | None
    session_close: dt.time
    intraday: bool
    reaction_on: dt.date
    prior_close: float
    reaction_close: float
    entry_on: dt.date
    entry_open: float
    exit_on: dt.date
    exit_close: float
    benchmark: str | None
    bench_prior_close: float | None
    bench_reaction_close: float | None
    bench_entry_open: float | None
    bench_exit_close: float | None
    period_label: str = ""
    """短信の期（Q1/Q2/Q3/FY）。SUE は FY だけが対象になる。"""
    forecast: float | None = None
    """直前の短信が出していた通期予想の純利益。**開示日より前に公表済み。**"""
    actual: float | None = None
    """通期短信が報告した純利益の実績。"""
    sue_surprise: float | None = None
    """``(実績 − 予想) / |予想|``。SUE 版が並べ替えに使う値そのもの。"""

    @property
    def stock_surprise(self) -> float:
        """R の値動き。"""
        return self.reaction_close / self.prior_close - 1.0

    @property
    def stock_forward(self) -> float:
        """R+1 の寄付きから R+60 の終値まで。"""
        return self.exit_close / self.entry_open - 1.0

    @property
    def bench_surprise(self) -> float | None:
        """同じ日のベンチマークの値動き。"""
        if self.bench_prior_close is None or self.bench_reaction_close is None:
            return None
        return self.bench_reaction_close / self.bench_prior_close - 1.0

    @property
    def bench_forward(self) -> float | None:
        """同じ窓のベンチマークのリターン。"""
        if self.bench_entry_open is None or self.bench_exit_close is None:
            return None
        return self.bench_exit_close / self.bench_entry_open - 1.0


def explain_events(
    database: Database,
    symbol: str,
    period: Period = Period.ALL,
    benchmark: str | None = None,
) -> list[Explanation]:
    """``symbol`` のイベントを、使った日付と価格ごと並べる。

    集計と同じ関数（:func:`reaction_position`）で反応日を決めるので、
    ここに出る日付が集計で使われた日付そのものである。別経路で計算し直すと、
    突き合わせたつもりで別のものを見ることになる。
    """
    out: list[Explanation] = []
    with database.session() as session:
        price_repo = PriceRepository(session)
        raw = price_repo.get_raw_prices(symbol)
        if raw.empty:
            return out
        adjusted = split_adjusted(raw)
        index = adjusted.index

        bench_frame: pd.DataFrame | None = None
        if benchmark is not None:
            bench_raw = price_repo.get_raw_prices(benchmark)
            if not bench_raw.empty:
                bench_frame = split_adjusted(bench_raw)

        def bench_at(column: str, position: int) -> float | None:
            if bench_frame is None:
                return None
            try:
                return float(bench_frame[column].loc[index[position]])
            except KeyError:
                return None

        # 局所インポート。forecast_revision がこのモジュールを使うので、
        # 上に置くと循環する。
        from stock_ai.backtest.forecast_revision import find_sue_events

        reports = FinancialStatementRepository(session).get_reports(symbol, period=None)
        sue_by_day = {e.disclosed_on: e for e in find_sue_events(reports).events}

        for report in reports:
            if report.disclosed_on is None or not is_earnings(report.doc_type):
                continue
            position = reaction_position(index, report.disclosed_on, report.disclosed_at)
            if position is None or position < 1:
                continue
            exit_at = position + 1 + HOLDING_DAYS
            if exit_at >= len(index):
                continue
            if not period.contains(index[position].date()):
                continue
            assert report.disclosed_at is not None  # reaction_position が None を返す
            out.append(
                Explanation(
                    symbol=symbol,
                    disclosed_on=report.disclosed_on,
                    disclosed_at=report.disclosed_at,
                    session_close=session_close_on(report.disclosed_on),
                    intraday=report.disclosed_at < session_close_on(report.disclosed_on),
                    reaction_on=index[position].date(),
                    prior_close=float(adjusted[CLOSE].iloc[position - 1]),
                    reaction_close=float(adjusted[CLOSE].iloc[position]),
                    entry_on=index[position + 1].date(),
                    entry_open=float(adjusted[OPEN].iloc[position + 1]),
                    exit_on=index[exit_at].date(),
                    exit_close=float(adjusted[CLOSE].iloc[exit_at]),
                    benchmark=benchmark,
                    bench_prior_close=bench_at(CLOSE, position - 1),
                    bench_reaction_close=bench_at(CLOSE, position),
                    bench_entry_open=bench_at(OPEN, position + 1),
                    bench_exit_close=bench_at(CLOSE, exit_at),
                    period_label=str(report.period),
                    forecast=(
                        sue_by_day[report.disclosed_on].forecast
                        if report.disclosed_on in sue_by_day
                        else None
                    ),
                    actual=(
                        sue_by_day[report.disclosed_on].actual
                        if report.disclosed_on in sue_by_day
                        else None
                    ),
                    sue_surprise=(
                        sue_by_day[report.disclosed_on].surprise
                        if report.disclosed_on in sue_by_day
                        else None
                    ),
                )
            )
    return out


@dataclass(frozen=True)
class Spread:
    """上位分位と下位分位の差、とその有意性。"""

    high: float
    """上位分位の平均超過リターン（コスト控除後、日次等加重）。"""
    low: float
    """下位分位の平均超過リターン（コスト控除後、日次等加重）。"""
    difference: float
    """``high - low``。**合否判定に使う数字はこれ**（セクション5）。

    **3つとも同じ日集合・同じ加重で出す。** セクション5は上位・下位を
    「差の内訳」と書いているので、内訳が本体に足し戻せなければ報告として
    成立しない。以前はここが揃っておらず、上位と下位がイベント等加重・全日、
    差が日次等加重・両分位が揃った日のみだった。列を引き算しても差にならず、
    表から結果を再構成できなかった。
    """
    t_statistic: float
    """日付クラスタ標準誤差による t 値（セクション7）。"""
    clusters: int
    """独立した反応日の数。クラスタ数が30を切ると t 値は信用できない。"""
    events: int
    days_without_both_legs: int = 0
    """上位と下位のどちらかしか出なかったため、差を取れなかった日の数。

    その日はロング・ショートを組めないので落とすのが正しい（セクション4の
    「同一日に複数イベントが出た場合は等金額で分散」に従う）。ただし落ちるのは
    発表の少ない日に偏るので、**残った日は混雑日寄りになる**。混雑度が結果に
    効く場合、この偏りは無視できない。数を出しておく。
    """

    @property
    def reliable(self) -> bool:
        """クラスタ数が、クラスタ頑健標準誤差の前提を満たしているか。

        30を切ると標準誤差が過小になり、t 値が大きく出すぎる。数字が出ること
        と、その数字を信じてよいことは別である。
        """
        return self.clusters >= 30


def assign_quantiles(frame: pd.DataFrame, quantiles: int = QUANTILES) -> pd.DataFrame:
    """暦月ごとに驚きの大きさで分位に切る（セクション3-3）。

    月内で切るのは、市場全体のボラティリティが時期で違うため。全期間を
    まとめて切ると、変動の大きい月が上位と下位を埋め尽くす。

    **その月のイベントだけで閉じている。** 将来の月の分布を使っていないので、
    先読みにならない（セクション9）。

    イベントが分位数に満たない月は落とす。5分位に3件しかない月を無理に
    分けても、上位も下位も1件ずつの「差」になり、平均が個別銘柄の事情に
    支配される。
    """
    if frame.empty:
        return frame.assign(quantile=pd.Series(dtype="Int64"))

    def cut(group: pd.DataFrame) -> pd.DataFrame:
        if len(group) < quantiles:
            return group.assign(quantile=pd.NA)
        ranked = group["surprise"].rank(method="first")
        labels = pd.qcut(ranked, quantiles, labels=False, duplicates="drop")
        return group.assign(quantile=pd.array(labels, dtype="Int64"))

    return (
        frame.groupby("month", group_keys=False)[frame.columns.tolist()]
        .apply(cut)
        .dropna(subset=["quantile"])
    )


def clustered_t(values: pd.Series, clusters: pd.Series) -> tuple[float, int]:
    """日付でクラスタリングした平均の t 値と、クラスタ数を返す。

    同じ日に出たイベントは同じ地合いを共有するので、独立とみなすと標準誤差が
    小さく出て、t 値が大きく出すぎる。日ごとの平均を1観測として扱う。

    Returns:
        ``(t値, クラスタ数)``。クラスタが2未満なら ``(nan, n)``。
    """
    per_day = values.groupby(clusters).mean()
    count = len(per_day)
    if count < 2:
        return float("nan"), count
    error = per_day.std(ddof=1) / (count**0.5)
    if not error or pd.isna(error):
        return float("nan"), count
    return float(per_day.mean() / error), count


def spread(
    frame: pd.DataFrame,
    column: str = "forward",
    quantiles: int = QUANTILES,
    one_way_cost: float = ONE_WAY_COST,
) -> Spread:
    """上位分位 − 下位分位の差を、コスト控除後で出す（セクション4・5）。

    コストは**両建てぶん**を引く。ロングもショートも往復するので、差には
    片道コストの4本ぶん（＝往復0.6%）が乗る。ロングだけを見るときの倍に
    なるので、ここを片側ぶんにすると差を過大に見積もる。
    """
    ranked = assign_quantiles(frame, quantiles)
    if ranked.empty:
        return Spread(float("nan"), float("nan"), float("nan"), float("nan"), 0, 0)

    top = int(ranked["quantile"].max())
    high = ranked[ranked["quantile"] == top]
    low = ranked[ranked["quantile"] == 0]

    round_trip = 2 * one_way_cost
    high_net = high[column] - round_trip
    low_net = low[column] + round_trip

    # 差は日ごとに取る。ロングとショートを別々に平均してから引くと、
    # 上位と下位でイベント数の違う日の重みがずれる。
    both = pd.DataFrame(
        {
            "high": high_net.groupby(high["reaction_on"]).mean(),
            "low": low_net.groupby(low["reaction_on"]).mean(),
        }
    )
    per_day = both.dropna()
    dropped = len(both) - len(per_day)
    if per_day.empty:
        return Spread(float("nan"), float("nan"), float("nan"), float("nan"), 0, 0, dropped)

    difference = per_day["high"] - per_day["low"]
    count = len(difference)
    error = difference.std(ddof=1) / (count**0.5) if count > 1 else float("nan")
    t_value = float(difference.mean() / error) if error and not pd.isna(error) else float("nan")

    # 上位・下位も、差と同じ日集合・同じ日次等加重で出す。イベント等加重に
    # すると、片側しか出ない日のイベントが上位（または下位）にだけ入り、
    # 内訳が本体に足し戻せなくなる。
    return Spread(
        high=float(per_day["high"].mean()),
        low=float(per_day["low"].mean()),
        difference=float(difference.mean()),
        t_statistic=t_value,
        clusters=count,
        events=len(high) + len(low),
        days_without_both_legs=dropped,
    )


def quantile_ladder(
    frame: pd.DataFrame, column: str = "forward", quantiles: int = QUANTILES
) -> pd.DataFrame:
    """分位ごとの平均超過リターンを、驚きの小さい順に並べる。

    **上位と下位の差だけを見ていても、それが本物かは分からない。** 差は2点
    しか使わないので、外れ値の多い分位が1つあるだけで動く。分位が驚きの順に
    単調に並んでいれば、2点の差よりずっと強い証拠になる。並んでいなければ、
    差が出ていても機構の説明が付かない。

    水準の偏りを読むためでもある。全分位が同じだけ沈んでいるなら、その水準は
    分位に依らない何か（ユニバースとベンチマークの組成差など）であり、差では
    相殺される。特定の分位だけが沈んでいるなら、それは差に効く。

    **加重が :func:`spread` と違う。** ここはイベント等加重で、全日を使い、
    コストを引かない。分位の形を見るためのものだからである。合否に使う差は
    日次等加重・両分位が揃った日のみ・コスト控除後なので、両端の値を引き算
    しても差にはならない。混同しないよう、表題に加重を書く。

    Returns:
        ``quantile``（0が最下位）、``mean``、``events``、``days`` の表。
    """
    ranked = assign_quantiles(frame, quantiles)
    if ranked.empty:
        return pd.DataFrame(columns=["quantile", "mean", "events", "days"])
    grouped = ranked.groupby("quantile", observed=True)
    return pd.DataFrame(
        {
            "quantile": list(grouped.groups),
            "mean": grouped[column].mean().to_numpy(),
            "events": grouped.size().to_numpy(),
            "days": grouped["reaction_on"].nunique().to_numpy(),
        }
    ).sort_values("quantile", ignore_index=True)


def crowding_split(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """混雑日と閑散日に二分する（セクション3-4）。

    境界は**その暦年の中央値**。全期間の固定値にすると、上場企業数と開示
    慣行が年で動くぶんが境界に乗り、古い年が一律に閑散日、新しい年が一律に
    混雑日になりかねない。

    Returns:
        ``(混雑日, 閑散日)``。中央値ちょうどの日は閑散日側に入れる。
    """
    if frame.empty:
        return frame, frame
    year = pd.to_datetime(frame["reaction_on"]).dt.year
    threshold = frame.groupby(year)["same_day_count"].transform("median")
    busy = frame["same_day_count"] > threshold
    return frame[busy], frame[~busy]
