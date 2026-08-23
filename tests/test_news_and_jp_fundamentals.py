"""Tests for J-Quants fundamentals and the news → text_source pipeline."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from stock_ai.core.exceptions import DataError
from stock_ai.data.jquants_fundamentals import (
    JQuantsFundamentalsProvider,
    normalize_statement,
)
from stock_ai.news.sources import (
    NewsItem,
    StaticNewsSource,
    YFinanceNewsSource,
    make_text_source,
)
from stock_ai.portfolio.ai_factors import NewsSentimentFactor
from stock_ai.screening.base import ScreeningContext

_TODAY = dt.date(2024, 6, 30)


# --- J-Quants fundamentals -------------------------------------------------


def _statements() -> list[dict[str, Any]]:
    """Records in the real V2 ``fins/summary`` shape (Sales/NP/Eq/EPS/BPS)."""
    return [
        {
            "DiscDate": "2024-02-14",
            "Sales": "1000000",
            "NP": "50000",
            "Eq": "500000",
        },
        {  # newer disclosure must win
            "DiscDate": "2024-05-15",
            "Sales": "1200000",
            "NP": "90000",
            "Eq": "600000",
            "EPS": "100",
            "BPS": "1000",
            "DivAnn": "40",
            "ShOutFY": "1000",
        },
    ]


def test_normalize_statement_uses_latest_and_computes_roe() -> None:
    result = normalize_statement("7203", _statements(), _TODAY)
    assert result.symbol == "7203"
    assert result.as_of == _TODAY
    assert result.revenue == 1_200_000
    assert result.net_income == 90_000
    assert result.roe == pytest.approx(90_000 / 600_000)
    # Without a price, price-dependent ratios stay absent.
    assert result.per is None
    assert result.pbr is None


def test_normalize_statement_with_price_computes_ratios() -> None:
    result = normalize_statement("7203", _statements(), _TODAY, price=2000.0)
    assert result.per == pytest.approx(2000 / 100)  # price / EPS
    assert result.pbr == pytest.approx(2000 / 1000)  # price / BPS
    assert result.dividend_yield == pytest.approx(40 / 2000)
    assert result.market_cap == pytest.approx(2000 * 1000)


def test_normalize_statement_accepts_v1_field_names() -> None:
    records = [{"DisclosedDate": "2024-05-15", "NetSales": "100", "Profit": "10"}]
    result = normalize_statement("7203", records, _TODAY)
    assert result.roe is None  # equity missing
    assert result.revenue == 100


def test_normalize_statement_empty_raises() -> None:
    with pytest.raises(DataError):
        normalize_statement("7203", [], _TODAY)


def test_jp_fundamentals_provider() -> None:
    provider = JQuantsFundamentalsProvider(fetcher=lambda _s: _statements(), clock=lambda: _TODAY)
    result = provider.fetch_fundamentals("7203")
    assert result.net_income == 90_000


# --- news sources ----------------------------------------------------------


def test_static_source_and_text_source() -> None:
    source = StaticNewsSource(
        {"AAPL": [NewsItem("Record revenue", "Apple beat estimates."), NewsItem("New product")]}
    )
    text = make_text_source(source)("AAPL")
    assert text is not None
    assert "Record revenue" in text
    assert "New product" in text


def test_text_source_none_when_no_items() -> None:
    assert make_text_source(StaticNewsSource({}))("AAPL") is None


def test_text_source_respects_limit_and_truncation() -> None:
    items = [NewsItem(f"headline {i}") for i in range(10)]
    source = StaticNewsSource({"AAPL": items})
    text = make_text_source(source, limit=2, max_chars=15)("AAPL")
    assert text is not None
    assert len(text) <= 15


def test_yfinance_source_parses_nested_content() -> None:
    raw = [{"content": {"title": "Earnings beat", "summary": "Strong quarter."}}]
    source = YFinanceNewsSource(fetcher=lambda _s: raw)
    items = source.fetch("AAPL")
    assert items[0].title == "Earnings beat"
    assert "Strong quarter" in items[0].as_text()


def test_yfinance_source_returns_empty_on_error() -> None:
    def boom(_s: str) -> list[dict[str, Any]]:
        raise RuntimeError("network down")

    assert YFinanceNewsSource(fetcher=boom).fetch("AAPL") == []


# --- end-to-end: news feeds the sentiment factor ---------------------------


def test_news_pipeline_feeds_sentiment_factor() -> None:
    class _StubAI:
        name = "stub"

        def complete(
            self,
            prompt: str,
            *,
            system: str | None = None,
            max_tokens: int = 1024,
            **_kwargs: object,
        ) -> str:
            return "positive"

    source = StaticNewsSource({"AAPL": [NewsItem("Record revenue", "Beat estimates.")]})
    factor = NewsSentimentFactor(_StubAI(), text_source=make_text_source(source))
    assert factor.score(ScreeningContext("AAPL")) == 1.0


def test_jquants_to_float_rejects_non_finite_values() -> None:
    """A NaN that escapes parsing poisons every ratio derived from it."""
    from stock_ai.data.jquants_fundamentals import _to_float

    assert _to_float(float("nan")) is None
    assert _to_float("nan") is None
    assert _to_float(float("inf")) is None
    assert _to_float("1.5") == 1.5
    assert _to_float("") is None
    assert _to_float(None) is None


def test_a_bare_japanese_code_is_queried_in_yahoos_own_form() -> None:
    """`3003` is not ヒューリック to Yahoo; Tadawul numbers listings the same way.

    Watching the bare code delivered Middle East small-cap articles - one of
    them naming City Cement, which is Tadawul 3003 - under a Japanese
    company's ticker. Every part of that output looks correct: the header
    names the watched symbol, the summary is a faithful rendering of the
    article, and nothing raises. Only the company is wrong.
    """
    from stock_ai.news.sources import YFinanceNewsSource

    asked: list[str] = []

    def fetcher(symbol: str) -> list[dict[str, object]]:
        asked.append(symbol)
        return [{"title": "決算", "summary": "..."}]

    source = YFinanceNewsSource(fetcher=fetcher)
    source.fetch("3003")
    source.fetch("7203.T")
    source.fetch("AAPL")

    assert asked == ["3003.T", "7203.T", "AAPL"]


def test_yahoo_symbol_leaves_anything_it_cannot_place_alone() -> None:
    """Guessing beyond the unambiguous case would route symbols wrongly."""
    from stock_ai.data.markets import to_yahoo_symbol

    assert to_yahoo_symbol("3003") == "3003.T"
    assert to_yahoo_symbol("3003.JP") == "3003.T"
    assert to_yahoo_symbol("BRK.B") == "BRK.B"
    assert to_yahoo_symbol("AAPL") == "AAPL"


def test_price_provider_asks_yahoo_for_the_tokyo_listing() -> None:
    """A bare four-digit code must not be sent to Yahoo as-is.

    Tadawul lists in the same range, so ``3003`` is answered - by City Cement.
    The provider would then return a complete, plausible price series for a
    Saudi cement maker under a Japanese symbol, and nothing downstream could
    tell. The news source was fixed for this; the price path was not.
    """
    import pandas as pd

    from stock_ai.data.yfinance_provider import YFinancePriceProvider

    asked: list[str] = []

    def downloader(symbol: str, start: dt.date, end: dt.date) -> pd.DataFrame:
        asked.append(symbol)
        return pd.DataFrame(
            {
                "Open": [1.0],
                "High": [1.0],
                "Low": [1.0],
                "Close": [1.0],
                "Adj Close": [1.0],
                "Volume": [1],
            },
            index=pd.DatetimeIndex([pd.Timestamp("2024-06-28")], name="Date"),
        )

    provider = YFinancePriceProvider(downloader=downloader)
    for symbol in ("3003", "7203.T", "AAPL"):
        provider.fetch_prices(symbol, dt.date(2024, 6, 1), _TODAY)

    assert asked == ["3003.T", "7203.T", "AAPL"]


def test_fundamentals_provider_asks_yahoo_for_the_tokyo_listing() -> None:
    """Same translation, and the snapshot keeps the caller's own symbol."""
    from stock_ai.data.yfinance_provider import YFinanceFundamentalsProvider

    asked: list[str] = []

    def info(symbol: str) -> dict[str, Any]:
        asked.append(symbol)
        return {"trailingPE": 10.0}

    provider = YFinanceFundamentalsProvider(info_fetcher=info, clock=lambda: _TODAY)
    snapshot = provider.fetch_fundamentals("3003")

    assert asked == ["3003.T"]
    # Stored under the symbol the rest of the system uses, not Yahoo's spelling.
    assert snapshot.symbol == "3003"


def test_profile_provider_reads_the_market_off_the_ticker() -> None:
    """``market`` was hardcoded to US, which is what the ranking splits on."""
    from stock_ai.data.yfinance_provider import YFinanceProfileProvider

    asked: list[str] = []

    def info(symbol: str) -> dict[str, Any]:
        asked.append(symbol)
        return {"longName": "Hulic Co., Ltd.", "sector": "Real Estate"}

    profile = YFinanceProfileProvider(info_fetcher=info).fetch_profile("3003")

    assert asked == ["3003.T"]
    assert profile.market == "JP"
    assert profile.symbol == "3003"


def test_missing_data_error_names_the_ticker_actually_queried() -> None:
    """Otherwise the message blames ``3003`` for a ``3003.T`` request."""
    from stock_ai.data.yfinance_provider import YFinanceProfileProvider

    provider = YFinanceProfileProvider(info_fetcher=lambda _symbol: {})
    with pytest.raises(DataError) as excinfo:
        provider.fetch_profile("3003")

    assert "3003.T" in str(excinfo.value)
