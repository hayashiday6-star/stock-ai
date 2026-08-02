"""Anthropic (Claude) implementation of :class:`~stock_ai.ai.base.AIProvider`.

Uses the official ``anthropic`` SDK, imported lazily so the dependency is only
required when this provider is actually used. The SDK client is injectable for
testing without network access.
"""

from __future__ import annotations

from typing import Any

from pydantic import SecretStr

from stock_ai.core.exceptions import AIError
from stock_ai.core.logging import get_logger

logger = get_logger(__name__)

DEFAULT_MODEL = "claude-opus-5"


class AnthropicProvider:
    """Generate completions with Claude via the Anthropic Messages API."""

    name = "anthropic"

    def __init__(
        self,
        api_key: SecretStr | None = None,
        model: str = DEFAULT_MODEL,
        client: Any = None,
    ) -> None:
        """Create the provider.

        Args:
            api_key: Anthropic API key; falls back to the SDK's env resolution.
            model: Model ID (defaults to the latest Opus).
            client: Pre-built SDK client; injected in tests to avoid the network.
        """
        self._api_key = api_key
        self._model = model
        self._client = client

    def _get_client(self) -> Any:
        """Return the SDK client, constructing it lazily on first use."""
        if self._client is None:
            import anthropic

            key = self._api_key.get_secret_value() if self._api_key else None
            self._client = anthropic.Anthropic(api_key=key)
        return self._client

    def complete(self, prompt: str, *, system: str | None = None, max_tokens: int = 1024) -> str:
        """Return Claude's text response to ``prompt``."""
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system is not None:
            kwargs["system"] = system

        try:
            response = self._get_client().messages.create(**kwargs)
        except Exception as exc:  # normalize any SDK/transport error
            raise AIError(f"Anthropic request failed: {exc}") from exc

        if getattr(response, "stop_reason", None) == "refusal":
            raise AIError("Claude refused the request.")

        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
        if not text:
            raise AIError("Claude returned no text content.")
        return text
