"""Tests for natural-language screening: parsing, guards, and execution."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator

import numpy as np
import pandas as pd
import pytest

from stock_ai.ai.query import ScreenQuery, build_query, parse_query, run_query
from stock_ai.core.exceptions import AIError
from stock_ai.data.sectors import Sector
from stock_ai.data.types import FinancialReport, Fundamentals, SecurityProfile
from stock_ai.database.engine import Database
from stock_ai.database.repository import (
    FinancialStatementRepository,
    FundamentalsRepository,
    PriceRepository,
    upsert_profile,
)


class _StubProvider:
    """An AI provider that returns a fixed reply."""

    name = "stub"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def complete(self, prompt: str, *, system: str | None = None, max_tokens: int = 1024) -> str:
        self.prompts.append(prompt)
        return self.reply


@pytest.fixture
def database() -> Iterator[Database]:
    db = Database("sqlite:///:memory:")
    db.create_all()
    yield db
    db.dispose()


def _seed(
    db: Database,
    symbol: str,
    market: str,
    sector: str,
    per: float,
    roe: float,
    statements: list[tuple[int, float, float]] | None = None,
) -> None:
    index = pd.date_range("2024-01-01", periods=30, freq="B", name="date")
    close = np.full(30, 100.0)
    frame = pd.DataFrame(
        {
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "adj_close": close,
            "volume": 1_000,
        },
        index=index,
    )
    with db.session() as session:
        PriceRepository(session).upsert_prices(symbol, frame, market=market)
        upsert_profile(session, SecurityProfile(symbol=symbol, market=market, sector=sector))
        FundamentalsRepository(session).upsert_fundamentals(
            Fundamentals(
                symbol=symbol, as_of=dt.date(2024, 6, 30), per=per, roe=roe, market_cap=1e10
            ),
            market=market,
        )
        if statements:
            FinancialStatementRepository(session).upsert_reports(
                symbol,
                [
                    FinancialReport(
                        symbol=symbol, fiscal_year=y, revenue=rev, net_income=ni, eps=ni
                    )
                    for y, rev, ni in statements
                ],
                market=market,
            )


# --- parsing ----------------------------------------------------------------


def test_criteria_become_a_condition_tree() -> None:
    query = build_query({"max_per": 15, "min_roe": 0.2, "sectors": ["Technology"]})
    assert query.sectors == [Sector.TECHNOLOGY]
    assert "PER <= 15.0" in query.describe()
    assert "ROE >= 0.2" in query.describe()


def test_a_percentage_the_model_forgot_to_convert_is_rescaled() -> None:
    """ "ROE 20%" comes back as 20 as often as 0.2; 2000% would match nothing."""
    assert build_query({"min_roe": 20}).describe() == "ROE >= 0.2"
    assert build_query({"min_roe": 0.2}).describe() == "ROE >= 0.2"


def test_non_ratio_fields_are_never_rescaled() -> None:
    assert "PER <= 15.0" in build_query({"max_per": 15}).describe()
    assert "MarketCap >= 1000000000.0" in build_query({"min_market_cap": 1e9}).describe()


def test_an_unsupported_field_is_refused_rather_than_ignored() -> None:
    """A hallucinated or hostile key must stop the query, not slip through."""
    with pytest.raises(AIError, match="unsupported criteria"):
        build_query({"exec": "rm -rf /", "max_per": 15})


def test_a_non_numeric_criterion_is_refused() -> None:
    with pytest.raises(AIError, match="must be a number"):
        build_query({"max_per": "cheap"})
    with pytest.raises(AIError, match="must be a number"):
        build_query({"max_per": True})


def test_unknown_sectors_are_dropped_but_known_ones_kept() -> None:
    query = build_query({"sectors": ["Technology", "Crypto Mining", "Financials"]})
    assert query.sectors == [Sector.TECHNOLOGY, Sector.FINANCIALS]


def test_unknown_markets_are_dropped() -> None:
    assert build_query({"markets": ["JP", "MARS", "us"]}).markets == ["JP", "US"]


def test_statement_backed_criteria_are_flagged() -> None:
    assert build_query({"max_per": 15}).needs_statements is False
    assert build_query({"min_revenue_growth": 0.1}).needs_statements is True
    assert build_query({"min_dividend_streak": 3}).needs_statements is True


def test_an_empty_payload_produces_an_empty_query() -> None:
    query = build_query({})
    assert query.is_empty
    assert query.describe() == "(no criteria)"


# --- reply handling ---------------------------------------------------------


def test_json_is_extracted_from_a_chatty_reply() -> None:
    provider = _StubProvider('Sure!\n```json\n{"max_per": 15}\n```\nHope that helps.')
    assert "PER <= 15.0" in parse_query(provider, "cheap stocks").describe()


def test_a_reply_with_no_json_is_an_error() -> None:
    with pytest.raises(AIError, match="no JSON object"):
        parse_query(_StubProvider("I cannot help with that."), "x")


def test_malformed_json_is_an_error() -> None:
    with pytest.raises(AIError, match="not valid JSON"):
        parse_query(_StubProvider('{"max_per": }'), "x")


def test_a_json_array_is_rejected() -> None:
    with pytest.raises(AIError):
        parse_query(_StubProvider("[1, 2, 3]"), "x")


def test_the_question_reaches_the_model_verbatim() -> None:
    provider = _StubProvider('{"max_per": 15}')
    parse_query(provider, "PER15以下の株")
    assert provider.prompts == ["PER15以下の株"]


# --- execution --------------------------------------------------------------


def test_sector_and_metric_filters_combine(database: Database) -> None:
    """The worked example: cheap, profitable, and in one sector."""
    _seed(database, "NVDA", "US", "Technology", per=12.0, roe=0.35)
    _seed(database, "INTC", "US", "Technology", per=40.0, roe=0.05)
    _seed(database, "JPM", "US", "Financials", per=11.0, roe=0.25)

    query = build_query({"max_per": 15, "min_roe": 0.2, "sectors": ["Technology"]})
    assert run_query(database, query) == ["NVDA"]


def test_market_restriction_narrows_the_universe(database: Database) -> None:
    _seed(database, "NVDA", "US", "Technology", per=12.0, roe=0.35)
    _seed(database, "6857.T", "JP", "Technology", per=13.0, roe=0.22)

    both = build_query({"max_per": 15, "min_roe": 0.2, "sectors": ["Technology"]})
    assert sorted(run_query(database, both)) == ["6857.T", "NVDA"]

    jp_only = build_query({"max_per": 15, "min_roe": 0.2, "markets": ["JP"]})
    assert run_query(database, jp_only) == ["6857.T"]


def test_growth_criteria_load_the_statement_series(database: Database) -> None:
    _seed(database, "NVDA", "US", "Technology", 12.0, 0.35, [(2023, 100, 10), (2024, 150, 18)])
    _seed(database, "INTC", "US", "Technology", 40.0, 0.05, [(2023, 100, 10), (2024, 101, 10)])

    query = build_query({"min_revenue_growth": 20})  # 20% after rescaling
    assert query.needs_statements
    assert run_query(database, query) == ["NVDA"]


def test_a_sector_only_query_needs_no_condition(database: Database) -> None:
    _seed(database, "NVDA", "US", "Technology", per=12.0, roe=0.35)
    _seed(database, "JPM", "US", "Financials", per=11.0, roe=0.25)

    assert run_query(database, build_query({"sectors": ["Financials"]})) == ["JPM"]


def test_a_symbol_without_a_profile_never_matches_a_sector_query(database: Database) -> None:
    """An unclassified name cannot be shown to be in the sector asked for."""
    _seed(database, "NVDA", "US", "Technology", per=12.0, roe=0.35)
    with database.session() as session:
        FundamentalsRepository(session).upsert_fundamentals(
            Fundamentals(symbol="MYSTERY", as_of=dt.date(2024, 6, 30), per=5.0, roe=0.9)
        )

    assert run_query(database, build_query({"sectors": ["Technology"]})) == ["NVDA"]


def test_an_empty_universe_returns_nothing(database: Database) -> None:
    assert run_query(database, build_query({"max_per": 15})) == []


def test_an_empty_query_is_reported_as_such() -> None:
    assert ScreenQuery(condition=None).is_empty
    assert not ScreenQuery(condition=None, markets=["JP"]).is_empty
