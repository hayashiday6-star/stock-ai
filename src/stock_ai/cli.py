"""Command-line interface for stock-ai.

Exposes the ``stock-ai`` console script. Subcommands for each pipeline stage
(``fetch``, ``screen``, ``backtest`` ...) are added in later phases; for now
only ``version`` exists so that "Python starts" can be verified end to end.
"""

from __future__ import annotations

import typer
from rich.console import Console

from stock_ai import __version__

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


def main() -> None:
    """Entry point for the ``stock-ai`` console script."""
    app()


if __name__ == "__main__":
    main()
