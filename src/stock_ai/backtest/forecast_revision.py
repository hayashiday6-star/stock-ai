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

from stock_ai.data.types import FinancialReport
from stock_ai.database.engine import Database
from stock_ai.database.repository import FinancialStatementRepository, list_securities

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

    @property
    def surprise(self) -> float:
        """予想からの乖離。予想を分母にする。"""
        return self.actual / self.forecast - 1.0


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


def find_sue_events(reports: list[FinancialReport], field: str = "net_income") -> _SueFound:
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
    annual = no_actual = no_forecast = 0

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
            events.append(
                SueEvent(
                    symbol=report.symbol,
                    fiscal_year_end=fiscal_year_end,
                    disclosed_on=report.disclosed_on,
                    forecast=forecast,
                    actual=float(actual),
                    forecast_from_period=str(prior.period),
                )
            )
    return _SueFound(events, annual, no_actual, no_forecast)


def census_sue(
    database: Database, symbols: list[str] | None = None, field: str = "net_income"
) -> SueReport:
    """SUE を計算できる通期短信を数える。**リターンは計算しない。**"""
    events: list[SueEvent] = []
    annual = no_actual = no_forecast = 0

    with database.session() as session:
        if symbols is None:
            symbols = [sym for sym, market in list_securities(session) if market == "JP"]
        repo = FinancialStatementRepository(session)
        for symbol in symbols:
            found = find_sue_events(repo.get_reports(symbol, period=None), field=field)
            events.extend(found.events)
            annual += found.fy_statements
            no_actual += found.without_actual
            no_forecast += found.without_prior_forecast

    return SueReport(
        events=events,
        symbols_scanned=len(symbols),
        fy_statements=annual,
        without_actual=no_actual,
        without_prior_forecast=no_forecast,
    )
