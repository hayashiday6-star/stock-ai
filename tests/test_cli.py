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


def test_inspect_prints_the_raw_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shows the fields as they arrive, including ones we do not read.

    Every wrong number so far traced to a field named or meaning something other
    than expected. A renamed field shows as a blank among the known names and an
    unfamiliar entry in the "other fields" line.
    """
    from stock_ai import cli
    from stock_ai.data import jquants_fundamentals

    records = [
        {"DiscDate": "2025-05-12", "CurPerType": "FY", "Sales": 1000, "NP": 100, "EPS": 50.0},
        {"DiscDate": "2025-08-05", "CurPerType": "1Q", "Sales": 260, "NP": 26, "SomethingNew": 1},
    ]
    monkeypatch.setattr(jquants_fundamentals, "_default_fetcher", lambda _key: lambda _s: records)

    result = runner.invoke(cli.app, ["inspect", "6758"])

    assert result.exit_code == 0
    assert "CurPerType" in result.output
    assert "2 record(s)" in result.output
    # A field we never read must still be visible - that is the point.
    assert "SomethingNew" in result.output


def test_inspect_reports_an_empty_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    from stock_ai import cli
    from stock_ai.data import jquants_fundamentals

    monkeypatch.setattr(jquants_fundamentals, "_default_fetcher", lambda _key: lambda _s: [])

    result = runner.invoke(cli.app, ["inspect", "9999"])

    assert result.exit_code == 1
    assert "No statements returned" in result.output


def test_inspect_shows_the_fields_that_disambiguate_a_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DocType, the non-consolidated series, and the dividend spellings.

    A forecast revision and a results announcement share DiscDate and
    CurPerType; only DocType separates them. Leaving these out of the table is
    how a diagnostic tool fails to diagnose.
    """
    from stock_ai import cli
    from stock_ai.data import jquants_fundamentals

    records = [
        {
            "DiscDate": "2026-05-08",
            "DocType": "FYFinancialStatements_Consolidated_JP",
            "CurPerType": "FY",
            "NP": -326865000000,
            "EPS": -54.7,
            "ROE": -3.7,
            "NCNP": 500000000000,
            "DivTotalAnn": 25.0,
            "FNP": 900000000000,
        },
    ]
    monkeypatch.setattr(jquants_fundamentals, "_default_fetcher", lambda _key: lambda _s: records)

    output = runner.invoke(cli.app, ["inspect", "6758"]).output

    for field in ("DocType", "ROE", "NCNP", "DivTotalAnn", "FNP"):
        assert field in output, f"{field} must be visible in the table"


def test_an_ai_command_reports_what_it_spent(monkeypatch: pytest.MonkeyPatch) -> None:
    """The wiring, not the arithmetic: does 'summarize' reach the spend line?

    ``_report_spend`` is tested on its own, and the provider's ledger is tested
    on its own. Neither says the command calls the one with the other, which is
    the part that was actually missing.
    """
    from stock_ai.ai.anthropic_provider import AnthropicProvider

    class _Block:
        type = "text"
        text = "A short summary."

    class _Usage:
        input_tokens = 4_000
        output_tokens = 200

    class _Response:
        content = [_Block()]
        stop_reason = "end_turn"
        usage = _Usage()

    class _Messages:
        def create(self, **kwargs: object) -> _Response:
            return _Response()

    class _Client:
        messages = _Messages()

    provider = AnthropicProvider(client=_Client(), model="claude-opus-5")
    monkeypatch.setattr("stock_ai.cli.get_ai_provider", lambda name, settings: provider)

    result = runner.invoke(app, ["summarize", "Revenue rose 12%.", "--provider", "claude"])

    assert result.exit_code == 0
    assert "A short summary." in result.stdout
    assert "spent:" in result.stdout
    assert "1 call(s)" in result.stdout
    assert "4,000 in" in result.stdout
    # 4,000 * $5/M + 200 * $25/M = $0.025
    assert "$0.0250" in result.stdout


def test_a_failed_ai_call_still_reports_what_it_spent(monkeypatch: pytest.MonkeyPatch) -> None:
    """The first live failure of 'sentiment' spent tokens and reported nothing."""
    from stock_ai.ai.anthropic_provider import AnthropicProvider

    class _Usage:
        input_tokens = 200
        output_tokens = 8

    class _Response:
        content: list[object] = []
        stop_reason = "max_tokens"
        usage = _Usage()

    class _Messages:
        def create(self, **kwargs: object) -> _Response:
            return _Response()

    class _Client:
        messages = _Messages()

    provider = AnthropicProvider(client=_Client(), model="claude-opus-5")
    monkeypatch.setattr("stock_ai.cli.get_ai_provider", lambda name, settings: provider)

    result = runner.invoke(app, ["sentiment", "good news", "--provider", "claude"])

    assert result.exit_code != 0
    assert "spent:" in result.stdout
    assert "1 call(s)" in result.stdout


def test_report_numbers_are_rendered_at_a_readable_precision() -> None:
    """str(float) gives 17 digits, which wrapped a 344-row screen into a wall."""
    import pandas as pd

    from stock_ai.cli import _format_cell

    assert _format_cell("per", 10.037256562235394) == "10.037"
    assert _format_cell("roe", 0.10125) == "0.101"
    # Fourteen digits printed in full wrapped the column to five lines.
    assert _format_cell("market_cap", 43252245337710.0) == "43.25T"
    assert _format_cell("revenue", 166855000000.0) == "166.85B"
    assert _format_cell("net_income", 7117000.0) == "7.12M"
    assert _format_cell("net_income", -450000.0) == "-450,000"
    assert _format_cell("dividend_yield", float("nan")) == "-"
    assert _format_cell("symbol", "7203") == "7203"
    assert _format_cell("as_of", None) == ""
    # A missing value must never render as a number.
    assert _format_cell("revenue", pd.NA) in {"-", "<NA>"}


def test_the_monitor_takes_the_same_feed_flag_the_estimate_does() -> None:
    """Priced with --feed and run with --source is two different questions.

    The live check estimated 'all' (two disclosures) then ran 'edinet' (one),
    which reads exactly like an estimate that cannot be trusted.

    Read off the parameter definition rather than the rendered --help: Rich
    wraps to the terminal width, so an assertion on that text passes on a wide
    developer console and fails in CI on the same correct code. It also tests
    the wrong thing - what matters is which flags the command accepts.
    """
    import typer.main

    command = typer.main.get_command(app).commands["monitor"]  # type: ignore[attr-defined]
    flags = {opt for param in command.params for opt in param.opts}

    assert "--feed" in flags
    # --source stays as an alias so existing scripts keep working.
    assert "--source" in flags


def test_a_symbol_file_is_read_in_the_encodings_notepad_writes(tmp_path) -> None:
    """The expected way to make one of these is Notepad on Japanese Windows.

    It writes UTF-16 for "Unicode", UTF-8 with a BOM, and cp932 for "ANSI".
    Only the middle one survives a plain read_text, and rejecting a file whose
    contents are perfectly good is not a defensible failure.
    """
    from pathlib import Path

    from stock_ai.cli import _symbols_from_file

    body = "# 大型\nAAPL, MSFT, NVDA\nGOOGL   # Alphabet\n"
    expected = ["AAPL", "MSFT", "NVDA", "GOOGL"]

    for name, data in {
        "utf8.txt": body.encode("utf-8"),
        "bom.txt": b"\xef\xbb\xbf" + body.encode("utf-8"),
        "utf16.txt": body.encode("utf-16"),
        "cp932.txt": body.encode("cp932"),
        "crlf.txt": b"\xef\xbb\xbf" + body.replace("\n", "\r\n").encode("utf-8"),
    }.items():
        path = Path(tmp_path) / name
        path.write_bytes(data)
        assert _symbols_from_file(path) == expected, name


def test_an_unreadable_symbol_file_says_what_it_actually_held(tmp_path) -> None:
    """ "contained no symbols" is a conclusion, and leaves nothing to check."""
    from pathlib import Path

    import typer

    from stock_ai.cli import _symbols_from_file

    path = Path(tmp_path) / "empty.txt"
    path.write_bytes(b"")
    with pytest.raises(typer.BadParameter) as excinfo:
        _symbols_from_file(path)

    message = str(excinfo.value)
    assert "0 bytes" in message  # the fact that decides what to do next


def test_ai_commands_take_their_provider_from_configuration() -> None:
    """The estimate priced Claude while the run used the dummy.

    ``ai-cost`` read the configured model and ``monitor`` had ``dummy`` frozen
    into its signature, so the two commands disagreed about which provider the
    run was for. Nothing on screen said so: the dummy pass reports alerts,
    names symbols and bills nothing, which reads as a cheap run rather than a
    fake one. A ``None`` default is what lets configuration decide.
    """
    import typer.main

    command = typer.main.get_command(app)
    for name in ("monitor", "daily", "ask", "summarize", "sentiment"):
        params = {p.name: p for p in command.commands[name].params}
        if "provider" not in params:
            continue
        assert params["provider"].default is None, name


def test_the_provider_setting_is_read_from_the_environment(monkeypatch) -> None:
    """Configuring it once must reach every command, not just the dashboard."""
    from stock_ai.config.settings import Settings

    monkeypatch.setenv("AI_PROVIDER", "claude")
    assert Settings().ai_provider == "claude"
    monkeypatch.delenv("AI_PROVIDER")
    assert Settings().ai_provider == "dummy"
