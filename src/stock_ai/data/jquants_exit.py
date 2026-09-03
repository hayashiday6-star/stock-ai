"""解約で失われるものが、いま手元にどれだけあるかを数える。

**期限は 2026-09-22。** J-Quants の有料プランはそこで切れる。切れてから
「これが無いと動かない」と分かるのが、いちばん困る形になる。

この棚卸しが答えるのは1つだけである。**「解約後に作り直せないもののうち、
まだ手元に無いものはどれか」。** 作り直せるものは急がない。立花から取れる
株価も、EDINET から取れる有報も、あとで何度でも取り直せる。

作り直せないものは3つに絞られる。

1. **日付ごとの上場名簿。** `equities/master` の ``date`` 付き。立花のマスタは
   現存銘柄しか返さない
2. **上場廃止銘柄の株価。** 立花にはもう存在しない銘柄コードである
3. **会社の通期予想と開示時刻。** `fins/summary` の ``F*`` と ``DiscTime``。
   EDINET の有報は実績のみで、どちらも持たない

そのうえで、**5年ローリング窓が効く**ことを忘れないこと。いま取れるのは
2021-09 以降だけで、これは解約を待たずに毎日**後ろから消えていく**。

数えるだけで、取りには行かない。取得は `delisted-harvest` と `bulk-fetch` の
役目である。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import func, select

from stock_ai.core.logging import get_logger
from stock_ai.database.engine import Database
from stock_ai.database.models import FinancialStatement, PriceBar, Security

logger = get_logger(__name__)

#: 有料プランが切れる日。
CANCELLATION = dt.date(2026, 9, 22)


@dataclass(frozen=True)
class Coverage:
    """いま手元にあるものの量。**判断は含まない。数えた値だけ。**"""

    securities: int
    symbols_with_prices: int
    price_first: dt.date | None
    price_last: dt.date | None
    symbols_with_statements: int
    statements: int
    with_disclosed_at: int
    """開示時刻が入っている行。決算ドリフトの反応日を決める唯一の材料。"""
    with_forecast: int
    """会社予想が1つでも入っている行。予想修正・SUE の唯一の材料。"""
    statement_first: dt.date | None
    statement_last: dt.date | None
    snapshots: int
    """保存済みの名簿の数。"""
    snapshot_first: dt.date | None
    snapshot_last: dt.date | None
    roster_symbols: int
    """名簿に一度でも出た銘柄。"""
    roster_without_prices: int
    """そのうち株価が手元に無いもの。**ここが生存バイアスの残り。**"""

    def days_left(self, today: dt.date | None = None) -> int:
        """解約日まで何日あるか。負なら過ぎている。"""
        return (CANCELLATION - (today or dt.date.today())).days


def audit(database: Database, snapshots: dict[dt.date, set[str]] | None = None) -> Coverage:
    """手元にあるものを数える。取りには行かない。

    Args:
        database: 数える対象。
        snapshots: 保存済みの名簿。``None`` なら名簿の欄は 0 になる。
    """
    with database.session() as session:
        securities = (
            session.execute(
                select(func.count(Security.id)).where(Security.market == "JP")
            ).scalar_one()
            or 0
        )
        priced = session.execute(
            select(
                func.count(func.distinct(PriceBar.security_id)),
                func.min(PriceBar.date),
                func.max(PriceBar.date),
            )
            .select_from(PriceBar)
            .join(Security, Security.id == PriceBar.security_id)
            .where(Security.market == "JP")
        ).one()
        statements = session.execute(
            select(
                func.count(func.distinct(FinancialStatement.security_id)),
                func.count(FinancialStatement.id),
                func.count(FinancialStatement.disclosed_at),
                func.min(FinancialStatement.disclosed_on),
                func.max(FinancialStatement.disclosed_on),
            )
            .select_from(FinancialStatement)
            .join(Security, Security.id == FinancialStatement.security_id)
            .where(Security.market == "JP")
        ).one()
        # **予想は4列のどれか1つでも入っていれば「ある」。** 純利益だけ埋まって
        # 売上が空、という取れ方を実際にする。1列だけ数えると過小に出る。
        forecasts = (
            session.execute(
                select(func.count(FinancialStatement.id))
                .select_from(FinancialStatement)
                .join(Security, Security.id == FinancialStatement.security_id)
                .where(Security.market == "JP")
                .where(
                    FinancialStatement.forecast_revenue.is_not(None)
                    | FinancialStatement.forecast_operating_income.is_not(None)
                    | FinancialStatement.forecast_net_income.is_not(None)
                    | FinancialStatement.forecast_eps.is_not(None)
                )
            ).scalar_one()
            or 0
        )
        with_bars = {
            symbol
            for (symbol,) in session.execute(
                select(func.distinct(Security.symbol))
                .select_from(PriceBar)
                .join(Security, Security.id == PriceBar.security_id)
                .where(Security.market == "JP")
            )
        }

    union: set[str] = set()
    for codes in (snapshots or {}).values():
        union |= codes
    ordered = sorted(snapshots or {})

    coverage = Coverage(
        securities=securities,
        symbols_with_prices=priced[0] or 0,
        price_first=priced[1],
        price_last=priced[2],
        symbols_with_statements=statements[0] or 0,
        statements=statements[1] or 0,
        with_disclosed_at=statements[2] or 0,
        with_forecast=forecasts,
        statement_first=statements[3],
        statement_last=statements[4],
        snapshots=len(ordered),
        snapshot_first=ordered[0] if ordered else None,
        snapshot_last=ordered[-1] if ordered else None,
        roster_symbols=len(union),
        roster_without_prices=len(union - with_bars),
    )
    logger.info(
        "解約前の棚卸し: 株価 %d 銘柄、財務 %d 行、名簿 %d 件、名簿にあって株価が無い %d 銘柄",
        coverage.symbols_with_prices,
        coverage.statements,
        coverage.snapshots,
        coverage.roster_without_prices,
    )
    return coverage
