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


def test_a_one_word_rating_is_bound_by_a_prefill_not_by_asking_nicely() -> None:
    """Asking in prose for one word was not binding, and the ceiling was blamed.

    On live data the ratings averaged ~110 output tokens each and two of
    nineteen came back with no text at all, one of them after exhausting a
    512-token ceiling that had already been raised from 8 to 64 chasing the
    same symptom. Raising it again would have been the third guess at a cause
    that was never the ceiling: the request did not constrain the answer, so no
    ceiling was ever going to be high enough.
    """
    from stock_ai.ai.analysis import ONE_WORD_PREFILL, classify_importance
    from stock_ai.data.types import Importance

    seen: dict[str, object] = {}

    class _Recording:
        name = "recording"

        def complete(self, prompt: str, **kwargs: object) -> str:
            seen.update(kwargs)
            return " high"

    assert classify_importance(_Recording(), "臨時報告書") is Importance.HIGH
    assert seen["prefill"] == ONE_WORD_PREFILL
    assert "\n" in (seen["stop_sequences"] or ())


def test_the_prefill_becomes_an_assistant_turn_the_reply_must_continue() -> None:
    """A prefill only binds if it reaches the API as the start of the answer."""
    from stock_ai.ai.anthropic_provider import AnthropicProvider

    captured: dict[str, object] = {}

    class _Block:
        type = "text"
        text = "high"

    class _Response:
        content = [_Block()]
        stop_reason = "stop_sequence"
        usage = None

    class _Messages:
        def create(self, **kwargs: object) -> _Response:
            captured.update(kwargs)
            return _Response()

    class _Client:
        messages = _Messages()

    provider = AnthropicProvider(client=_Client())
    assert provider.complete("rate this", prefill="The answer is:", stop_sequences=["\n"]) == "high"

    messages = captured["messages"]
    assert messages[-1] == {"role": "assistant", "content": "The answer is:"}
    assert captured["stop_sequences"] == ["\n"]


def test_a_prefill_is_trimmed_because_the_api_rejects_trailing_space() -> None:
    """Callers write these as ordinary strings; the API will not take one."""
    from stock_ai.ai.anthropic_provider import AnthropicProvider

    captured: dict[str, object] = {}

    class _Block:
        type = "text"
        text = "low"

    class _Response:
        content = [_Block()]
        stop_reason = "end_turn"
        usage = None

    class _Messages:
        def create(self, **kwargs: object) -> _Response:
            captured.update(kwargs)
            return _Response()

    class _Client:
        messages = _Messages()

    AnthropicProvider(client=_Client()).complete("x", prefill="The answer is: ")
    assert captured["messages"][-1]["content"] == "The answer is:"
    # No stop sequences given, so none are sent rather than an empty list.
    assert "stop_sequences" not in captured
