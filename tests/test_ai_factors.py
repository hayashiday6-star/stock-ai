"""Tests for the AI news-sentiment scoring factor."""

from __future__ import annotations

import datetime as dt

import pytest

from stock_ai.data.types import Fundamentals
from stock_ai.portfolio.ai_factors import (
    NewsSentimentFactor,
    weighted_factors_with_sentiment,
)
from stock_ai.portfolio.scoring import WeightedScorer
from stock_ai.screening.base import ScreeningContext

_TODAY = dt.date(2024, 6, 30)


class _StubProvider:
    name = "stub"

    def __init__(self, reply: str) -> None:
        self.reply = reply

    def complete(self, prompt: str, *, system: str | None = None, max_tokens: int = 1024) -> str:
        return self.reply


def _ctx(symbol: str = "AAPL") -> ScreeningContext:
    return ScreeningContext(
        symbol=symbol, fundamentals=Fundamentals(symbol=symbol, as_of=_TODAY, roe=0.30)
    )


@pytest.mark.parametrize(
    ("reply", "expected"),
    [("positive", 1.0), ("neutral", 0.5), ("negative", 0.0)],
)
def test_sentiment_factor_maps_labels(reply: str, expected: float) -> None:
    factor = NewsSentimentFactor(_StubProvider(reply), text_source=lambda _s: "news text")
    assert factor.score(_ctx()) == expected


def test_sentiment_factor_none_without_text() -> None:
    factor = NewsSentimentFactor(_StubProvider("positive"), text_source=lambda _s: None)
    assert factor.score(_ctx()) is None


def test_sentiment_factor_none_when_unclassifiable() -> None:
    factor = NewsSentimentFactor(_StubProvider("I cannot tell"), text_source=lambda _s: "x")
    assert factor.score(_ctx()) is None


def test_scorer_includes_sentiment_factor() -> None:
    factors = weighted_factors_with_sentiment(
        _StubProvider("positive"), text_source=lambda _s: "great earnings"
    )
    result = WeightedScorer(factors).score(_ctx())
    assert "news_sentiment" in result.breakdown
    assert result.breakdown["news_sentiment"] == 1.0
    assert result.score > 0
