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
from dataclasses import dataclass

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

    @property
    def total(self) -> int:
        """検出できた修正の数。"""
        return len(self.revisions)

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
) -> tuple[list[Revision], int, int, int]:
    """1銘柄の短信列から、同じ会計年度内の予想修正を拾う。

    **会計年度をまたいだ比較はしない。** 通期予想は当期のものなので、
    翌期の予想と比べても「修正」ではなく別の期の話になる。期末日で束ねる。

    Returns:
        ``(修正, 比較した組数, 予想が無くて比較できなかった組数, 据え置きの組数)``
    """
    by_year: dict[dt.date | None, list[FinancialReport]] = {}
    for report in reports:
        if report.disclosed_on is None:
            continue
        by_year.setdefault(report.fiscal_year_end, []).append(report)

    revisions: list[Revision] = []
    compared = missing = unchanged = 0

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
    return revisions, compared, missing, unchanged


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
        for symbol in symbols:
            found, pairs, gaps, same = find_revisions(
                repo.get_reports(symbol, period=None), field=field, min_change=min_change
            )
            revisions.extend(found)
            compared += pairs
            missing += gaps
            unchanged += same

    return RevisionReport(
        revisions=revisions,
        symbols_scanned=len(symbols),
        pairs_compared=compared,
        pairs_without_forecast=missing,
        pairs_unchanged=unchanged,
    )
