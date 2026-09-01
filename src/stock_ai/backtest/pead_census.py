"""PEAD（決算後ドリフト）を事前登録する前に、測れるかどうかを先に数える。

前回のアキュムレーション検証は、事前登録を書き上げてから実装し、最後に
件数を測って「検証不能」で終わった。原因は仮説ではなく、**現象が起きている
場所と、手元のデータが届く場所が交わっていなかった**ことである
（`docs/PREREG_ACCUMULATION_JP.md` セクション10）。

同じ順序を繰り返さない。このモジュールは事前登録を書く**前**に走らせて、
以下を事実として確定させる。リターンは一切計算しない。

- 開示イベントが年に何件あるか。銘柄数・ユニークな開示日数はいくつか
- **流動性で絞っても残るか。** 「年数千件ある」は市場についての主張であって、
  手元のDBについての主張ではない
- **リターン窓が実際に取れるか。** D+1の寄りと D+60 の終値が価格データに
  存在しない開示は、件数に数えても検証には使えない
- 同日発表社数の分布（注意分散仮説がそのまま測れるか）

数えた結果を見てから事前登録を書く。これは合否判定に使うバックテストでは
ないので、この順序は事前登録の原則に反しない。
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from dataclasses import dataclass

import pandas as pd

from stock_ai.data.schema import CLOSE, VOLUME
from stock_ai.database.engine import Database
from stock_ai.database.repository import (
    FinancialStatementRepository,
    PriceRepository,
    list_securities,
)

#: 売買代金を平均する営業日数。判定日 D 自身は含めない。
TURNOVER_WINDOW = 20

#: ドリフトを測る最長の保有期間（営業日）。仮説は20〜60営業日を挙げており、
#: 窓が取れるかは一番長い60で判定する。60が取れれば20も取れる。
DRIFT_WINDOW = 60

#: エントリーは D+1。開示当日の終値は使えない（引け後開示なら約定できない）。
ENTRY_OFFSET = 1

#: 流動性帯の区切り（円）。前回の事前登録が使った1億円を含む。どの帯なら
#: 件数が残るかを見るためのもので、閾値をここで決めるものではない。
TURNOVER_BANDS: tuple[float, ...] = (0.0, 20_000_000.0, 50_000_000.0, 100_000_000.0, 300_000_000.0)


@dataclass(frozen=True)
class Disclosure:
    """1件の開示イベントと、それをPEADで使えるかどうか。"""

    symbol: str
    disclosed_on: dt.date
    fiscal_year: int
    period: str
    turnover_20d: float | None
    """D を除く直近20営業日の平均売買代金。価格が足りなければ ``None``。"""
    has_entry_bar: bool
    """D+1 に価格がある（エントリーできる）。"""
    has_exit_bar: bool
    """D+60 に価格がある（決済できる）。"""

    @property
    def measurable(self) -> bool:
        """リターンを計算できる開示か。"""
        return self.has_entry_bar and self.has_exit_bar

    def band(self) -> float | None:
        """この開示が属する流動性帯の下限。"""
        if self.turnover_20d is None:
            return None
        chosen = TURNOVER_BANDS[0]
        for edge in TURNOVER_BANDS:
            if self.turnover_20d >= edge:
                chosen = edge
        return chosen


@dataclass(frozen=True)
class CensusReport:
    """DBにある開示イベントを、PEADの観点から数えた結果。"""

    disclosures: list[Disclosure]
    symbols_scanned: int
    symbols_without_prices: int
    rows_total: int
    rows_without_disclosed_on: int
    """開示日が入っていない行。これは「開示が無かった」ではなく
    「いつ開示されたか分からない」であり、PEADには使えない。"""

    def measurable(self) -> list[Disclosure]:
        """リターン窓が取れる開示だけ。"""
        return [d for d in self.disclosures if d.measurable]

    def by_year(
        self, disclosures: list[Disclosure] | None = None
    ) -> list[tuple[int, int, int, int]]:
        """年ごとの (年, 件数, 銘柄数, ユニーク開示日数)。"""
        rows = self.disclosures if disclosures is None else disclosures
        years = sorted({d.disclosed_on.year for d in rows})
        out = []
        for year in years:
            same = [d for d in rows if d.disclosed_on.year == year]
            out.append(
                (
                    year,
                    len(same),
                    len({d.symbol for d in same}),
                    len({d.disclosed_on for d in same}),
                )
            )
        return out

    def by_band(
        self, disclosures: list[Disclosure] | None = None
    ) -> list[tuple[float | None, int]]:
        """流動性帯ごとの件数。``None`` は売買代金が計算できなかった分。"""
        rows = self.disclosures if disclosures is None else disclosures
        counts = Counter(d.band() for d in rows)
        ordered: list[tuple[float | None, int]] = [
            (edge, counts.get(edge, 0)) for edge in TURNOVER_BANDS
        ]
        if counts.get(None):
            ordered.append((None, counts[None]))
        return ordered

    def same_day_counts(self, disclosures: list[Disclosure] | None = None) -> Counter[dt.date]:
        """開示日ごとの発表社数。注意分散仮説はこの分布の上でしか測れない。"""
        rows = self.disclosures if disclosures is None else disclosures
        return Counter(d.disclosed_on for d in rows)


def _turnover_before(raw: pd.DataFrame, when: pd.Timestamp) -> float | None:
    """``when`` を含まない直近20営業日の平均売買代金。"""
    prior = raw.loc[raw.index < when]
    if len(prior) < TURNOVER_WINDOW:
        return None
    window = prior.iloc[-TURNOVER_WINDOW:]
    value = float((window[CLOSE] * window[VOLUME]).mean())
    return None if pd.isna(value) else value


def _bar_exists(index: pd.DatetimeIndex, when: pd.Timestamp, offset: int) -> bool:
    """``when`` から ``offset`` 営業日先のバーが price データにあるか。

    営業日はその銘柄自身の価格インデックスから取る - 祝日表を別に持つと、
    表とデータがずれたときに黙って間違う。
    """
    # side="left" は「``when`` 以降の最初のバー」を指す。開示日が非営業日なら
    # その次の営業日が D になり、営業日ならその日自身が D になる。
    position = int(index.searchsorted(when, side="left"))
    if position >= len(index):
        return False
    return position + offset < len(index)


def run_census(database: Database, symbols: list[str] | None = None) -> CensusReport:
    """DBにある日本株の開示を、PEADで使えるかどうかの観点から数える。

    Args:
        database: 価格と開示の保存先。
        symbols: 対象銘柄。省略時は ``market="JP"`` の全銘柄。

    Returns:
        件数のみ。**リターンは計算しない。**
    """
    disclosures: list[Disclosure] = []
    rows_total = 0
    rows_without_date = 0
    without_prices = 0

    with database.session() as session:
        if symbols is None:
            symbols = [sym for sym, market in list_securities(session) if market == "JP"]
        price_repo = PriceRepository(session)
        statement_repo = FinancialStatementRepository(session)

        for symbol in symbols:
            reports = statement_repo.get_reports(symbol, period=None)
            rows_total += len(reports)
            dated = [r for r in reports if r.disclosed_on is not None]
            rows_without_date += len(reports) - len(dated)
            if not dated:
                continue

            # 売買代金は生値で計算する。調整済み終値に実出来高を掛けると、
            # 分割前のバーで売買代金を分割比率のぶん過小に見積もる。
            raw = price_repo.get_raw_prices(symbol)
            if raw.empty:
                without_prices += 1
                continue

            for report in dated:
                assert report.disclosed_on is not None  # dated で絞り込み済み
                when = pd.Timestamp(report.disclosed_on)
                disclosures.append(
                    Disclosure(
                        symbol=symbol,
                        disclosed_on=report.disclosed_on,
                        fiscal_year=report.fiscal_year,
                        period=str(report.period),
                        turnover_20d=_turnover_before(raw, when),
                        has_entry_bar=_bar_exists(raw.index, when, ENTRY_OFFSET),
                        has_exit_bar=_bar_exists(raw.index, when, ENTRY_OFFSET + DRIFT_WINDOW),
                    )
                )

    return CensusReport(
        disclosures=disclosures,
        symbols_scanned=len(symbols),
        symbols_without_prices=without_prices,
        rows_total=rows_total,
        rows_without_disclosed_on=rows_without_date,
    )
