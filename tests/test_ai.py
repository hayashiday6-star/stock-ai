"""Tests for the AI provider abstraction, dummy provider, and factory.

The Anthropic provider is tested with an injected fake SDK client, so no network
access or API key is required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from stock_ai.ai.anthropic_provider import DEFAULT_MODEL, AnthropicProvider
from stock_ai.ai.dummy import DummyAIProvider
from stock_ai.ai.factory import get_ai_provider
from stock_ai.config.settings import Settings
from stock_ai.core.exceptions import AIError


def _settings() -> Settings:
    return Settings(_env_file=None)


# --- dummy + factory -------------------------------------------------------


def test_dummy_echoes_prompt() -> None:
    provider = DummyAIProvider()
    out = provider.complete("Summarize this earnings report.")
    assert "Summarize this earnings report." in out
    assert out.startswith("[dummy]")


def test_factory_returns_dummy() -> None:
    assert isinstance(get_ai_provider("dummy", _settings()), DummyAIProvider)


def test_factory_returns_anthropic_for_aliases() -> None:
    for name in ("anthropic", "Claude"):
        assert isinstance(get_ai_provider(name, _settings()), AnthropicProvider)


def test_factory_unknown_raises() -> None:
    with pytest.raises(AIError):
        get_ai_provider("bogus", _settings())


# --- Anthropic provider with an injected fake client -----------------------


@dataclass
class _Block:
    type: str
    text: str = ""


@dataclass
class _Response:
    content: list[_Block]
    stop_reason: str = "end_turn"


@dataclass
class _FakeMessages:
    response: _Response
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create(self, **kwargs: Any) -> _Response:
        self.calls.append(kwargs)
        return self.response


@dataclass
class _FakeClient:
    messages: _FakeMessages


def _client(response: _Response) -> _FakeClient:
    return _FakeClient(messages=_FakeMessages(response=response))


def test_anthropic_returns_joined_text() -> None:
    client = _client(_Response(content=[_Block("text", "Hello "), _Block("text", "world")]))
    provider = AnthropicProvider(client=client)
    assert provider.complete("hi") == "Hello world"
    assert client.messages.calls[0]["model"] == DEFAULT_MODEL


def test_anthropic_passes_system_only_when_set() -> None:
    client = _client(_Response(content=[_Block("text", "ok")]))
    provider = AnthropicProvider(client=client)
    provider.complete("hi", system="You are terse.")
    assert client.messages.calls[0]["system"] == "You are terse."

    client2 = _client(_Response(content=[_Block("text", "ok")]))
    AnthropicProvider(client=client2).complete("hi")
    assert "system" not in client2.messages.calls[0]


def test_anthropic_ignores_non_text_blocks() -> None:
    client = _client(_Response(content=[_Block("thinking", "..."), _Block("text", "answer")]))
    assert AnthropicProvider(client=client).complete("hi") == "answer"


def test_anthropic_refusal_raises() -> None:
    client = _client(_Response(content=[], stop_reason="refusal"))
    with pytest.raises(AIError, match="refused"):
        AnthropicProvider(client=client).complete("hi")


def test_anthropic_empty_text_raises() -> None:
    client = _client(_Response(content=[_Block("thinking", "...")]))
    with pytest.raises(AIError, match="no text"):
        AnthropicProvider(client=client).complete("hi")


def test_anthropic_wraps_sdk_errors() -> None:
    class _Boom:
        def create(self, **_: Any) -> None:
            raise RuntimeError("network down")

    provider = AnthropicProvider(client=_FakeClient(messages=_Boom()))
    with pytest.raises(AIError, match="request failed"):
        provider.complete("hi")
