"""A notifier that prints to the console — the safe offline default."""

from __future__ import annotations

from rich.console import Console

_console = Console()


class ConsoleNotifier:
    """Print the message locally instead of sending it anywhere."""

    name = "console"

    def send(self, message: str) -> None:
        """Print ``message`` to stdout."""
        _console.print(f"[bold]notify[/] {message}")
