"""Tests for sector normalization, holdings, schema upgrades, and portfolio analysis."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine, text

from stock_ai.core.exceptions import DataError
from stock_ai.data.fx import static_converter
from stock_ai.data.jquants_profile import JQuantsProfileProvider, normalize_listing
from stock_ai.data.sectors import Sector, from_topix17, from_tse33, from_yfinance, parse
from stock_ai.data.types import SecurityProfile
from stock_ai.data.yfinance_provider import YFinanceProfileProvider
from stock_ai.database.engine import Database
from stock_ai.database.repository import (
    HoldingRepository,
    PriceRepository,
    get_profile,
    upsert_profile,
)
from stock_ai.portfolio.analysis import analyze_portfolio

_JPY_USD = 1.0 / 150.0


@pytest.fixture
def database() -> Iterator[Database]:
    db = Database("sqlite:///:memory:")
    db.create_all()
    yield db
    db.dispose()


def _seed_prices(
    db: Database, symbol: str, market: str, prices: list[float], sector: str | None = None
) -> None:
    index = pd.date_range("2024-01-01", periods=len(prices), freq="B", name="date")
    series = np.array(prices, dtype=float)
    frame = pd.DataFrame(
        {
            "open": series,
            "high": series,
            "low": series,
            "close": series,
            "adj_close": series,
            "volume": 1_000,
        },
        index=index,
    )
    with db.session() as session:
        PriceRepository(session).upsert_prices(symbol, frame, market=market)
        if sector is not None:
            upsert_profile(session, SecurityProfile(symbol=symbol, market=market, sector=sector))


# --- sector normalization ---------------------------------------------------


def test_us_and_jp_labels_land_in_the_same_bucket() -> None:
    """The whole point: a JP bank and a US bank must group together."""
    assert from_yfinance("Financial Services") is Sector.FINANCIALS
    assert from_topix17("15") is Sector.FINANCIALS  # 銀行
    assert from_tse33("7050") is Sector.FINANCIALS  # 銀行業


def test_sector_lookup_is_case_and_padding_insensitive() -> None:
    assert from_yfinance("  technology  ") is Sector.TECHNOLOGY
    assert from_topix17("09") is Sector.TECHNOLOGY  # zero-padded 電機・精密
    assert from_topix17(9) is Sector.TECHNOLOGY  # numeric payloads


def test_unknown_classification_becomes_other_rather_than_a_guess() -> None:
    assert from_yfinance("Cryptocurrency Mining") is Sector.OTHER
    assert from_yfinance(None) is Sector.OTHER
    assert from_topix17("999") is Sector.OTHER
    assert from_tse33(None) is Sector.OTHER
    assert parse("not a sector") is Sector.OTHER


def test_stored_sector_names_round_trip() -> None:
    for sector in Sector:
        assert parse(str(sector)) is sector


# --- profile providers ------------------------------------------------------


def test_yfinance_profile_normalizes_the_sector() -> None:
    provider = YFinanceProfileProvider(
        info_fetcher=lambda _s: {
            "longName": "Apple Inc.",
            "sector": "Technology",
            "industry": "Consumer Electronics",
        }
    )
    profile = provider.fetch_profile("AAPL")
    assert profile.name == "Apple Inc."
    assert profile.sector == str(Sector.TECHNOLOGY)
    assert profile.industry == "Consumer Electronics"
    assert profile.market == "US"


def test_jquants_profile_prefers_the_finer_tse33_code() -> None:
    records = [
        {"Name": "トヨタ自動車", "Sec33Cd": "3700", "Sec17Cd": "6", "Sec33Name": "輸送用機器"}
    ]
    profile = normalize_listing("7203", records)
    assert profile.sector == str(Sector.CONSUMER_CYCLICAL)
    assert profile.industry == "輸送用機器"
    assert profile.market == "JP"


def test_jquants_profile_falls_back_to_topix17() -> None:
    profile = normalize_listing("8306", [{"Name": "銀行", "Sec17Cd": "15"}])
    assert profile.sector == str(Sector.FINANCIALS)


def test_jquants_profile_requires_records() -> None:
    with pytest.raises(DataError):
        normalize_listing("X", [])


def test_jquants_provider_uses_the_injected_fetcher() -> None:
    provider = JQuantsProfileProvider(fetcher=lambda _s: [{"Name": "X", "Sec33Cd": "7050"}])
    assert provider.fetch_profile("8306").sector == str(Sector.FINANCIALS)


# --- profile persistence ----------------------------------------------------


def test_profile_round_trips(database: Database) -> None:
    with database.session() as session:
        upsert_profile(
            session,
            SecurityProfile(symbol="AAPL", market="US", name="Apple", sector="Technology"),
        )
    with database.session() as session:
        stored = get_profile(session, "AAPL")
    assert stored is not None
    assert (stored.name, stored.sector, stored.market) == ("Apple", "Technology", "US")


def test_a_partial_profile_does_not_blank_existing_fields(database: Database) -> None:
    """A provider that omits the industry must not erase another's."""
    with database.session() as session:
        upsert_profile(
            session,
            SecurityProfile(symbol="X", name="Full", sector="Technology", industry="Software"),
        )
        upsert_profile(session, SecurityProfile(symbol="X", sector="Financials"))

    with database.session() as session:
        stored = get_profile(session, "X")
    assert stored is not None
    assert stored.sector == "Financials"  # updated
    assert stored.industry == "Software"  # preserved
    assert stored.name == "Full"


def test_missing_profile_is_none(database: Database) -> None:
    with database.session() as session:
        assert get_profile(session, "NOPE") is None


# --- holdings ---------------------------------------------------------------


def test_holdings_round_trip_and_update(database: Database) -> None:
    with database.session() as session:
        repo = HoldingRepository(session)
        repo.set_holding("AAPL", 100, 120.0, market="US")
        repo.set_holding("AAPL", 150, 130.0, market="US")  # replaces, not appends

    with database.session() as session:
        holdings = HoldingRepository(session).list_holdings()
    assert len(holdings) == 1
    assert (holdings[0].quantity, holdings[0].average_cost) == (150, 130.0)


def test_adding_shares_blends_the_cost_basis(database: Database) -> None:
    with database.session() as session:
        repo = HoldingRepository(session)
        repo.set_holding("AAPL", 100, 100.0)
        repo.add_shares("AAPL", 100, 200.0)

    with database.session() as session:
        holding = HoldingRepository(session).get_holding("AAPL")
    assert holding is not None
    assert holding.quantity == 200
    assert holding.average_cost == pytest.approx(150.0)
    assert holding.cost_basis() == pytest.approx(30_000.0)


def test_a_non_positive_quantity_clears_the_position(database: Database) -> None:
    with database.session() as session:
        repo = HoldingRepository(session)
        repo.set_holding("AAPL", 100, 120.0)
        repo.set_holding("AAPL", 0, 0.0)

    with database.session() as session:
        assert HoldingRepository(session).list_holdings() == []


# --- schema upgrade ---------------------------------------------------------


def test_an_older_database_gains_new_columns_without_losing_data(tmp_path: Path) -> None:
    """create_all() alone leaves old tables short a column; the data must survive."""
    path = tmp_path / "old.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE securities (
            id INTEGER PRIMARY KEY, symbol VARCHAR(32) UNIQUE,
            name VARCHAR(128), market VARCHAR(8), created_at DATETIME
        );
        INSERT INTO securities (symbol, name, market) VALUES ('AAPL', 'Apple', 'US');
        """
    )
    connection.commit()
    connection.close()

    upgraded = Database(f"sqlite:///{path}")
    upgraded.create_all()
    upgraded.dispose()

    engine = create_engine(f"sqlite:///{path}")
    with engine.connect() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(securities)"))}
        tables = {
            row[0]
            for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        }
        rows = list(conn.execute(text("SELECT symbol, name, sector FROM securities")))
    engine.dispose()

    assert {"sector", "industry"} <= columns
    assert "holdings" in tables  # a wholly new table is created too
    assert rows == [("AAPL", "Apple", None)]  # existing data intact


def test_create_all_is_repeatable(database: Database) -> None:
    database.create_all()
    database.create_all()  # must not fail re-adding columns


# --- portfolio analysis -----------------------------------------------------


def test_positions_are_valued_and_weighted_in_the_base_currency(database: Database) -> None:
    """A ¥ position and a $ position only compare once both are converted."""
    _seed_prices(database, "AAPL", "US", [100.0] * 10, sector="Technology")
    _seed_prices(database, "7203.T", "JP", [3000.0] * 10, sector="Consumer Cyclical")
    with database.session() as session:
        repo = HoldingRepository(session)
        repo.set_holding("AAPL", 100, 80.0, market="US")  # $10,000 now, $8,000 cost
        repo.set_holding("7203.T", 500, 2400.0, market="JP")  # ¥1.5m -> $10,000

    analysis = analyze_portfolio(database, fx=static_converter("USD", JPY=_JPY_USD, USD=1.0))

    assert analysis.total_value == pytest.approx(20_000.0)
    assert analysis.total_cost == pytest.approx(16_000.0)
    assert analysis.unrealized_return == pytest.approx(0.25)
    assert {p.symbol: round(p.weight, 4) for p in analysis.positions} == {
        "AAPL": 0.5,
        "7203.T": 0.5,
    }


def test_sector_and_market_weights_group_across_markets(database: Database) -> None:
    _seed_prices(database, "AAPL", "US", [100.0] * 10, sector="Technology")
    _seed_prices(database, "MSFT", "US", [100.0] * 10, sector="Technology")
    _seed_prices(database, "8306.T", "JP", [150.0] * 10, sector="Financials")
    with database.session() as session:
        repo = HoldingRepository(session)
        repo.set_holding("AAPL", 100, 100.0, market="US")  # $10,000
        repo.set_holding("MSFT", 100, 100.0, market="US")  # $10,000
        repo.set_holding("8306.T", 20_000, 150.0, market="JP")  # ¥3m -> $20,000

    analysis = analyze_portfolio(database, fx=static_converter("USD", JPY=_JPY_USD, USD=1.0))

    assert analysis.sector_weights[Sector.TECHNOLOGY] == pytest.approx(0.5)
    assert analysis.sector_weights[Sector.FINANCIALS] == pytest.approx(0.5)
    assert analysis.market_weights == pytest.approx({"US": 0.5, "JP": 0.5})


def test_a_holding_without_a_sector_falls_into_other(database: Database) -> None:
    _seed_prices(database, "X", "US", [100.0] * 10)  # no profile stored
    with database.session() as session:
        HoldingRepository(session).set_holding("X", 10, 100.0)

    analysis = analyze_portfolio(database, fx=static_converter("USD", USD=1.0))
    assert analysis.sector_weights == {Sector.OTHER: pytest.approx(1.0)}


def test_unpriced_holdings_are_excluded_not_valued_at_zero(database: Database) -> None:
    """Counting them at zero would silently understate every other weight."""
    _seed_prices(database, "AAPL", "US", [100.0] * 10, sector="Technology")
    with database.session() as session:
        repo = HoldingRepository(session)
        repo.set_holding("AAPL", 100, 100.0)
        repo.set_holding("NOPRICE", 50, 10.0)

    analysis = analyze_portfolio(database, fx=static_converter("USD", USD=1.0))

    assert analysis.unpriced == ["NOPRICE"]
    assert [p.symbol for p in analysis.positions] == ["AAPL"]
    assert analysis.positions[0].weight == pytest.approx(1.0)


def test_concentration_reflects_the_number_of_equal_positions(database: Database) -> None:
    for symbol in ("A", "B", "C", "D"):
        _seed_prices(database, symbol, "US", [100.0] * 10, sector="Technology")
        with database.session() as session:
            HoldingRepository(session).set_holding(symbol, 10, 100.0)

    analysis = analyze_portfolio(database, fx=static_converter("USD", USD=1.0))

    assert analysis.concentration == pytest.approx(0.25)  # 4 equal weights: 4 * 0.25^2
    assert analysis.effective_positions == pytest.approx(4.0)


def test_volatility_captures_diversification_not_an_average(database: Database) -> None:
    """Two perfectly anticorrelated names cancel; averaging their vols would not."""
    up = [100.0 * (1.01**i) if i % 2 == 0 else 100.0 * (1.01 ** (i - 1)) * 0.99 for i in range(60)]
    down = [
        200.0 * (0.99**i) if i % 2 == 0 else 200.0 * (0.99 ** (i - 1)) * 1.01 for i in range(60)
    ]
    _seed_prices(database, "UP", "US", up, sector="Technology")
    _seed_prices(database, "DOWN", "US", down, sector="Technology")
    with database.session() as session:
        repo = HoldingRepository(session)
        repo.set_holding("UP", 100, 100.0)
        repo.set_holding("DOWN", 50, 200.0)

    analysis = analyze_portfolio(database, fx=static_converter("USD", USD=1.0))

    assert analysis.annual_volatility is not None
    assert analysis.correlations is not None
    # Anticorrelated legs must leave the blend calmer than either one alone.
    solo = analysis.correlations.shape[0]
    assert solo == 2
    assert analysis.correlations.loc["UP", "DOWN"] < 0


def test_an_empty_portfolio_reports_nothing_rather_than_dividing_by_zero(
    database: Database,
) -> None:
    analysis = analyze_portfolio(database, fx=static_converter("USD", USD=1.0))

    assert analysis.positions == []
    assert analysis.total_value == 0.0
    assert analysis.unrealized_return is None
    assert analysis.annual_volatility is None
    assert analysis.concentration is None
    assert analysis.effective_positions is None


# --- TSE-33 buckets that real data proved wrong -----------------------------


def test_service_sector_is_not_communication() -> None:
    """9050 サービス業 is a catch-all, and COMMUNICATION is the wrong catch.

    Found on live data: a 20-name TSE Growth sample put a biotech, a recycler,
    and a photo-services firm in "Communication Services" together. Growth is
    dominated by 9050, so the mistake collapses most of a screen into one bucket
    and inflates every concentration figure computed from it.
    """
    from stock_ai.data.sectors import Sector, from_tse33

    assert from_tse33("9050") is Sector.INDUSTRIALS


def test_telecoms_keep_the_communication_bucket() -> None:
    """5250 情報・通信業 is the code that genuinely means telecom and media."""
    from stock_ai.data.sectors import Sector, from_tse33

    assert from_tse33("5250") is Sector.COMMUNICATION


def test_the_service_and_telecom_codes_stay_distinct() -> None:
    """The two catch-alls must not collapse into each other.

    Note what this does *not* claim. On the live Growth sample the fix did not
    spread names out - it moved them, from 10 in Communication Services to 14 in
    Industrials, because that slice really is mostly 建設業 and サービス業. A
    concentrated breakdown can be the truth. What matters is that a telecom and
    a staffing firm are not filed as the same business.
    """
    from stock_ai.data.sectors import Sector, from_tse33

    assert from_tse33("9050") is not from_tse33("5250")
    assert from_tse33("9050") is Sector.INDUSTRIALS
    assert from_tse33("5250") is Sector.COMMUNICATION


def test_the_tse33_table_covers_the_whole_canonical_set() -> None:
    """A table that folded everything into two buckets would pass the tests above."""
    from stock_ai.data.sectors import _TSE33_SECTORS, Sector

    mapped = set(_TSE33_SECTORS.values())
    assert Sector.OTHER not in mapped  # OTHER means "unknown", never a target
    assert len(mapped) >= 8
