"""Anthropic (Claude) implementation of :class:`~stock_ai.ai.base.AIProvider`.

Uses the official ``anthropic`` SDK, imported lazily so the dependency is only
required when this provider is actually used. The SDK client is injectable for
testing without network access.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import SecretStr

from stock_ai.ai.pricing import Usage, UsageLedger
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
        #: Tokens consumed by the most recent call, or ``None`` before the
        #: first one. Recorded because a run that bills per disclosure has no
        #: other way to say afterwards what it actually spent.
        self.last_usage: Usage | None = None
        #: Every call this provider has made, added up. ``last_usage`` answers
        #: "what did that cost"; a monitor run makes one call per disclosure
        #: plus one per summary, and only the running total answers "what did
        #: *this run* cost" - the figure the pre-run estimate is checked
        #: against.
        self.usage = UsageLedger()

    def _get_client(self) -> Any:
        """Return the SDK client, constructing it lazily on first use.

        A missing package and a rejected key are different problems with
        different fixes, and the SDK's ``ModuleNotFoundError`` says nothing
        about which. Naming the install here is what stops the caller being
        sent to check a key that was correct all along.
        """
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:  # the dependency is an opt-in extra
                raise AIError(
                    "The 'anthropic' package is not installed. This is an "
                    "optional extra, so it is absent until asked for: run "
                    "'uv sync --extra ai'. Your API key is not the problem."
                ) from exc

            key = self._api_key.get_secret_value() if self._api_key else None
            self._client = anthropic.Anthropic(api_key=key)
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
        """Return Claude's text response to ``prompt``.

        ``prefill`` becomes a leading assistant turn, so the reply continues it
        instead of starting freely. Asking in prose for one word is a request
        the model can decline in favour of explaining itself - and an answer
        that runs long is not merely wasteful here: it can exhaust
        ``max_tokens`` and come back with no text at all, which reads as a
        provider failure rather than a prompt that was never binding.
        """
        messages: list[dict[str, str]] = [{"role": "user", "content": prompt}]
        if prefill:
            # The API rejects a prefill with trailing whitespace, and callers
            # write these as ordinary strings.
            messages.append({"role": "assistant", "content": prefill.rstrip()})

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system is not None:
            kwargs["system"] = system
        if stop_sequences:
            kwargs["stop_sequences"] = list(stop_sequences)

        try:
            response = self._get_client().messages.create(**kwargs)
        except Exception as exc:  # normalize any SDK/transport error
            raise AIError(f"Anthropic request failed: {exc}") from exc

        usage = getattr(response, "usage", None)
        if usage is not None:
            self.last_usage = Usage(
                input_tokens=getattr(usage, "input_tokens", 0),
                output_tokens=getattr(usage, "output_tokens", 0),
                model=self._model,
            )
            self.usage.record(self.last_usage)
            logger.debug(
                "Anthropic call: %d in, %d out",
                self.last_usage.input_tokens,
                self.last_usage.output_tokens,
            )

        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason == "refusal":
            raise AIError("Claude refused the request.")

        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
        if not text:
            # Observed live: a 200 response with an empty content list, from a
            # call whose max_tokens was 8. "Claude returned no text content"
            # sent the reader looking at the key and the model name, when the
            # cause was a ceiling this code set itself. The stop reason is in
            # the response the whole time, so it goes in the message.
            if stop_reason == "max_tokens":
                raise AIError(
                    f"Claude produced nothing within max_tokens={max_tokens}. "
                    "The ceiling is too low for this model, not a key or "
                    "network problem - raise it at the call site."
                )
            raise AIError(f"Claude returned no text content (stop_reason={stop_reason!r}).")
        return text

    @property
    def model(self) -> str:
        """The model ID this provider calls, needed to price a call."""
        return self._model

    def count_tokens(self, prompt: str, *, system: str | None = None) -> int:
        """Return the exact input-token count for a request, without sending it.

        ``count_tokens`` is a separate endpoint that does no generation and is
        not billed, which is what makes a pre-run estimate exact on the input
        side rather than a characters-divided-by-four guess.
        """
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system is not None:
            kwargs["system"] = system
        client = self._get_client()  # raises AIError with its own advice
        try:
            return int(client.messages.count_tokens(**kwargs).input_tokens)
        except Exception as exc:
            raise AIError(f"Anthropic token count failed: {exc}") from exc
