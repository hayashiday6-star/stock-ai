"""連続する短信の予想比較で、予想修正を検出できるか。

修正開示は別文書として取れない（実測で fins/summary の99.2%が決算短信で、
予想修正は上位に1件も無い）。短信に毎回載る通期予想を前回と突き合わせるのが
唯一の経路なので、その突き合わせ方を固定する。
"""

from __future__ import annotations

import datetime as dt

import pytest

from stock_ai.backtest.forecast_revision import (
    census_revisions,
    census_sue,
    find_revisions,
    find_sue_events,
)
from stock_ai.data.types import FinancialReport
from stock_ai.database.engine import Database
from stock_ai.database.repository import FinancialStatementRepository


def _report(
    period: str,
    disclosed_on: dt.date,
    forecast: float | None,
    fiscal_year_end: dt.date | None = dt.date(2024, 3, 31),
    symbol: str = "7203",
    net_income: float | None = None,
) -> FinancialReport:
    return FinancialReport(
        symbol=symbol,
        fiscal_year=fiscal_year_end.year if fiscal_year_end else disclosed_on.year,
        period=period,
        disclosed_on=disclosed_on,
        disclosed_at=dt.time(15, 30),
        doc_type=f"{period}FinancialStatements_Consolidated_JP",
        fiscal_year_end=fiscal_year_end,
        forecast_net_income=forecast,
        net_income=net_income,
    )


def test_an_upward_revision_between_consecutive_statements_is_found() -> None:
    found = find_revisions(
        [
            _report("Q1", dt.date(2023, 8, 3), 1_000.0),
            _report("Q2", dt.date(2023, 11, 2), 1_300.0),
        ]
    )
    revisions = found.revisions

    assert found.compared == 1
    assert found.missing == 0
    assert found.unchanged == 0
    assert len(revisions) == 1
    assert revisions[0].upward
    assert revisions[0].change == pytest.approx(0.30)
    # イベント日は「変わったほうの短信」の開示日。
    assert revisions[0].disclosed_on == dt.date(2023, 11, 2)


def test_a_downward_revision_is_found_and_marked() -> None:
    revisions = find_revisions(
        [
            _report("Q2", dt.date(2023, 11, 2), 1_000.0),
            _report("Q3", dt.date(2024, 2, 2), 700.0),
        ]
    ).revisions

    assert len(revisions) == 1
    assert not revisions[0].upward
    assert revisions[0].change == pytest.approx(-0.30)


def test_a_small_move_is_not_a_revision() -> None:
    """端数や丸めで1円動いただけのものを修正として数えない。"""
    found = find_revisions(
        [
            _report("Q1", dt.date(2023, 8, 3), 1_000.0),
            _report("Q2", dt.date(2023, 11, 2), 1_020.0),  # +2%
        ]
    )

    assert found.compared == 1
    assert found.unchanged == 1
    assert found.revisions == []


def test_forecasts_are_not_compared_across_fiscal_years() -> None:
    """通期予想は当期のもの。翌期の予想と比べても修正ではない。"""
    found = find_revisions(
        [
            _report("FY", dt.date(2024, 5, 10), 1_000.0, dt.date(2024, 3, 31)),
            _report("Q1", dt.date(2024, 8, 2), 2_000.0, dt.date(2025, 3, 31)),
        ]
    )

    assert found.compared == 0  # 期末日が違うので同じ組にならない
    assert found.revisions == []


def test_a_missing_forecast_is_counted_not_treated_as_unchanged() -> None:
    """取り込みが古いと全組がここに落ちる。据え置きと混ぜると気付けない。"""
    found = find_revisions(
        [
            _report("Q1", dt.date(2023, 8, 3), None),
            _report("Q2", dt.date(2023, 11, 2), 1_300.0),
        ]
    )

    assert found.compared == 1
    assert found.missing == 1
    assert found.unchanged == 0
    assert found.revisions == []
    # 前側だけが欠けている。SUE は前側の予想が要るので、この組は使えない。
    assert found.missing_previous_only == 1
    assert found.missing_current_only == 0
    assert found.missing_by_transition == {"Q1->Q2": 1}


def test_a_report_without_a_fiscal_year_end_is_skipped() -> None:
    """どの期の予想か決まらないものを束ねると、別の期を比べてしまう。"""
    found = find_revisions(
        [
            _report("Q1", dt.date(2023, 8, 3), 1_000.0, None),
            _report("Q2", dt.date(2023, 11, 2), 1_300.0, None),
        ]
    )

    assert found.compared == 0
    assert found.revisions == []


def test_statements_are_ordered_by_quarter_not_by_arrival() -> None:
    """並びが崩れていても、Q1→Q2→Q3→FY の順で比べる。"""
    found = find_revisions(
        [
            _report("FY", dt.date(2024, 5, 10), 1_500.0),
            _report("Q1", dt.date(2023, 8, 3), 1_000.0),
            _report("Q3", dt.date(2024, 2, 2), 1_400.0),
            _report("Q2", dt.date(2023, 11, 2), 1_300.0),
        ]
    )
    revisions = found.revisions

    assert found.compared == 3
    # 1,000 → 1,300 → 1,400 → 1,500。いずれも5%以上なので3件とも修正。
    assert [r.from_period for r in revisions] == ["Q1", "Q2", "Q3"]
    assert [r.to_period for r in revisions] == ["Q2", "Q3", "FY"]


def test_the_census_reaches_the_database() -> None:
    database = Database("sqlite:///:memory:")
    database.create_all()
    with database.session() as session:
        FinancialStatementRepository(session).upsert_reports(
            "7203",
            [
                _report("Q1", dt.date(2023, 8, 3), 1_000.0),
                _report("Q2", dt.date(2023, 11, 2), 1_300.0),
            ],
            market="JP",
        )

    report = census_revisions(database)

    assert report.total == 1
    assert report.by_year() == [(2023, 1, 1, 0)]


def test_a_missing_forecast_on_the_later_statement_still_allows_sue() -> None:
    """SUE は前回の予想と今回の実績を比べる。後ろ側が空でも計算できる。

    修正検出は両側が要るので、成否が分かれる。同じ「比較できず」に丸めると、
    SUE が使える件数を実際より少なく見積もる。
    """
    found = find_revisions(
        [
            _report("Q3", dt.date(2024, 2, 2), 1_000.0),
            _report("FY", dt.date(2024, 5, 10), None),
        ]
    )

    assert found.missing == 1
    assert found.missing_current_only == 1
    assert found.missing_previous_only == 0
    assert found.missing_by_transition == {"Q3->FY": 1}


def test_the_census_separates_which_side_is_missing() -> None:
    database = Database("sqlite:///:memory:")
    database.create_all()
    with database.session() as session:
        FinancialStatementRepository(session).upsert_reports(
            "7203",
            [
                _report("Q3", dt.date(2024, 2, 2), 1_000.0),
                _report("FY", dt.date(2024, 5, 10), None),
            ],
            market="JP",
        )

    report = census_revisions(database)

    assert report.pairs_compared == 1
    assert report.missing_current_only == 1
    # 前側に予想があるので SUE は計算できる。
    assert report.usable_for_sue == 1


def test_the_census_counts_independent_days() -> None:
    database = Database("sqlite:///:memory:")
    database.create_all()
    with database.session() as session:
        repo = FinancialStatementRepository(session)
        for symbol in ("7203", "6758"):
            repo.upsert_reports(
                symbol,
                [
                    _report("Q1", dt.date(2023, 8, 3), 1_000.0, symbol=symbol),
                    _report("Q2", dt.date(2023, 11, 2), 1_300.0, symbol=symbol),
                ],
                market="JP",
            )

    report = census_revisions(database)

    assert report.total == 2
    assert report.unique_days == 1  # 同じ日に2社


# --- SUE ---------------------------------------------------------------------
#
# 通期短信の実績と、その時点で公表済みだった通期予想を組にする。四半期では
# 組めない（予想は12ヶ月ぶん、実績は期中累計）ので、通期だけを見る。


def test_a_full_year_statement_is_paired_with_the_standing_forecast() -> None:
    found = find_sue_events(
        [
            _report("Q3", dt.date(2024, 2, 2), 1_000.0),
            _report("FY", dt.date(2024, 5, 10), None, net_income=1_200.0),
        ]
    )

    assert found.fy_statements == 1
    assert len(found.events) == 1
    event = found.events[0]
    assert event.forecast == pytest.approx(1_000.0)
    assert event.actual == pytest.approx(1_200.0)
    assert event.surprise == pytest.approx(0.20)
    assert event.forecast_from_period == "Q3"
    # イベント日は通期短信の開示日。予想はそれより前に公開済みである。
    assert event.disclosed_on == dt.date(2024, 5, 10)


def test_quarterly_statements_are_never_turned_into_events() -> None:
    # 予想も実績も揃っているが、四半期なので組まない。Q1の実績3ヶ月ぶんを
    # 通期予想12ヶ月ぶんから引くと、驚きではなく季節性を測ることになる。
    found = find_sue_events(
        [
            _report("Q1", dt.date(2023, 8, 3), 1_000.0, net_income=200.0),
            _report("Q2", dt.date(2023, 11, 2), 1_000.0, net_income=500.0),
        ]
    )

    assert found.fy_statements == 0
    assert found.events == []


def test_an_older_forecast_is_used_when_the_latest_statement_has_none() -> None:
    # Q3が予想を出していなくても、Q2の予想は公開済みである。使ってよい。
    found = find_sue_events(
        [
            _report("Q2", dt.date(2023, 11, 2), 900.0),
            _report("Q3", dt.date(2024, 2, 2), None),
            _report("FY", dt.date(2024, 5, 10), None, net_income=1_100.0),
        ]
    )

    assert len(found.events) == 1
    assert found.events[0].forecast_from_period == "Q2"


def test_a_full_year_statement_without_any_prior_forecast_is_counted_not_dropped() -> None:
    found = find_sue_events([_report("FY", dt.date(2024, 5, 10), None, net_income=1_100.0)])

    assert found.fy_statements == 1
    assert found.without_prior_forecast == 1
    assert found.events == []


def test_a_forecast_from_a_different_fiscal_year_is_not_used() -> None:
    # 前期の通期予想は当期の実績の予想ではない。期末日で束ねているので混ざらない。
    found = find_sue_events(
        [
            _report("Q3", dt.date(2023, 2, 2), 800.0, fiscal_year_end=dt.date(2023, 3, 31)),
            _report(
                "FY",
                dt.date(2024, 5, 10),
                None,
                fiscal_year_end=dt.date(2024, 3, 31),
                net_income=1_100.0,
            ),
        ]
    )

    assert found.without_prior_forecast == 1
    assert found.events == []


def test_the_sue_census_counts_independent_days_not_just_events() -> None:
    # 通期短信は5月に集中する。件数ではなく日数が実質的なサンプルサイズになる。
    database = Database("sqlite:///:memory:")
    database.create_all()
    with database.session() as session:
        repo = FinancialStatementRepository(session)
        for symbol, actual in (("7203", 1_200.0), ("6758", 900.0)):
            repo.upsert_reports(
                symbol,
                [
                    _report("Q3", dt.date(2024, 2, 2), 1_000.0, symbol=symbol),
                    _report("FY", dt.date(2024, 5, 10), None, symbol=symbol, net_income=actual),
                ],
                market="JP",
            )

    report = census_sue(database)

    assert report.total == 2
    assert report.unique_days == 1
    assert report.by_year() == [(2024, 2, 1)]
    assert report.forecast_sources() == [("Q3", 2)]


def test_the_census_flags_how_many_surprises_are_effectively_zero() -> None:
    # 会社が着地を見てから予想を出し直すと、実績が予想にぴたりと寄る。
    # 分位に分けても中央が潰れていないかを、リターンを見る前に知りたい。
    database = Database("sqlite:///:memory:")
    database.create_all()
    with database.session() as session:
        repo = FinancialStatementRepository(session)
        for symbol, actual in (("7203", 1_002.0), ("6758", 1_500.0)):
            repo.upsert_reports(
                symbol,
                [
                    _report("Q3", dt.date(2024, 2, 2), 1_000.0, symbol=symbol),
                    _report("FY", dt.date(2024, 5, 10), None, symbol=symbol, net_income=actual),
                ],
                market="JP",
            )

    report = census_sue(database)

    assert report.near_zero == 1  # 7203 は +0.2%
    assert [name for name, _ in report.surprise_quantiles()] == [
        "p5",
        "p20",
        "p40",
        "p50",
        "p60",
        "p80",
        "p95",
    ]
