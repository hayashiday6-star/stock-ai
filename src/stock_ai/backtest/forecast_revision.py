"""連続する決算短信の会社予想を比べて、予想修正を検出する。

**修正開示は別文書としては取れない。** 実測（2026-09-02）で
``fins/summary`` の開示種類は99.2%が決算短信で、予想修正は上位12種類に
1件も現れなかった。したがって「上方修正が出た」を知る経路は、短信そのものに
毎回載る通期予想を、前回の短信と突き合わせることしかない。

同じ抽出が2つの用途に効く。

- **SUE**（実績と会社予想の差）。日本は会社が予想を出す制度なので、
  アナリスト予想が無くても驚きの大きさを定義できる
- **予想修正後のドリフト**（候補2）。修正そのものがイベントになる

このモジュールは件数を数えるだけで、リターンは計算しない。**候補2が成立する
かどうかは、ここで検出できる件数が決める。** 年に数百件しか出ないなら、
設計を先に見直す必要がある。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import pandas as pd

from stock_ai.backtest.pead import (
    HOLDING_DAYS,
    MIN_TURNOVER,
    QUANTILES,
    TURNOVER_WINDOW,
    is_earnings,
    reaction_position,
)
from stock_ai.data.schema import CLOSE, VOLUME
from stock_ai.data.types import FinancialReport
from stock_ai.database.engine import Database
from stock_ai.database.repository import (
    FinancialStatementRepository,
    PriceRepository,
    list_securities,
)

#: 予想が「変わった」とみなす最小の相対幅。
#:
#: 端数処理や単位の丸めで1円動いただけのものを修正として数えない。5%は
#: 東証の適時開示規則が定める業績予想修正の開示基準（売上10%、利益30%）より
#: 緩いので、**開示義務が生じない小さな修正も拾う**。拾いすぎたら分布を見て
#: から絞れるよう、閾値は引数にしてある。
DEFAULT_MIN_CHANGE = 0.05


@dataclass(frozen=True)
class Revision:
    """同じ会計年度について、前回の短信から予想が変わったこと。"""

    symbol: str
    fiscal_year_end: dt.date | None
    from_period: str
    to_period: str
    disclosed_on: dt.date
    """変わったほうの短信の開示日。イベント日はここ。"""
    previous: float
    current: float

    @property
    def change(self) -> float:
        """相対的な変化幅。前回予想を分母にする。"""
        return self.current / self.previous - 1.0

    @property
    def upward(self) -> bool:
        """上方修正か。"""
        return self.current > self.previous


@dataclass(frozen=True)
class RevisionReport:
    """検出できた修正と、検出できなかった理由の内訳。"""

    revisions: list[Revision]
    symbols_scanned: int
    pairs_compared: int
    """前後で比較できた短信の組の数。"""
    pairs_without_forecast: int
    """どちらかに予想が入っておらず比較できなかった組。

    **0でない場合、取り込みが古い可能性がある。** 予想フィールドは後から
    足した列なので、取り直していないDBでは全件がここに落ちる。
    """
    pairs_unchanged: int
    """予想が動かなかった組（据え置き）。"""
    missing_previous_only: int = 0
    """前の短信にだけ予想が無かった組。

    **SUE の成否はここで決まる。** SUE は「前回の予想」と「今回の実績」を
    比べるので、前側に予想があれば計算できる。後ろ側が空でも困らない。
    修正検出（両側が要る）と成否が分かれるので、分けて数える。
    """
    missing_current_only: int = 0
    """後の短信にだけ予想が無かった組。通期短信は当期が終わっているので、
    当期通期予想の欄が空になりうる。その形かどうかを見る。"""
    missing_both: int = 0
    """両方に予想が無かった組。"""
    missing_by_transition: dict[str, int] = field(default_factory=dict)
    """``"Q3->FY"`` のような期の遷移ごとの、比較できなかった組の数。

    特定の遷移に偏っていれば構造的な欠落（通期短信に当期予想が無い、など）で
    あり、散っていれば銘柄側の事情（予想を出さない会社）である。**推測で
    片付けないために分けて数える。**
    """

    @property
    def total(self) -> int:
        """検出できた修正の数。"""
        return len(self.revisions)

    @property
    def unique_days(self) -> int:
        """修正が起きた独立の開示日数。日次クラスタの有効サンプルサイズ。"""
        return len({r.disclosed_on for r in self.revisions})

    @property
    def usable_for_sue(self) -> int:
        """SUE を計算できる組の数。前側に予想があればよい。"""
        return self.pairs_compared - self.missing_previous_only - self.missing_both

    def by_year(self) -> list[tuple[int, int, int, int]]:
        """年ごとの (年, 修正数, 上方, 下方)。"""
        years = sorted({r.disclosed_on.year for r in self.revisions})
        out = []
        for year in years:
            same = [r for r in self.revisions if r.disclosed_on.year == year]
            up = sum(1 for r in same if r.upward)
            out.append((year, len(same), up, len(same) - up))
        return out


#: 会計年度内での短信の並び順。予想を比べるのは「同じ会計年度の連続する短信」。
_ORDER = {"Q1": 1, "Q2": 2, "Q3": 3, "FY": 4}


@dataclass(frozen=True)
class _Found:
    """:func:`find_revisions` の返り値。内訳を取りこぼさないための入れ物。"""

    revisions: list[Revision]
    compared: int
    missing: int
    unchanged: int
    missing_previous_only: int
    missing_current_only: int
    missing_both: int
    missing_by_transition: dict[str, int]


def _forecast_of(report: FinancialReport, field: str) -> float | None:
    """比較に使う予想値。0や負は分母にできないので落とす。"""
    value = getattr(report, f"forecast_{field}", None)
    if value is None or value <= 0:
        return None
    return float(value)


def find_revisions(
    reports: list[FinancialReport],
    field: str = "net_income",
    min_change: float = DEFAULT_MIN_CHANGE,
) -> _Found:
    """1銘柄の短信列から、同じ会計年度内の予想修正を拾う。

    **会計年度をまたいだ比較はしない。** 通期予想は当期のものなので、
    翌期の予想と比べても「修正」ではなく別の期の話になる。期末日で束ねる。

    Returns:
        :class:`_Found`。件数の内訳つき。
    """
    by_year: dict[dt.date | None, list[FinancialReport]] = {}
    for report in reports:
        if report.disclosed_on is None:
            continue
        by_year.setdefault(report.fiscal_year_end, []).append(report)

    revisions: list[Revision] = []
    compared = missing = unchanged = 0
    only_previous = only_current = neither = 0
    by_transition: dict[str, int] = {}

    for fiscal_year_end, group in by_year.items():
        if fiscal_year_end is None:
            # 期末日が無いと、どの期の予想かが決まらない。同じ年度として
            # 束ねると別の期の予想を比べてしまう。
            continue
        ordered = sorted(
            group, key=lambda r: (_ORDER.get(str(r.period), 9), r.disclosed_on or dt.date.min)
        )
        for previous, current in zip(ordered, ordered[1:], strict=False):
            compared += 1
            before = _forecast_of(previous, field)
            after = _forecast_of(current, field)
            if before is None or after is None:
                missing += 1
                if before is None and after is None:
                    neither += 1
                elif before is None:
                    only_previous += 1
                else:
                    only_current += 1
                key = f"{previous.period}->{current.period}"
                by_transition[key] = by_transition.get(key, 0) + 1
                continue
            if abs(after / before - 1.0) < min_change:
                unchanged += 1
                continue
            assert current.disclosed_on is not None  # 上で None を除外済み
            revisions.append(
                Revision(
                    symbol=current.symbol,
                    fiscal_year_end=fiscal_year_end,
                    from_period=str(previous.period),
                    to_period=str(current.period),
                    disclosed_on=current.disclosed_on,
                    previous=before,
                    current=after,
                )
            )
    return _Found(
        revisions, compared, missing, unchanged, only_previous, only_current, neither, by_transition
    )


def census_revisions(
    database: Database,
    symbols: list[str] | None = None,
    field: str = "net_income",
    min_change: float = DEFAULT_MIN_CHANGE,
) -> RevisionReport:
    """全銘柄で予想修正を数える。**候補2が成立するかはこの件数が決める。**"""
    revisions: list[Revision] = []
    compared = missing = unchanged = 0

    with database.session() as session:
        if symbols is None:
            symbols = [sym for sym, market in list_securities(session) if market == "JP"]
        repo = FinancialStatementRepository(session)
        only_previous = only_current = neither = 0
        by_transition: dict[str, int] = {}
        for symbol in symbols:
            found = find_revisions(
                repo.get_reports(symbol, period=None), field=field, min_change=min_change
            )
            revisions.extend(found.revisions)
            compared += found.compared
            missing += found.missing
            unchanged += found.unchanged
            only_previous += found.missing_previous_only
            only_current += found.missing_current_only
            neither += found.missing_both
            for key, count in found.missing_by_transition.items():
                by_transition[key] = by_transition.get(key, 0) + count

    return RevisionReport(
        revisions=revisions,
        symbols_scanned=len(symbols),
        pairs_compared=compared,
        pairs_without_forecast=missing,
        pairs_unchanged=unchanged,
        missing_previous_only=only_previous,
        missing_current_only=only_current,
        missing_both=neither,
        missing_by_transition=by_transition,
    )


# --- SUE（実績と会社予想の差）--------------------------------------------------


@dataclass(frozen=True)
class SueEvent:
    """通期短信1件。実績と、その直前に公表されていた通期予想の組。"""

    symbol: str
    fiscal_year_end: dt.date
    disclosed_on: dt.date
    forecast: float
    """直前の短信が出していた通期予想。**開示日より前に公開済みの値である。**"""
    actual: float
    """通期短信が報告した実績。この日の新情報。"""
    forecast_from_period: str
    """予想を出した短信の期。通常は Q3。"""
    disclosed_at: dt.time | None = None
    """開示時刻。**無ければ反応日を決められないので、イベントにならない。**

    日本の開示は8割が引け後なので、時刻を取り違えると8割のイベントが
    1日ずれる。分からない行は落とす。
    """
    turnover_20d: float | None = None
    """反応日 R の直前20営業日の平均売買代金。流動性フィルタが見る値。

    ``None`` は「時刻が無い」「バーが足りない」など、そもそも測れなかった
    ことを意味する。薄いのではない。
    """
    has_window: bool = False
    """R-1 の終値と、R+1 の寄付きから R+保有日数 の終値までが揃っているか。"""
    is_earnings_doc: bool = False
    """開示種類が決算短信だと確認できたか。種類が分からない行は通さない。"""
    market_cap: float | None = None
    """開示日の**前営業日**の終値 × その時点の発行済株式数。

    前営業日にするのは、開示当日の終値だと場中開示の反応が混ざるためである。
    分割調整前の終値と、その時点の株数を掛ける。両方が同じ株数基準なので
    分割をまたいでも時価総額は連続する。
    """

    @property
    def surprise(self) -> float:
        """予想からの乖離。予想を分母にする。

        **分母が小さい会社で発散する。** 予想利益が薄いほど比が大きく出るので、
        分位の端が薄利の会社に偏る。順位でしか使わないので裾の値そのものは
        効かないが、**選ばれる顔ぶれが変わる**。
        """
        return self.actual / self.forecast - 1.0

    @property
    def scaled_surprise(self) -> float | None:
        """時価総額に対する驚きの大きさ。時価総額が取れなければ None。

        市場が織り込むのは株価に対する金額であって、予想利益に対する比では
        ない。予想1億が2億になる（+100%）のと1000億が1100億になる（+10%）の
        とでは、時価総額比では後者が大きいこともある。
        """
        if not self.market_cap:
            return None
        return (self.actual - self.forecast) / self.market_cap


@dataclass(frozen=True)
class SueReport:
    """SUE を計算できるイベントの件数。**リターンは計算しない。**"""

    events: list[SueEvent]
    symbols_scanned: int
    fy_statements: int
    """通期短信の総数。"""
    without_actual: int
    """実績が入っていない通期短信。"""
    without_prior_forecast: int
    """直前の短信に通期予想が無かった通期短信。"""
    without_market_cap: int = 0
    """イベントにはなったが時価総額を出せなかった数。

    **0でなければ、時価総額で正規化する定義は全件では使えない。**
    株数が入っていないか、開示日より前の終値が無いかのどちらかである。
    """

    @property
    def total(self) -> int:
        """SUE を計算できたイベント数。"""
        return len(self.events)

    @property
    def unique_days(self) -> int:
        """独立した開示日数。日次クラスタの有効サンプルサイズ。

        通期短信は5月に極端に集中するので、**イベント数のわりに日数が
        少なくなる**。差の検定はこの日数で効くので、件数だけを見て
        サンプルが足りると判断してはいけない。
        """
        return len({e.disclosed_on for e in self.events})

    @property
    def scaled_available(self) -> int:
        """時価総額比の驚きを計算できたイベント数。"""
        return sum(1 for e in self.events if e.scaled_surprise is not None)

    def scaled_quantiles(self) -> list[tuple[str, float]]:
        """時価総額比の驚きの分布。単位は bp（時価総額の万分の一）。"""
        values = sorted(
            e.scaled_surprise * 10_000 for e in self.events if e.scaled_surprise is not None
        )
        if not values:
            return []

        def at(fraction: float) -> float:
            return values[min(len(values) - 1, int(fraction * len(values)))]

        return [(f"p{int(f * 100)}", at(f)) for f in (0.05, 0.2, 0.4, 0.5, 0.6, 0.8, 0.95)]

    def rank_correlation(self) -> float | None:
        """2つの定義の順位相関（スピアマン）。

        1に近ければどちらで並べても同じ顔ぶれが選ばれるので、議論する必要が
        無い。低ければ**どちらを封印するかが結果を変える**ので、理屈で
        決めておく必要がある。
        """
        pairs = [(e.surprise, e.scaled_surprise) for e in self.events if e.scaled_surprise]
        if len(pairs) < 3:
            return None
        frame = pd.DataFrame(pairs, columns=["relative", "scaled"])
        return float(frame["relative"].rank().corr(frame["scaled"].rank()))

    def size_profile(self) -> list[tuple[str, float, float, float]]:
        """各定義で5分位に切ったときの、下位・中位・上位分位の時価総額中央値（億円）。

        **相対変化率が薄利の会社に偏るかを直接測る。** 端の分位だけ時価総額が
        小さければ、その定義は驚きの大きさではなく会社の小ささを並べている。
        """
        usable = [e for e in self.events if e.scaled_surprise is not None and e.market_cap]
        if len(usable) < 25:
            return []
        out = []
        for name, key in (
            ("相対変化率", lambda e: e.surprise),
            ("時価総額比", lambda e: e.scaled_surprise),
        ):
            frame = pd.DataFrame(
                {"value": [key(e) for e in usable], "cap": [e.market_cap for e in usable]}
            )
            bucket = pd.qcut(frame["value"].rank(method="first"), 5, labels=False)
            median = frame.groupby(bucket)["cap"].median() / 1e8
            out.append((name, float(median.iloc[0]), float(median.iloc[2]), float(median.iloc[4])))
        return out

    @property
    def near_zero(self) -> int:
        """驚きが±1%未満だったイベント数。

        **多すぎると SUE で並べ替えられない。** 日本の会社は着地が見えた
        時点で予想を出し直すので、実績が予想にぴたりと寄る。分位に分けても
        上位と下位が同じものになっていないかを、リターンを見る前に確かめる。
        """
        return sum(1 for e in self.events if abs(e.surprise) < 0.01)

    def surprise_quantiles(self) -> list[tuple[str, float]]:
        """驚きの分布。分位の境目が潰れていないかを見る。"""
        if not self.events:
            return []
        values = sorted(e.surprise for e in self.events)

        def at(fraction: float) -> float:
            return values[min(len(values) - 1, int(fraction * len(values)))]

        return [(f"p{int(f * 100)}", at(f)) for f in (0.05, 0.2, 0.4, 0.5, 0.6, 0.8, 0.95)]

    def forecast_sources(self) -> list[tuple[str, int]]:
        """予想を出した短信の期ごとの件数。通常は Q3 が大半になるはず。"""
        counts: dict[str, int] = {}
        for event in self.events:
            counts[event.forecast_from_period] = counts.get(event.forecast_from_period, 0) + 1
        return sorted(counts.items(), key=lambda kv: -kv[1])

    def admitted(self, min_turnover: float = MIN_TURNOVER) -> list[SueEvent]:
        """封印する手順を全部通ったイベント。**実際に測れるのはこれだけ。**"""
        return [
            e
            for e in self.events
            if e.is_earnings_doc
            and e.disclosed_at is not None
            and e.has_window
            and e.turnover_20d is not None
            and e.turnover_20d >= min_turnover
        ]

    def admission_ladder(self, min_turnover: float = MIN_TURNOVER) -> list[tuple[str, int, int]]:
        """段階ごとの (残った件数, 独立開示日数)。

        **アキュムレーションの事前登録は、封印してから流動性フィルタが
        11,014件を279件にしていたと分かって中止になった。** 同じ失い方を
        繰り返さないために、封印する前にここで数える。落ちるのが分かって
        いれば、事前登録に本当の母数を書ける。
        """
        stages: list[tuple[str, list[SueEvent]]] = []
        step = self.events
        stages.append(("SUE を組めた", step))
        step = [e for e in step if e.is_earnings_doc]
        stages.append(("短信だと確認できた", step))
        step = [e for e in step if e.disclosed_at is not None]
        stages.append(("開示時刻がある", step))
        step = [e for e in step if e.has_window]
        stages.append(("前後のバーが揃う", step))
        step = [e for e in step if e.turnover_20d is not None and e.turnover_20d >= min_turnover]
        stages.append((f"売買代金 {min_turnover / 1e8:.0f}億円以上", step))
        return [(name, len(group), len({e.disclosed_on for e in group})) for name, group in stages]

    def admitted_by_year(self, min_turnover: float = MIN_TURNOVER) -> list[tuple[int, int, int]]:
        """通った後の、年ごとの (年, イベント数, 独立開示日数)。"""
        events = self.admitted(min_turnover)
        years = sorted({e.disclosed_on.year for e in events})
        return [
            (
                year,
                len([e for e in events if e.disclosed_on.year == year]),
                len({e.disclosed_on for e in events if e.disclosed_on.year == year}),
            )
            for year in years
        ]

    def admitted_size_profile(
        self, min_turnover: float = MIN_TURNOVER
    ) -> list[tuple[str, float, float, float]]:
        """通った後で月次5分位に切ったときの、端と中央の時価総額中央値（億円）。

        **分位は流動性フィルタの後に切る。** 封印する手順がそうなっているため
        で、実際に売買できる銘柄の中での上位・下位を見ることになる。全銘柄で
        切ってから絞ると、端の分位だけが削られて中身が変わる。
        """
        usable = [e for e in self.admitted(min_turnover) if e.market_cap]
        if len(usable) < 5 * QUANTILES:
            return []
        base = pd.DataFrame(
            {
                "month": [pd.Timestamp(e.disclosed_on).to_period("M") for e in usable],
                "cap": [e.market_cap for e in usable],
                "relative": [e.surprise for e in usable],
                "scaled": [e.scaled_surprise for e in usable],
            }
        )
        out = []
        for name, column in (("相対変化率", "relative"), ("時価総額比", "scaled")):
            frame = base.dropna(subset=[column]).copy()
            frame["bucket"] = frame.groupby("month")[column].transform(
                lambda values: (
                    pd.qcut(values.rank(method="first"), QUANTILES, labels=False, duplicates="drop")
                    if len(values) >= QUANTILES
                    else pd.NA
                )
            )
            frame = frame.dropna(subset=["bucket"])
            if frame.empty:
                continue
            median = frame.groupby("bucket")["cap"].median() / 1e8
            out.append(
                (
                    name,
                    float(median.iloc[0]),
                    float(median.iloc[len(median) // 2]),
                    float(median.iloc[-1]),
                )
            )
        return out

    def by_year(self) -> list[tuple[int, int, int]]:
        """年ごとの (年, イベント数, 独立開示日数)。"""
        years = sorted({e.disclosed_on.year for e in self.events})
        return [
            (
                year,
                len([e for e in self.events if e.disclosed_on.year == year]),
                len({e.disclosed_on for e in self.events if e.disclosed_on.year == year}),
            )
            for year in years
        ]


@dataclass(frozen=True)
class _SueFound:
    """:func:`find_sue_events` の返り値。"""

    events: list[SueEvent]
    fy_statements: int
    without_actual: int
    without_prior_forecast: int
    without_market_cap: int = 0


def _market_cap_before(
    closes: pd.Series | None, report: FinancialReport, disclosed_on: dt.date
) -> float | None:
    """開示日の前営業日の終値 × その時点の発行済株式数。

    **当日の終値は使わない。** 場中開示なら反応が既に混ざっており、引け後開示
    でも当日の値動きは開示への期待を含みうる。正規化に使う分母は、開示より
    前に確定していた値でなければならない。

    調整前の終値を使う。株数もその時点のものなので、分割をまたいでも
    「株価×株数」は連続する。調整済み終値と当時の株数を掛けると、分割の
    ぶんだけ時価総額を取り違える。
    """
    shares = report.shares_outstanding
    if closes is None or closes.empty or not shares or shares <= 0:
        return None
    position = int(closes.index.searchsorted(pd.Timestamp(disclosed_on), side="left"))
    if position == 0:
        return None
    price = closes.iloc[position - 1]
    if price is None or not float(price) > 0:
        return None
    return float(price) * float(shares)


def _admission(
    raw: pd.DataFrame | None, report: FinancialReport, disclosed_on: dt.date
) -> tuple[float | None, bool]:
    """封印する手順が反応日 R について見る2つを、そのまま計算する。

    **``pead`` と同じ関数・同じ定数を呼ぶ。** ここで独自に近いものを書くと、
    センサスが数えた件数と、実際に回したときの件数がずれる。ずれても
    例外は出ないので、気付けない。

    Returns:
        ``(R直前20営業日の平均売買代金, 前後のバーが揃っているか)``。
    """
    if raw is None or raw.empty:
        return None, False
    index = raw.index
    position = reaction_position(index, disclosed_on, report.disclosed_at)
    if position is None:
        return None, False
    # 売買代金は生値で測る。調整済み終値に実出来高を掛けると、分割前のバーで
    # 売買代金を分割比率のぶん過小に見積もる。
    turnover = (raw[CLOSE] * raw[VOLUME]).rolling(TURNOVER_WINDOW).mean().shift(1)
    exit_at = position + 1 + HOLDING_DAYS
    has_window = position >= 1 and exit_at < len(index)
    floor = turnover.iloc[position]
    return (None if pd.isna(floor) else float(floor)), has_window


def find_sue_events(
    reports: list[FinancialReport],
    field: str = "net_income",
    closes: pd.Series | None = None,
    raw: pd.DataFrame | None = None,
) -> _SueFound:
    """通期短信について、実績と直前の通期予想を組にする。

    **四半期では SUE を定義しない。** 日本の短信は通期予想と期中累計の実績を
    出すので、Q1時点では実績3ヶ月ぶんと予想12ヶ月ぶんになり、直接引き算
    できない。四半期でやるには「期待累計＝通期予想×季節配分」が要り、季節配分の
    推定という可動部が増える。校正用の物差しに可動部は持ち込まない。

    通期短信だけなら、実績も予想も同じ12ヶ月ぶんで、そのまま引ける。
    """
    by_year: dict[dt.date, list[FinancialReport]] = {}
    for report in reports:
        if report.disclosed_on is None or report.fiscal_year_end is None:
            continue
        by_year.setdefault(report.fiscal_year_end, []).append(report)

    events: list[SueEvent] = []
    annual = no_actual = no_forecast = no_cap = 0

    for fiscal_year_end, group in by_year.items():
        ordered = sorted(group, key=lambda r: (_ORDER.get(str(r.period), 9), r.disclosed_on))
        for index, report in enumerate(ordered):
            if str(report.period) != "FY":
                continue
            annual += 1
            actual = getattr(report, field, None)
            if actual is None:
                no_actual += 1
                continue
            # 直前の短信が出していた通期予想。無ければさらに前を見る -
            # Q3が予想を出していない会社でも、Q2の予想は公開済みである。
            prior = next(
                (
                    earlier
                    for earlier in reversed(ordered[:index])
                    if _forecast_of(earlier, field) is not None
                ),
                None,
            )
            if prior is None:
                no_forecast += 1
                continue
            forecast = _forecast_of(prior, field)
            assert forecast is not None  # 上の next() で絞り込み済み
            market_cap = _market_cap_before(closes, report, report.disclosed_on)
            if market_cap is None:
                no_cap += 1
            turnover, has_window = _admission(raw, report, report.disclosed_on)
            events.append(
                SueEvent(
                    symbol=report.symbol,
                    fiscal_year_end=fiscal_year_end,
                    disclosed_on=report.disclosed_on,
                    forecast=forecast,
                    actual=float(actual),
                    forecast_from_period=str(prior.period),
                    disclosed_at=report.disclosed_at,
                    turnover_20d=turnover,
                    has_window=has_window,
                    market_cap=market_cap,
                    is_earnings_doc=is_earnings(report.doc_type),
                )
            )
    return _SueFound(events, annual, no_actual, no_forecast, no_cap)


def census_sue(
    database: Database, symbols: list[str] | None = None, field: str = "net_income"
) -> SueReport:
    """SUE を計算できる通期短信を数える。**リターンは計算しない。**"""
    events: list[SueEvent] = []
    annual = no_actual = no_forecast = no_cap = 0

    with database.session() as session:
        if symbols is None:
            symbols = [sym for sym, market in list_securities(session) if market == "JP"]
        repo = FinancialStatementRepository(session)
        price_repo = PriceRepository(session)
        for symbol in symbols:
            # 調整前の終値。時価総額はその時点の株数と掛け合わせるので、
            # 調整済みの値を使うと分割のぶんだけ取り違える。
            raw = price_repo.get_raw_prices(symbol)
            closes = raw[CLOSE] if not raw.empty and CLOSE in raw else None
            found = find_sue_events(
                repo.get_reports(symbol, period=None), field=field, closes=closes, raw=raw
            )
            events.extend(found.events)
            annual += found.fy_statements
            no_actual += found.without_actual
            no_forecast += found.without_prior_forecast
            no_cap += found.without_market_cap

    return SueReport(
        events=events,
        symbols_scanned=len(symbols),
        fy_statements=annual,
        without_actual=no_actual,
        without_prior_forecast=no_forecast,
        without_market_cap=no_cap,
    )
