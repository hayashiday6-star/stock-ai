"""Google Gemini implementation of :class:`~stock_ai.ai.base.AIProvider`.

Uses the official ``google-generativeai`` SDK, imported lazily. The underlying
model object is injectable for testing without network access.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import SecretStr

from stock_ai.core.exceptions import AIError
from stock_ai.core.logging import get_logger

logger = get_logger(__name__)

DEFAULT_MODEL = "gemini-1.5-pro"


class GeminiProvider:
    """Generate completions with Google Gemini models."""

    name = "gemini"

    def __init__(
        self,
        api_key: SecretStr | None = None,
        model: str = DEFAULT_MODEL,
        client: Any = None,
    ) -> None:
        """Create the provider.

        Args:
            api_key: Gemini API key.
            model: Model ID.
            client: A pre-built model object exposing ``generate_content``;
                injected in tests to avoid the network.
        """
        self._api_key = api_key
        self._model = model
        self._client = client

    def _get_client(self) -> Any:
        """Return the SDK model object, constructing it lazily on first use."""
        if self._client is None:
            import google.generativeai as genai

            if self._api_key is not None:
                genai.configure(api_key=self._api_key.get_secret_value())
            self._client = genai.GenerativeModel(self._model)
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

        ``prefill`` is accepted and ignored; this API has no equivalent.

        The Gemini SDK has no separate system field here, so ``system`` is
        prepended to the prompt.
        """
        content = prompt if system is None else f"{system}\n\n{prompt}"
        try:
            response = self._get_client().generate_content(content)
        except Exception as exc:
            raise AIError(f"Gemini request failed: {exc}") from exc

        text = getattr(response, "text", None)
        if not text:
            raise AIError("Gemini returned no text content.")
        return text
