"""Provider-agnostic interface for text-completion AI providers.

Downstream analysis code (news/IR summaries, sentiment) depends only on this
interface, so the concrete provider (Claude, OpenAI, Gemini, or a dummy) can be
swapped via configuration or dependency injection.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class AIProvider(Protocol):
    """Turns a prompt into a text completion."""

    name: str

    def complete(self, prompt: str, *, system: str | None = None, max_tokens: int = 1024) -> str:
        """Return the model's text response to ``prompt``.

        Args:
            prompt: The user prompt.
            system: Optional system instruction.
            max_tokens: Maximum tokens to generate.

        Returns:
            The completion text.

        Raises:
            AIError: On provider failure, misconfiguration, or refusal.
        """
        ...
