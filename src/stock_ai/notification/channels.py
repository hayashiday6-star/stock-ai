"""HTTP-based notifiers for Discord, Telegram, and LINE.

Each wraps an injectable HTTP client (an ``httpx.Client`` by default) so the
POST can be faked in tests. Any transport or HTTP error is normalized to
:class:`~stock_ai.core.exceptions.NotificationError`.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from pydantic import SecretStr

from stock_ai.core.exceptions import NotificationError
from stock_ai.core.logging import get_logger

logger = get_logger(__name__)

_TIMEOUT = 10.0


def _reveal(value: SecretStr | str | None) -> str | None:
    """Return the plain string of a secret/str, or ``None``."""
    if value is None:
        return None
    return value.get_secret_value() if isinstance(value, SecretStr) else value


def _safe_url(url: str) -> str:
    """Return ``url`` reduced to ``scheme://host`` for use in messages.

    Every channel here carries its credential in the URL path - a Telegram bot
    token (``/bot<token>/sendMessage``) or a Discord webhook id and token - so
    the full URL must never reach an exception message, a log line, or the
    console. Only the host survives, which is enough to tell channels apart.
    """
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return "<redacted>"
    return f"{parts.scheme}://{parts.netloc}/…"


def _scrub(text: str, url: str) -> str:
    """Strip ``url`` and its credential-bearing path out of ``text``.

    The path is removed separately from the whole URL: a transport error may
    quote the target in a form that does not match ``url`` byte for byte (a
    redirect, an appended query), and it is the path that carries the secret.
    """
    cleaned = text.replace(url, _safe_url(url))
    path = urlsplit(url).path
    if len(path) > 1:
        cleaned = cleaned.replace(path, "/…")
    return cleaned


def _post_json(
    client: Any, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None
) -> None:
    """POST ``payload`` as JSON, raising ``NotificationError`` on any failure."""
    safe = _safe_url(url)
    try:
        response = client.post(url, json=payload, headers=headers or {}, timeout=_TIMEOUT)
        response.raise_for_status()
    except Exception as exc:
        # httpx quotes the full request URL in its own error text, so scrubbing
        # only our prefix is not enough - the credential rides in the path.
        detail = _scrub(str(exc), url)
        # ``from None``: the chained httpx traceback would reprint the raw URL.
        raise NotificationError(f"POST {safe} failed: {detail}") from None


def _default_client() -> Any:
    """Return a new httpx client (imported lazily)."""
    import httpx

    return httpx.Client()


def split_message(message: str, limit: int) -> list[str]:
    """Split ``message`` into pieces no longer than ``limit`` characters.

    Every chat service caps a single message, and a monitor run has no say in
    how many alerts a day produces: a quiet day is one short message and a busy
    one is far past any cap. Sending the lot as one string turns a *good* day -
    lots to report - into a delivery failure, which is precisely backwards.

    Splits on blank lines first, since alerts are separated by one, then on
    single lines, and only cuts mid-line for a line longer than the limit on
    its own. The point is that a reader never sees an alert torn in half
    because the one before it happened to be long.
    """
    if limit <= 0:
        raise ValueError("limit must be positive")
    if len(message) <= limit:
        return [message] if message else []

    chunks: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current:
            chunks.append(current)
            current = ""

    for block in message.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= limit:
            current = candidate
            continue
        flush()
        if len(block) <= limit:
            current = block
            continue
        # One alert alone is over the cap: fall back to lines, then to a hard
        # cut, so something still arrives rather than nothing.
        for line in block.split("\n"):
            candidate = f"{current}\n{line}" if current else line
            if len(candidate) <= limit:
                current = candidate
                continue
            flush()
            while len(line) > limit:
                chunks.append(line[:limit])
                line = line[limit:]
            current = line
    flush()
    return chunks


class DiscordNotifier:
    """Post a message to a Discord channel via an incoming webhook."""

    name = "discord"

    #: Discord rejects a webhook payload whose ``content`` exceeds this, with a
    #: 400 that names no field. Measured against the API, not guessed.
    MAX_CHARS = 2000

    def __init__(self, webhook_url: SecretStr | str | None, client: Any = None) -> None:
        """Store the webhook URL and optional injected HTTP client."""
        self._webhook_url = webhook_url
        self._client = client

    def send(self, message: str) -> None:
        """Deliver ``message`` to the configured Discord webhook, in parts if needed."""
        url = _reveal(self._webhook_url)
        if not url:
            raise NotificationError("Discord webhook URL is not configured.")
        client = self._client or _default_client()
        parts = split_message(message, self.MAX_CHARS)
        for part in parts:
            _post_json(client, url, {"content": part})
        logger.info(
            "Sent Discord notification (%d chars in %d message(s))", len(message), len(parts)
        )


class TelegramNotifier:
    """Send a message via the Telegram Bot API."""

    name = "telegram"

    #: Telegram's documented limit for ``sendMessage`` text.
    MAX_CHARS = 4096

    def __init__(
        self,
        bot_token: SecretStr | str | None,
        chat_id: SecretStr | str | None,
        client: Any = None,
    ) -> None:
        """Store the bot token, chat id, and optional injected HTTP client."""
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._client = client

    def send(self, message: str) -> None:
        """Deliver ``message`` to the configured Telegram chat."""
        token = _reveal(self._bot_token)
        chat_id = _reveal(self._chat_id)
        if not token or not chat_id:
            raise NotificationError("Telegram bot token or chat id is not configured.")
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        client = self._client or _default_client()
        parts = split_message(message, self.MAX_CHARS)
        for part in parts:
            _post_json(client, url, {"chat_id": chat_id, "text": part})
        logger.info(
            "Sent Telegram notification (%d chars in %d message(s))", len(message), len(parts)
        )


class LineNotifier:
    """Broadcast a message via the LINE Messaging API."""

    name = "line"

    #: LINE's documented limit for a text message.
    MAX_CHARS = 5000

    _URL = "https://api.line.me/v2/bot/message/broadcast"

    def __init__(self, access_token: SecretStr | str | None, client: Any = None) -> None:
        """Store the channel access token and optional injected HTTP client."""
        self._access_token = access_token
        self._client = client

    def send(self, message: str) -> None:
        """Broadcast ``message`` to the LINE official account's followers."""
        token = _reveal(self._access_token)
        if not token:
            raise NotificationError("LINE access token is not configured.")
        headers = {"Authorization": f"Bearer {token}"}
        client = self._client or _default_client()
        parts = split_message(message, self.MAX_CHARS)
        for part in parts:
            payload = {"messages": [{"type": "text", "text": part}]}
            _post_json(client, self._URL, payload, headers=headers)
        logger.info("Sent LINE notification (%d chars in %d message(s))", len(message), len(parts))
