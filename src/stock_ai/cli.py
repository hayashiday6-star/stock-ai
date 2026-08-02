"""Command-line interface for stock-ai.

Exposes the ``stock-ai`` console script. Subcommands for each pipeline stage
are added as the phases progress.
"""

from __future__ import annotations

import datetime as dt

import typer
from rich.console import Console
from rich.table import Table

from stock_ai import __version__
from stock_ai.config.settings import Settings, get_settings
from stock_ai.core.logging import configure_logging
from stock_ai.data.service import IngestionService, IngestResult
from stock_ai.data.yfinance_provider import YFinancePriceProvider
from stock_ai.database.engine import Database

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


@app.command()
def fetch(
    symbols: list[str] = typer.Argument(..., help="Ticker symbols, e.g. AAPL MSFT"),
    start: str | None = typer.Option(None, help="ISO start date YYYY-MM-DD."),
    end: str | None = typer.Option(None, help="ISO end date; defaults to today."),
    lookback: int = typer.Option(365, help="Backfill days when a symbol has no data."),
) -> None:
    """Fetch daily prices for SYMBOLS and store them in the local database."""
    settings = get_settings()
    configure_logging(settings.log_level)

    database = Database()
    database.create_all()
    service = IngestionService(YFinancePriceProvider(), database, default_lookback_days=lookback)

    results = service.ingest_many(symbols, _parse_date(start), _parse_date(end))
    _render_results(results)

    if any(not r.ok for r in results):
        raise typer.Exit(code=1)


def _parse_date(value: str | None) -> dt.date | None:
    """Parse an ISO date string, or return ``None`` when unset."""
    if value is None:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter(f"Invalid date {value!r}; use YYYY-MM-DD.") from exc


def _render_results(results: list[IngestResult]) -> None:
    """Print an ingestion summary table."""
    table = Table(title="fetch results")
    table.add_column("Symbol", style="cyan")
    table.add_column("Rows", justify="right")
    table.add_column("Status")
    for result in results:
        status = "[green]ok[/]" if result.ok else f"[red]error[/] {result.error or ''}"
        table.add_row(result.symbol, str(result.rows), status)
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
