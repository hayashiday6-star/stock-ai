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
            self, prompt: str, *, system: str | None = None, max_tokens: int = 1024
        ) -> str:
            return "positive"

    source = StaticNewsSource({"AAPL": [NewsItem("Record revenue", "Beat estimates.")]})
    factor = NewsSentimentFactor(_StubAI(), text_source=make_text_source(source))
    assert factor.score(ScreeningContext("AAPL")) == 1.0
