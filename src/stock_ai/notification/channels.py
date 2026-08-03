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

    Every channel here carries its credential in the URL path — a Telegram bot
    token (``/bot<token>/sendMessage``) or a Discord webhook id and token — so
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
        # only our prefix is not enough — the credential rides in the path.
        detail = _scrub(str(exc), url)
        # ``from None``: the chained httpx traceback would reprint the raw URL.
        raise NotificationError(f"POST {safe} failed: {detail}") from None


def _default_client() -> Any:
    """Return a new httpx client (imported lazily)."""
    import httpx

    return httpx.Client()


class DiscordNotifier:
    """Post a message to a Discord channel via an incoming webhook."""

    name = "discord"

    def __init__(self, webhook_url: SecretStr | str | None, client: Any = None) -> None:
        """Store the webhook URL and optional injected HTTP client."""
        self._webhook_url = webhook_url
        self._client = client

    def send(self, message: str) -> None:
        """Deliver ``message`` to the configured Discord webhook."""
        url = _reveal(self._webhook_url)
        if not url:
            raise NotificationError("Discord webhook URL is not configured.")
        _post_json(self._client or _default_client(), url, {"content": message})
        logger.info("Sent Discord notification (%d chars)", len(message))


class TelegramNotifier:
    """Send a message via the Telegram Bot API."""

    name = "telegram"

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
        _post_json(self._client or _default_client(), url, {"chat_id": chat_id, "text": message})
        logger.info("Sent Telegram notification (%d chars)", len(message))


class LineNotifier:
    """Broadcast a message via the LINE Messaging API."""

    name = "line"

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
        payload = {"messages": [{"type": "text", "text": message}]}
        _post_json(self._client or _default_client(), self._URL, payload, headers=headers)
        logger.info("Sent LINE notification (%d chars)", len(message))
