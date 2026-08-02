"""Provider-agnostic notification interface.

A :class:`Notifier` delivers a text message to one channel. Callers depend on
this interface, so channels (Discord, Telegram, LINE, console) are swappable.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Notifier(Protocol):
    """Delivers a text message to a channel."""

    name: str

    def send(self, message: str) -> None:
        """Deliver ``message``.

        Raises:
            NotificationError: If the channel is misconfigured or delivery fails.
        """
        ...
