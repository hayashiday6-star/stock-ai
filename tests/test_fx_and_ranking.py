"""Tests for currency conversion and the cross-market ranking (no network)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator

import numpy as np
import pandas as pd
import pytest

from stock_ai.core.exceptions import DataError
from stock_ai.data.fx import FxConverter, currency_for_market, static_converter
from stock_ai.data.types import Fundamentals
from stock_ai.database.engine import Database
from stock_ai.database.repository import (
    FundamentalsRepository,
    PriceRepository,
    list_securities,
)
from stock_ai.portfolio.ranking import rank_securities

_AS_OF = dt.date(2024, 6, 30)
# One dollar buys 150 yen, so a yen figure is worth 1/150 of a dollar.
_JPY_USD = 1.0 / 150.0


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
    *,
    market_cap: float | None = None,
    roe: float = 0.15,
    per: float = 20.0,
    bars: int = 200,
) -> None:
    """Store a rising price series plus one fundamentals snapshot."""
    index = pd.date_range("2024-01-01", periods=bars, freq="B", name="date")
    close = np.arange(bars, dtype=float) + 100.0
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
        FundamentalsRepository(session).upsert_fundamentals(
            Fundamentals(
                symbol=symbol,
                as_of=_AS_OF,
                roe=roe,
                per=per,
                revenue=1e9,
                net_income=2e8,
                dividend_yield=0.03,
                market_cap=market_cap,
            ),
            market=market,
        )


# --- currency ---------------------------------------------------------------


def test_currency_follows_the_listing_market() -> None:
    assert currency_for_market("JP") == "JPY"
    assert currency_for_market("us") == "USD"
    assert currency_for_market("XX") == "USD"  # unknown falls back to the base


def test_converter_caches_one_lookup_per_currency() -> None:
    calls: list[str] = []

    def fetcher(currency: str, base: str) -> float:
        calls.append(currency)
        return _JPY_USD

    fx = FxConverter(base="USD", fetcher=fetcher)
    assert fx.convert(15_000.0, "JPY") == pytest.approx(100.0)
    assert fx.convert(30_000.0, "JPY") == pytest.approx(200.0)
    assert calls == ["JPY"]  # cached, not refetched per row


def test_base_currency_needs_no_lookup() -> None:
    fx = static_converter("USD")  # its fetcher raises if consulted
    assert fx.convert(1_234.0, "USD") == pytest.approx(1_234.0)


def test_missing_amount_converts_to_none() -> None:
    assert static_converter("USD", JPY=_JPY_USD).convert(None, "JPY") is None


def test_static_converter_refuses_to_reach_the_network() -> None:
    with pytest.raises(DataError):
        static_converter("USD", JPY=_JPY_USD).convert(1.0, "EUR")


# --- repository -------------------------------------------------------------


def test_list_securities_reports_the_market(database: Database) -> None:
    _seed(database, "AAPL", "US")
    _seed(database, "7203.T", "JP")
    with database.session() as session:
        assert list_securities(session) == [("7203.T", "JP"), ("AAPL", "US")]


# --- ranking ----------------------------------------------------------------


def test_ranking_covers_both_markets_and_sorts_by_score(database: Database) -> None:
    _seed(database, "AAPL", "US", roe=0.30, per=10.0, market_cap=2e12)
    _seed(database, "7203.T", "JP", roe=0.05, per=35.0, market_cap=3e13)

    frame = rank_securities(database, fx=static_converter("USD", JPY=_JPY_USD, USD=1.0))

    assert list(frame["symbol"]) == ["AAPL", "7203.T"]  # score descending
    assert list(frame["market"]) == ["US", "JP"]
    assert frame["score"].is_monotonic_decreasing


def test_market_cap_is_restated_in_the_base_currency(database: Database) -> None:
    """A ¥30tn company must not outrank a $2tn one on size alone."""
    _seed(database, "7203.T", "JP", market_cap=3e13)
    _seed(database, "AAPL", "US", market_cap=2e12)

    frame = rank_securities(database, fx=static_converter("USD", JPY=_JPY_USD, USD=1.0))
    caps = dict(zip(frame["symbol"], frame["market_cap"], strict=True))

    assert caps["7203.T"] == pytest.approx(2e11)  # ¥30tn -> $200bn
    assert caps["AAPL"] == pytest.approx(2e12)  # already USD
    assert caps["AAPL"] > caps["7203.T"]


def test_size_bounds_are_applied_after_conversion(database: Database) -> None:
    """The whole point of the FX step: one size filter, both markets."""
    _seed(database, "4593.T", "JP", market_cap=3e10)  # ¥30bn -> $200m
    _seed(database, "7203.T", "JP", market_cap=3e13)  # ¥30tn -> $200bn
    _seed(database, "AAPL", "US", market_cap=2e12)

    fx = static_converter("USD", JPY=_JPY_USD, USD=1.0)
    small = rank_securities(database, fx=fx, max_market_cap=1e9)
    assert list(small["symbol"]) == ["4593.T"]

    large = rank_securities(database, fx=fx, min_market_cap=1e11)
    assert set(large["symbol"]) == {"7203.T", "AAPL"}


def test_unknown_market_cap_never_satisfies_a_size_bound(database: Database) -> None:
    _seed(database, "NOCAP", "US", market_cap=None)
    _seed(database, "AAPL", "US", market_cap=2e12)

    fx = static_converter("USD", USD=1.0)
    assert list(rank_securities(database, fx=fx, min_market_cap=0.0)["symbol"]) == ["AAPL"]
    # Unbounded, it is still ranked — only the size filter excludes it.
    assert "NOCAP" in set(rank_securities(database, fx=fx)["symbol"])


def test_ranking_can_be_restricted_to_named_symbols(database: Database) -> None:
    _seed(database, "AAPL", "US", market_cap=2e12)
    _seed(database, "7203.T", "JP", market_cap=3e13)

    frame = rank_securities(
        database, symbols=["AAPL"], fx=static_converter("USD", JPY=_JPY_USD, USD=1.0)
    )
    assert list(frame["symbol"]) == ["AAPL"]


def test_empty_database_ranks_to_an_empty_frame(database: Database) -> None:
    frame = rank_securities(database, fx=static_converter("USD"))
    assert frame.empty
    assert list(frame.columns) == ["symbol", "market", "score", "market_cap"]


# --- failure messages must name the fix -------------------------------------


def test_an_unfetchable_rate_says_how_to_pin_it() -> None:
    """A transport failure buried in a stack trace tells the user nothing."""

    def boom(currency: str, base: str) -> float:
        raise RuntimeError("CONNECT tunnel failed, response 403")

    fx = FxConverter(base="USD", fetcher=boom)
    with pytest.raises(DataError) as excinfo:
        fx.convert(1.0, "JPY")

    message = str(excinfo.value)
    assert "JPY" in message
    assert "--fx JPY=" in message  # the actionable part


def test_a_data_error_from_the_fetcher_passes_through_unwrapped() -> None:
    """static_converter's refusal already reads well; do not re-wrap it."""
    with pytest.raises(DataError, match="No FX rate configured"):
        static_converter("USD").convert(1.0, "EUR")
