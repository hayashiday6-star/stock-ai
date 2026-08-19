"""Tests for OpenAI/Gemini providers, analysis use-cases, and the AI CLI."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from typer.testing import CliRunner

from stock_ai import cli
from stock_ai.ai.analysis import analyze_sentiment, summarize
from stock_ai.ai.gemini_provider import GeminiProvider
from stock_ai.ai.openai_provider import OpenAIProvider
from stock_ai.core.exceptions import AIError

runner = CliRunner()


# --- OpenAI provider (injected fake client) --------------------------------


@dataclass
class _Msg:
    content: str | None


@dataclass
class _Choice:
    message: _Msg


@dataclass
class _ChatResponse:
    choices: list[_Choice]


class _FakeChat:
    def __init__(self, content: str | None) -> None:
        self.content = content
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _ChatResponse:
        self.calls.append(kwargs)
        return _ChatResponse(choices=[_Choice(_Msg(self.content))])


@dataclass
class _FakeOpenAI:
    chat: Any


def test_openai_returns_message_content() -> None:
    fake = _FakeOpenAI(chat=type("C", (), {"completions": _FakeChat("hi there")})())
    provider = OpenAIProvider(client=fake)
    assert provider.complete("q", system="s") == "hi there"


def test_openai_empty_raises() -> None:
    fake = _FakeOpenAI(chat=type("C", (), {"completions": _FakeChat(None)})())
    with pytest.raises(AIError):
        OpenAIProvider(client=fake).complete("q")


# --- Gemini provider (injected fake model) ---------------------------------


@dataclass
class _GeminiResp:
    text: str | None


@dataclass
class _FakeGeminiModel:
    text: str | None
    calls: list[str] = field(default_factory=list)

    def generate_content(self, content: str) -> _GeminiResp:
        self.calls.append(content)
        return _GeminiResp(text=self.text)


def test_gemini_returns_text_and_prepends_system() -> None:
    model = _FakeGeminiModel(text="answer")
    provider = GeminiProvider(client=model)
    assert provider.complete("prompt", system="sys") == "answer"
    assert model.calls[0].startswith("sys")


def test_gemini_empty_raises() -> None:
    with pytest.raises(AIError):
        GeminiProvider(client=_FakeGeminiModel(text=None)).complete("q")


# --- analysis use-cases with a stub provider -------------------------------


class _StubProvider:
    name = "stub"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.last_system: str | None = None

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        **_kwargs: object,
    ) -> str:
        self.last_system = system
        return self.reply


def test_summarize_uses_provider_and_system() -> None:
    stub = _StubProvider("  A concise summary.  ")
    assert summarize(stub, "long text", max_words=50) == "A concise summary."
    assert "analyst" in (stub.last_system or "")


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("Positive.", "positive"),
        ("This reads NEGATIVE overall", "negative"),
        ("neutral", "neutral"),
        ("I cannot tell", "unknown"),
    ],
)
def test_analyze_sentiment_maps_labels(reply: str, expected: str) -> None:
    assert analyze_sentiment(_StubProvider(reply), "text") == expected


# --- CLI (dummy provider, offline) -----------------------------------------


def test_summarize_cli_dummy() -> None:
    result = runner.invoke(cli.app, ["summarize", "Revenue rose 20% this quarter."])
    assert result.exit_code == 0
    assert "Revenue rose 20%" in result.stdout


def test_sentiment_cli_dummy() -> None:
    result = runner.invoke(cli.app, ["sentiment", "The outlook is positive and strong."])
    assert result.exit_code == 0
    assert "positive" in result.stdout


def test_summarize_cli_unknown_provider_errors() -> None:
    result = runner.invoke(cli.app, ["summarize", "text", "--provider", "bogus"])
    assert result.exit_code != 0
