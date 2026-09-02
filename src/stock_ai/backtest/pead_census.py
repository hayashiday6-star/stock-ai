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
- **開示が場中か引け後か。** 引け後開示なら当日の値動きにニュースは入って
  おらず反応は翌日から始まる。場中なら当日に動く。取り違えると、反応そのものを
  ドリフトとして数えるか、逆に反応を取り逃がすかのどちらかになる

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

#: 東証の後場が終わる時刻。これ以降の開示は、その日の値動きに入っていない。
#:
#: 2024-11-05 に 15:00 から 15:30 へ延長されたが、ここでは**遅いほう**を使わない。
#: 15:10 の開示を「引け後」と誤って扱うと、実際には当日に織り込まれた反応を
#: ドリフトとして数えてしまう。境界に載る開示は少数なので、保守側に倒して
#: 15:00 以降を引け後とみなし、延長後の 15:00-15:30 は別枠で数える。
SESSION_CLOSE = dt.time(15, 0)

#: 立会時間の延長日。これ以降は 15:30 が引け。
SESSION_EXTENDED_FROM = dt.date(2024, 11, 5)
EXTENDED_CLOSE = dt.time(15, 30)

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
    disclosed_at: dt.time | None = None
    """開示時刻。``None`` は「取り込んでいない」であって「無い」ではない。"""

    def timing(self) -> str:
        """場中 / 引け後 / 判定不能 のどれか。

        延長後（2024-11-05 以降）の 15:00-15:30 は、当日中の開示ではあるが
        残り時間が短い。まとめてしまうと「引け後」の中に当日反応を持つものが
        混ざるので、別枠で数える。
        """
        if self.disclosed_at is None:
            return "時刻なし"
        if self.disclosed_at < SESSION_CLOSE:
            return "場中"
        if self.disclosed_on >= SESSION_EXTENDED_FROM and self.disclosed_at < EXTENDED_CLOSE:
            return "延長後の場中（15:00-15:30）"
        return "引け後"

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

    def timing_counts(self, disclosures: list[Disclosure] | None = None) -> Counter[str]:
        """場中 / 引け後 の内訳。PEAD のエントリー日はこれで決まる。"""
        rows = self.disclosures if disclosures is None else disclosures
        return Counter(d.timing() for d in rows)

    def slots_per_fiscal_year(self, disclosures: list[Disclosure] | None = None) -> Counter[int]:
        """1銘柄・1会計年度あたり、開示が何件あるかの分布。

        DBは ``(銘柄, 会計年度, 四半期)`` を一意キーにしているので、**1銘柄・
        1会計年度に4件までしか入らない**。日本の上場企業は四半期ごとに短信を
        出すので、揃っていれば4のはずである。

        3が並ぶなら、四半期が1つ落ちているか、同じ期の再開示が先のものを
        上書きしている。2が多いなら、そもそも四半期開示をしない銘柄
        （REITなど）が混ざっている。**この分布を見るまでは、どちらとも
        言えない。** 年別の件数だけでは「1銘柄あたり3件」の理由が分からず、
        原因を取り違えたまま事前登録を書くことになる。
        """
        rows = self.disclosures if disclosures is None else disclosures
        filled: Counter[tuple[str, int]] = Counter((d.symbol, d.fiscal_year) for d in rows)
        return Counter(filled.values())

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
                        disclosed_at=report.disclosed_at,
                    )
                )

    return CensusReport(
        disclosures=disclosures,
        symbols_scanned=len(symbols),
        symbols_without_prices=without_prices,
        rows_total=rows_total,
        rows_without_disclosed_on=rows_without_date,
    )
