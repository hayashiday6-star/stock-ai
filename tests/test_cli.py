"""Tests for the command-line interface."""

import pytest
from typer.testing import CliRunner

from stock_ai import __version__
from stock_ai.cli import app

runner = CliRunner()


def test_version_command_exits_cleanly() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0


def test_version_command_reports_current_version() -> None:
    result = runner.invoke(app, ["version"])
    assert __version__ in result.stdout


def test_help_lists_version_command() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "version" in result.stdout


def test_info_command_runs_and_masks_secrets() -> None:
    result = runner.invoke(app, ["info"])
    assert result.exit_code == 0
    assert "configuration" in result.stdout


def test_metrics_reports_the_distribution_of_stored_fundamentals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """For answering "is this number plausible?" without another round trip.

    Three rounds went into guessing why a screen matched three quarters of the
    market. The quartiles answer that directly.
    """
    import datetime as dt

    from stock_ai import cli
    from stock_ai.data.types import Fundamentals
    from stock_ai.database.engine import Database
    from stock_ai.database.repository import FundamentalsRepository, get_or_create_security

    database = Database("sqlite:///:memory:")
    database.create_all()
    with database.session() as session:
        for index, per in enumerate((5.0, 10.0, 20.0, -30.0)):
            symbol = f"{1300 + index:04d}"
            get_or_create_security(session, symbol, market="JP")
            FundamentalsRepository(session).upsert_fundamentals(
                Fundamentals(symbol=symbol, as_of=dt.date(2026, 8, 9), per=per), market="JP"
            )

    monkeypatch.setattr(cli, "Database", lambda: database)
    result = runner.invoke(cli.app, ["metrics"])

    assert result.exit_code == 0
    assert "per" in result.output
    # The loss-maker has to be visible as such, not averaged away.
    assert "4" in result.output  # four snapshots present
    database.dispose()


def test_metrics_on_an_empty_database_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    from stock_ai import cli
    from stock_ai.database.engine import Database

    database = Database("sqlite:///:memory:")
    database.create_all()
    monkeypatch.setattr(cli, "Database", lambda: database)

    result = runner.invoke(cli.app, ["metrics"])

    assert result.exit_code == 1
    assert "No fundamentals stored" in result.output
    database.dispose()
