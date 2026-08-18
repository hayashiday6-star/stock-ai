"""Select an AI provider by name, wiring in configured credentials."""

from __future__ import annotations

from stock_ai.ai.anthropic_provider import DEFAULT_MODEL, AnthropicProvider
from stock_ai.ai.base import AIProvider
from stock_ai.ai.dummy import DummyAIProvider
from stock_ai.ai.gemini_provider import GeminiProvider
from stock_ai.ai.openai_provider import OpenAIProvider
from stock_ai.config.settings import Settings

_ANTHROPIC_ALIASES = {"anthropic", "claude"}
_OPENAI_ALIASES = {"openai", "gpt"}
_GEMINI_ALIASES = {"gemini", "google"}


def get_ai_provider(name: str, settings: Settings) -> AIProvider:
    """Return an :class:`AIProvider` for ``name``.

    Args:
        name: Provider selector (``dummy``, ``anthropic``/``claude``, ...).
        settings: Application settings supplying API keys.

    Returns:
        A ready-to-use provider.

    Raises:
        AIError: If ``name`` is not a known provider.
    """
    key = name.lower()
    if key == "dummy":
        return DummyAIProvider()
    if key in _ANTHROPIC_ALIASES:
        return AnthropicProvider(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model or DEFAULT_MODEL,
        )
    if key in _OPENAI_ALIASES:
        return OpenAIProvider(api_key=settings.openai_api_key)
    if key in _GEMINI_ALIASES:
        return GeminiProvider(api_key=settings.gemini_api_key)

    from stock_ai.core.exceptions import AIError

    raise AIError(f"Unknown AI provider {name!r}.")
