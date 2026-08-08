"""Tests for the fiscal-period statement series: parsing, storage, growth rules."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator

import pytest

from stock_ai.data.jquants_fundamentals import (
    JQuantsFundamentalsProvider,
    normalize_statements,
)
from stock_ai.data.types import FinancialReport, FiscalPeriod, Fundamentals
from stock_ai.database.engine import Database
from stock_ai.database.repository import (
    FinancialStatementRepository,
    FundamentalsRepository,
)
from stock_ai.fundamental.growth import (
    cagr,
    consecutive_dividend_increases,
    consecutive_dividend_non_cuts,
    dividend_growth,
    latest_payout_ratio,
    profit_growth,
    revenue_growth,
)
from stock_ai.screening.base import All
from stock_ai.screening.conditions import (
    MaxMarketCap,
    MaxPayoutRatio,
    MaxPER,
    MinConsecutiveDividendIncreases,
    MinDividendGrowth,
    MinProfitGrowth,
    MinRevenueGrowth,
)
from stock_ai.screening.engine import ScreeningEngine


@pytest.fixture
def database() -> Iterator[Database]:
    db = Database("sqlite:///:memory:")
    db.create_all()
    yield db
    db.dispose()


def _report(
    year: int,
    *,
    revenue: float | None = None,
    net_income: float | None = None,
    dps: float | None = None,
    eps: float | None = None,
    symbol: str = "X",
    period: FiscalPeriod = FiscalPeriod.FY,
) -> FinancialReport:
    return FinancialReport(
        symbol=symbol,
        fiscal_year=year,
        period=period,
        revenue=revenue,
        net_income=net_income,
        dividend_per_share=dps,
        eps=eps,
    )


# --- parsing ----------------------------------------------------------------


def test_every_disclosed_period_is_kept_not_just_the_latest() -> None:
    """The payload already carries the history; collapsing it loses the series."""
    records = [
        {"DiscDate": "2022-05-13", "FY": "2022", "Period": "FY", "Sales": "1000"},
        {"DiscDate": "2023-05-12", "FY": "2023", "Period": "FY", "Sales": "1200"},
        {"DiscDate": "2024-05-10", "FY": "2024", "Period": "FY", "Sales": "1500"},
    ]
    reports = normalize_statements("4593.T", records)
    assert [r.fiscal_year for r in reports] == [2022, 2023, 2024]
    assert [r.revenue for r in reports] == [1000.0, 1200.0, 1500.0]


def test_raw_figures_survive_normalization() -> None:
    """Ratios alone are not enough — streaks and payouts need the raw numbers."""
    records = [
        {
            "DiscDate": "2024-05-10",
            "FY": "2024",
            "Period": "FY",
            "Sales": "1500",
            "OP": "200",
            "NP": "130",
            "Eq": "980",
            "EPS": "130",
            "BPS": "980",
            "DivAnn": "30",
            "ShOutFY": "1000",
        }
    ]
    (report,) = normalize_statements("X", records)
    assert report.revenue == 1500.0
    assert report.operating_income == 200.0
    assert report.net_income == 130.0
    assert report.equity == 980.0
    assert report.eps == 130.0
    assert report.bps == 980.0
    assert report.dividend_per_share == 30.0
    assert report.shares_outstanding == 1000.0
    assert report.disclosed_on == dt.date(2024, 5, 10)


def test_a_restatement_supersedes_the_earlier_disclosure() -> None:
    records = [
        {"DiscDate": "2024-05-10", "FY": "2024", "Period": "FY", "Sales": "1500"},
        {"DiscDate": "2024-06-20", "FY": "2024", "Period": "FY", "Sales": "1490"},
    ]
    (report,) = normalize_statements("X", records)
    assert report.revenue == 1490.0


def test_quarters_are_labelled_and_ordered_chronologically() -> None:
    records = [
        {"DiscDate": "2024-11-05", "FY": "2025", "Period": "3Q", "Sales": "1200"},
        {"DiscDate": "2024-08-05", "FY": "2025", "Period": "1Q", "Sales": "400"},
        {"DiscDate": "2025-05-10", "FY": "2025", "Period": "FY", "Sales": "1600"},
    ]
    reports = normalize_statements("X", records)
    assert [r.period for r in reports] == [FiscalPeriod.Q1, FiscalPeriod.Q3, FiscalPeriod.FY]


def test_a_record_with_no_resolvable_fiscal_year_is_dropped() -> None:
    """Filing it under the wrong year would corrupt every YoY comparison."""
    assert normalize_statements("X", [{"Sales": "100"}]) == []


def test_fiscal_year_falls_back_to_the_year_end_date() -> None:
    records = [{"DiscDate": "2024-05-10", "FYEnd": "2024-03-31", "Sales": "100"}]
    (report,) = normalize_statements("X", records)
    assert report.fiscal_year == 2024


def test_provider_exposes_the_whole_history() -> None:
    records = [
        {"DiscDate": "2023-05-12", "FY": "2023", "Period": "FY", "Sales": "1200"},
        {"DiscDate": "2024-05-10", "FY": "2024", "Period": "FY", "Sales": "1500"},
    ]
    provider = JQuantsFundamentalsProvider(fetcher=lambda _s: records)
    assert [r.fiscal_year for r in provider.fetch_statements("X")] == [2023, 2024]


# --- persistence ------------------------------------------------------------


def test_statements_round_trip_and_reingest_idempotently(database: Database) -> None:
    reports = [_report(y, revenue=float(y)) for y in (2022, 2023, 2024)]

    with database.session() as session:
        assert FinancialStatementRepository(session).upsert_reports("X", reports) == 3
    with database.session() as session:  # re-ingest the same history
        FinancialStatementRepository(session).upsert_reports("X", reports)

    with database.session() as session:
        stored = FinancialStatementRepository(session).get_reports("X")
    assert [r.fiscal_year for r in stored] == [2022, 2023, 2024]


def test_get_reports_defaults_to_annual_only(database: Database) -> None:
    """A YoY series must not silently absorb quarterly rows."""
    reports = [
        _report(2024, revenue=1500.0),
        _report(2025, revenue=400.0, period=FiscalPeriod.Q1),
    ]
    with database.session() as session:
        FinancialStatementRepository(session).upsert_reports("X", reports)

    with database.session() as session:
        repo = FinancialStatementRepository(session)
        assert [r.fiscal_year for r in repo.get_reports("X")] == [2024]
        assert len(repo.get_reports("X", period=None)) == 2
        assert repo.latest_fiscal_year("X") == 2024


def test_latest_fiscal_year_is_none_without_statements(database: Database) -> None:
    with database.session() as session:
        assert FinancialStatementRepository(session).latest_fiscal_year("X") is None


# --- growth metrics ---------------------------------------------------------


_SERIES = [
    _report(2020, revenue=1000, net_income=80, dps=10, eps=80),
    _report(2021, revenue=1100, net_income=90, dps=12, eps=90),
    _report(2022, revenue=1200, net_income=100, dps=14, eps=100),
    _report(2023, revenue=1400, net_income=120, dps=16, eps=120),
    _report(2024, revenue=1500, net_income=130, dps=20, eps=130),
]


def test_year_over_year_growth_matches_hand_calculation() -> None:
    assert revenue_growth(_SERIES) == pytest.approx(1500 / 1400 - 1)
    assert profit_growth(_SERIES) == pytest.approx(130 / 120 - 1)
    assert dividend_growth(_SERIES) == pytest.approx(20 / 16 - 1)


def test_multi_year_window_looks_further_back() -> None:
    assert revenue_growth(_SERIES, periods=4) == pytest.approx(1500 / 1000 - 1)


def test_cagr_matches_hand_calculation() -> None:
    assert cagr(_SERIES, "revenue", 4) == pytest.approx((1500 / 1000) ** 0.25 - 1)


def test_growth_needs_enough_history() -> None:
    assert revenue_growth([_report(2024, revenue=1500)]) is None
    assert cagr(_SERIES, "revenue", 10) is None


def test_growth_from_a_loss_making_base_is_not_a_percentage() -> None:
    """Loss to profit is not "-300% growth"; it has no meaningful rate."""
    series = [_report(2023, net_income=-50), _report(2024, net_income=100)]
    assert profit_growth(series) is None


def test_dividend_increase_streak_counts_consecutive_raises() -> None:
    assert consecutive_dividend_increases(_SERIES) == 4


def test_a_cut_ends_the_streak() -> None:
    series = [
        _report(2020, dps=10),
        _report(2021, dps=12),
        _report(2022, dps=11),  # cut
        _report(2023, dps=16),
        _report(2024, dps=20),
    ]
    assert consecutive_dividend_increases(series) == 2


def test_a_flat_year_ends_an_increase_streak_but_not_a_non_cut_streak() -> None:
    series = [_report(2022, dps=14), _report(2023, dps=14), _report(2024, dps=14)]
    assert consecutive_dividend_increases(series) == 0
    assert consecutive_dividend_non_cuts(series) == 2


def test_a_missing_year_ends_the_streak_rather_than_counting_through() -> None:
    series = [_report(2022, dps=10), _report(2023), _report(2024, dps=20)]
    assert consecutive_dividend_increases(series) == 0


def test_payout_ratio_uses_the_latest_usable_report() -> None:
    assert latest_payout_ratio(_SERIES) == pytest.approx(20 / 130)


def test_payout_ratio_is_unknown_when_earnings_are_negative() -> None:
    assert _report(2024, dps=10, eps=-5).payout_ratio is None
    assert latest_payout_ratio([_report(2024, dps=10, eps=-5)]) is None


# --- screening conditions ---------------------------------------------------


def _seed(
    database: Database,
    symbol: str,
    rows: list[tuple[int, float, float, float, float]],
    per: float = 10.0,
    market_cap: float = 5e10,
) -> None:
    reports = [
        _report(y, revenue=rev, net_income=ni, dps=d, eps=eps, symbol=symbol)
        for y, rev, ni, d, eps in rows
    ]
    with database.session() as session:
        FinancialStatementRepository(session).upsert_reports(symbol, reports, market="JP")
        FundamentalsRepository(session).upsert_fundamentals(
            Fundamentals(symbol=symbol, as_of=dt.date(2024, 6, 30), per=per, market_cap=market_cap),
            market="JP",
        )


_GROWING = [
    (2021, 1000, 80, 10, 80),
    (2022, 1100, 90, 12, 90),
    (2023, 1250, 105, 14, 105),
    (2024, 1400, 125, 17, 125),
]
_FLAT_DIVIDEND = [
    (2021, 1000, 80, 10, 80),
    (2022, 1100, 90, 10, 90),
    (2023, 1250, 105, 10, 105),
    (2024, 1400, 125, 10, 125),
]
_SHRINKING = [
    (2021, 1400, 125, 17, 125),
    (2022, 1250, 105, 14, 105),
    (2023, 1100, 90, 12, 90),
    (2024, 1000, 80, 10, 80),
]


def test_value_growth_screen_selects_only_the_intended_name(database: Database) -> None:
    """増収・増益・増配・割安 — the composite the screener exists for."""
    _seed(database, "GOOD", _GROWING, per=12.0)
    _seed(database, "PRICEY", _GROWING, per=60.0)
    _seed(database, "SHRINK", _SHRINKING, per=8.0)
    _seed(database, "NODIV", _FLAT_DIVIDEND, per=10.0)

    condition = All(
        MinRevenueGrowth(0.05),
        MinProfitGrowth(0.05),
        MinDividendGrowth(0.001),  # a real raise, not merely "not cut"
        MaxPER(20.0),
    )
    assert ScreeningEngine(database, load_statements=True).screen(condition) == ["GOOD"]


def test_zero_dividend_growth_threshold_means_not_cut(database: Database) -> None:
    """``>= 0`` admits a held dividend; require a positive floor for a raise."""
    _seed(database, "GOOD", _GROWING)
    _seed(database, "NODIV", _FLAT_DIVIDEND)
    engine = ScreeningEngine(database, load_statements=True)
    assert sorted(engine.screen(MinDividendGrowth(0.0))) == ["GOOD", "NODIV"]


def test_growth_conditions_fail_closed_without_loaded_statements(database: Database) -> None:
    """An unloaded series is unknown, and unknown never passes."""
    _seed(database, "GOOD", _GROWING)
    assert ScreeningEngine(database, load_statements=False).screen(MinRevenueGrowth(0.05)) == []


def test_dividend_streak_and_payout_conditions(database: Database) -> None:
    _seed(database, "GOOD", _GROWING)
    _seed(database, "NODIV", _FLAT_DIVIDEND)
    engine = ScreeningEngine(database, load_statements=True)

    assert engine.screen(MinConsecutiveDividendIncreases(3)) == ["GOOD"]
    assert sorted(engine.screen(MaxPayoutRatio(0.30))) == ["GOOD", "NODIV"]


def test_unknown_payout_ratio_does_not_pass(database: Database) -> None:
    _seed(database, "LOSS", [(2023, 1000, -50, 10, -50), (2024, 1100, -40, 10, -40)])
    assert ScreeningEngine(database, load_statements=True).screen(MaxPayoutRatio(0.9)) == []


def test_max_market_cap_selects_small_caps(database: Database) -> None:
    _seed(database, "BIG", _GROWING, market_cap=5e11)
    _seed(database, "SMALL", _GROWING, market_cap=3e9)
    assert ScreeningEngine(database).screen(MaxMarketCap(1e10)) == ["SMALL"]


# --- the period marker decides whether growth is real -----------------------


def test_the_v2_period_field_is_read() -> None:
    """V2 calls it ``CurPerType``; the older spellings do not appear in it.

    Missing it files every quarterly row as annual. ``Sales`` and ``NP`` are
    cumulative from the start of the fiscal year, so a 3Q row holds nine months,
    and comparing that against a twelve-month row invents growth.
    """
    from stock_ai.data.jquants_fundamentals import normalize_statements
    from stock_ai.data.types import FiscalPeriod

    records = [
        {"CurPerType": "FY", "FYEnd": "2024-03-31", "DiscDate": "2024-05-10", "Sales": 1000},
        {"CurPerType": "1Q", "FYEnd": "2025-03-31", "DiscDate": "2024-08-05", "Sales": 260},
        {"CurPerType": "2Q", "FYEnd": "2025-03-31", "DiscDate": "2024-11-05", "Sales": 530},
        {"CurPerType": "3Q", "FYEnd": "2025-03-31", "DiscDate": "2025-02-05", "Sales": 800},
        {"CurPerType": "FY", "FYEnd": "2025-03-31", "DiscDate": "2025-05-12", "Sales": 1030},
    ]
    reports = normalize_statements("7203", records)

    periods = [report.period for report in reports]
    assert periods.count(FiscalPeriod.FY) == 2
    assert FiscalPeriod.Q1 in periods
    assert FiscalPeriod.Q3 in periods


def test_growth_uses_only_the_annual_rows() -> None:
    """1030 / 1000 - 1 = 3%, not 1030 / 800 - 1 = 29%."""
    from stock_ai.data.jquants_fundamentals import normalize_statements
    from stock_ai.data.types import FiscalPeriod
    from stock_ai.fundamental.growth import revenue_growth

    records = [
        {"CurPerType": "FY", "FYEnd": "2024-03-31", "DiscDate": "2024-05-10", "Sales": 1000},
        {"CurPerType": "3Q", "FYEnd": "2025-03-31", "DiscDate": "2025-02-05", "Sales": 800},
        {"CurPerType": "FY", "FYEnd": "2025-03-31", "DiscDate": "2025-05-12", "Sales": 1030},
    ]
    annual = [r for r in normalize_statements("7203", records) if r.period is FiscalPeriod.FY]

    assert revenue_growth(annual) == pytest.approx(0.03)


def test_a_payload_with_no_period_marker_is_reported(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Every row defaulting to annual is the signature of a renamed field.

    It corrupts silently, so it has to be loud - this is the third field-rename
    in this API that showed up as wrong numbers rather than an error.
    """
    import logging

    from stock_ai.data.jquants_fundamentals import normalize_statements

    records = [
        {"FYEnd": "2024-03-31", "DiscDate": "2024-05-10", "Sales": 1000},
        {"FYEnd": "2025-03-31", "DiscDate": "2025-05-12", "Sales": 1030},
    ]
    with caplog.at_level(logging.WARNING):
        normalize_statements("7203", records)

    assert "no period marker" in caplog.text
    assert "CurPerType" in caplog.text
    assert any(record.levelno >= logging.ERROR for record in caplog.records)


def test_a_marked_payload_logs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    """The warning must not fire on healthy data, or it trains people to ignore it."""
    import logging

    from stock_ai.data.jquants_fundamentals import normalize_statements

    records = [
        {"CurPerType": "FY", "FYEnd": "2024-03-31", "DiscDate": "2024-05-10", "Sales": 1000},
    ]
    with caplog.at_level(logging.WARNING):
        normalize_statements("7203", records)

    assert "no period marker" not in caplog.text
