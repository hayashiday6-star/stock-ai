"""Provider-agnostic interface for text-completion AI providers.

Downstream analysis code (news/IR summaries, sentiment) depends only on this
interface, so the concrete provider (Claude, OpenAI, Gemini, or a dummy) can be
swapped via configuration or dependency injection.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable


@runtime_checkable
class AIProvider(Protocol):
    """Turns a prompt into a text completion."""

    name: str

    #: Whether this provider only imitates a model. Callers that persist a
    #: verdict must not persist a stub's, since a recorded verdict is never
    #: revisited and would hide the disclosure from every later real run.
    is_stub: bool = False

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

        Args:
            prompt: The user prompt.
            system: Optional system instruction.
            max_tokens: Maximum tokens to generate.
            prefill: Text to put in the model's mouth, so the reply continues
                it rather than starting freely. A one-word answer asked for in
                prose is a request the model may decline in favour of
                explaining itself; started mid-sentence it has nowhere else to
                go. Providers without the concept ignore it.
            stop_sequences: Strings that end generation as soon as they appear.

        Returns:
            The completion text.

        Raises:
            AIError: On provider failure, misconfiguration, or refusal.
        """
        ...
