"""OpenAI implementation of :class:`~stock_ai.ai.base.AIProvider`.

Uses the official ``openai`` SDK, imported lazily. The client is injectable for
testing without network access.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import SecretStr

from stock_ai.core.exceptions import AIError
from stock_ai.core.logging import get_logger

logger = get_logger(__name__)

DEFAULT_MODEL = "gpt-4o"


class OpenAIProvider:
    """Generate completions with OpenAI chat models."""

    name = "openai"

    def __init__(
        self,
        api_key: SecretStr | None = None,
        model: str = DEFAULT_MODEL,
        client: Any = None,
    ) -> None:
        """Create the provider (see :class:`AnthropicProvider` for arg semantics)."""
        self._api_key = api_key
        self._model = model
        self._client = client

    def _get_client(self) -> Any:
        """Return the SDK client, constructing it lazily on first use."""
        if self._client is None:
            from openai import OpenAI

            key = self._api_key.get_secret_value() if self._api_key else None
            self._client = OpenAI(api_key=key)
        return self._client

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        prefill: str | None = None,
        stop_sequences: Sequence[str] | None = None,
    ) -> str:
        """Return the model's text response to ``prompt``.

        ``prefill`` is accepted and ignored: this API has no equivalent, and
        silently dropping it is better than refusing a call the caller only
        meant to constrain.
        """
        messages: list[dict[str, str]] = []
        if system is not None:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        try:
            response = self._get_client().chat.completions.create(
                model=self._model, max_tokens=max_tokens, messages=messages
            )
        except Exception as exc:
            raise AIError(f"OpenAI request failed: {exc}") from exc

        text = response.choices[0].message.content
        if not text:
            raise AIError("OpenAI returned no text content.")
        return text
