"""Command-line interface for stock-ai.

Exposes the ``stock-ai`` console script. Subcommands for each pipeline stage
(``fetch``, ``screen``, ``backtest`` ...) are added in later phases.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from stock_ai import __version__
from stock_ai.config.settings import Settings, get_settings
from stock_ai.core.logging import configure_logging

app = typer.Typer(
    name="stock-ai",
    help="AI-driven stock screening, backtesting, and trading system.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


@app.callback()
def _root() -> None:
    """Group root: forces multi-command mode so subcommands keep their names."""


@app.command()
def version() -> None:
    """Print the installed stock-ai version."""
    console.print(f"stock-ai [bold cyan]v{__version__}[/]")


@app.command()
def info() -> None:
    """Show the active configuration (secrets are masked, never printed)."""
    settings = get_settings()
    configure_logging(settings.log_level)

    table = Table(title="stock-ai configuration")
    table.add_column("Key", style="cyan", no_wrap=True)
    table.add_column("Value", style="white")
    table.add_row("version", __version__)
    table.add_row("env", settings.env)
    table.add_row("log_level", settings.log_level)
    for label, is_set in _secret_status(settings):
        table.add_row(label, "[green]set[/]" if is_set else "[dim]—[/]")
    console.print(table)


def _secret_status(settings: Settings) -> list[tuple[str, bool]]:
    """Return ``(label, is_set)`` pairs for each secret without exposing values."""
    return [
        ("jquants_api_key", settings.jquants_api_key is not None),
        ("anthropic_api_key", settings.anthropic_api_key is not None),
        ("openai_api_key", settings.openai_api_key is not None),
        ("gemini_api_key", settings.gemini_api_key is not None),
        ("discord_webhook_url", settings.discord_webhook_url is not None),
        ("line_channel_access_token", settings.line_channel_access_token is not None),
        ("telegram_bot_token", settings.telegram_bot_token is not None),
    ]


def main() -> None:
    """Entry point for the ``stock-ai`` console script."""
    app()


if __name__ == "__main__":
    main()
