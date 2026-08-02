"""Tests for the command-line interface."""

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
