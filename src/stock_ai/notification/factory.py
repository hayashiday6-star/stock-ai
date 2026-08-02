"""Select a notifier by channel name, wiring in configured credentials."""

from __future__ import annotations

from stock_ai.config.settings import Settings
from stock_ai.notification.base import Notifier
from stock_ai.notification.channels import (
    DiscordNotifier,
    LineNotifier,
    TelegramNotifier,
)
from stock_ai.notification.console import ConsoleNotifier


def get_notifier(channel: str, settings: Settings) -> Notifier:
    """Return a :class:`Notifier` for ``channel``.

    Args:
        channel: ``console``, ``discord``, ``telegram``, or ``line``.
        settings: Application settings supplying channel credentials.

    Returns:
        A ready-to-use notifier.

    Raises:
        NotificationError: If ``channel`` is unknown.
    """
    key = channel.lower()
    if key == "console":
        return ConsoleNotifier()
    if key == "discord":
        return DiscordNotifier(settings.discord_webhook_url)
    if key == "telegram":
        return TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    if key == "line":
        return LineNotifier(settings.line_channel_access_token)

    from stock_ai.core.exceptions import NotificationError

    raise NotificationError(f"Unknown notification channel {channel!r}.")
