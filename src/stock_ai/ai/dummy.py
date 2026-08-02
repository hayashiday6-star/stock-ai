"""A no-network AI provider for offline development and tests."""

from __future__ import annotations


class DummyAIProvider:
    """Echoes a deterministic, truncated transformation of the prompt.

    Useful for wiring up the pipeline and testing analysis code without an API
    key or network access.
    """

    name = "dummy"

    def __init__(self, prefix: str = "[dummy]") -> None:
        """Store the response prefix."""
        self.prefix = prefix

    def complete(self, prompt: str, *, system: str | None = None, max_tokens: int = 1024) -> str:
        """Return a deterministic echo of ``prompt`` (system is ignored)."""
        snippet = prompt.strip().replace("\n", " ")[:200]
        return f"{self.prefix} {snippet}"
