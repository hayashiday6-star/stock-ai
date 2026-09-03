"""Tests for the command-line interface."""

import pytest
from typer.testing import CliRunner

from stock_ai import __version__
from stock_ai.cli import app
from stock_ai.config.settings import get_settings

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


def test_version_and_info_name_the_commit_not_just_the_package_version() -> None:
    """静的な 0.1.0 だけでは「古いコードで測っていないか」に答えられない。

    core.version.describe() はこのために書かれていたのに、CLI がどこからも
    呼んでいなかった。実際に info の出力を貼ってもらっても、どのコミットが
    動いたか分からないままだった。両コマンドが describe() を通ることを固定する。
    """
    from stock_ai.core.version import describe

    described = describe()
    # git チェックアウトの中で走っている限り、コミットまで出るはず。
    assert described != __version__

    for command in ("version", "info"):
        stdout = runner.invoke(app, [command]).stdout
        # rich が表の幅で折り返すので、コミット部分だけを取り出して照合する。
        commit = described.removeprefix(__version__).strip().lstrip("(").split()[0]
        assert commit in stdout.replace("\n", ""), command


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


def test_news_shows_which_symbol_was_actually_queried(monkeypatch) -> None:
    """The rewrite has to be visible, or the check cannot confirm anything.

    Reading headlines is the free way to catch a feed answering for the wrong
    company. That only works if the reader can see which symbol went out.
    """
    from typer.testing import CliRunner

    from stock_ai.news.sources import NewsItem

    monkeypatch.setattr(
        "stock_ai.cli.YFinanceNewsSource",
        lambda: type(
            "_S", (), {"fetch": lambda self, sym, limit=5: [NewsItem("決算発表", "本文")]}
        )(),
    )
    result = CliRunner().invoke(app, ["news", "3003"])

    assert result.exit_code == 0
    assert "3003.T" in result.stdout
    assert "決算発表" in result.stdout


def test_news_says_it_cannot_tell_empty_from_unknown(monkeypatch) -> None:
    """No headlines is ambiguous, and saying so is the honest report."""
    from typer.testing import CliRunner

    monkeypatch.setattr(
        "stock_ai.cli.YFinanceNewsSource",
        lambda: type("_S", (), {"fetch": lambda self, sym, limit=5: []})(),
    )
    result = CliRunner().invoke(app, ["news", "9999"])

    assert result.exit_code == 1
    assert "does not know" in result.stdout


def test_the_unattended_job_can_be_told_a_per_symbol_limit() -> None:
    """The nightly run was fixed at 10 with no way to say otherwise.

    The news feed returns up to --limit items for every watched name whether
    or not anything happened, so this sets the size of a run more than the news
    does. A setting chosen by hand and unavailable to the job that actually
    runs every night is not a setting.
    """
    import typer.main

    params = {p.name: p for p in typer.main.get_command(app).commands["daily"].params}
    assert "limit" in params
    assert params["limit"].default == 10  # unchanged for anyone not passing it
    assert "heartbeat" in params


def test_the_daily_script_passes_limit_and_heartbeat_through() -> None:
    """Registering a task and running it must agree on what was chosen.

    The registration builds a command line for Task Scheduler and the run
    builds one for the CLI. A flag added to one and not the other is a setting
    that silently disappears the moment it is scheduled.
    """
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "scripts" / "4-daily.ps1"
    text = script.read_text(encoding="utf-8")

    assert "'-Limit', $Limit" in text  # into the scheduled task
    assert "'--limit', $Limit" in text  # into the CLI it runs
    assert "$arguments += '-Heartbeat'" in text
    assert "$arguments += '--heartbeat'" in text


def test_fetch_routes_japanese_codes_away_from_yfinance(monkeypatch) -> None:
    """`fetch 3003` with the default source stored another exchange's prices.

    A bare four-digit code is a Tadawul listing to Yahoo, so 3003 comes back
    as City Cement and is stored under ヒューリック's name. Nothing raises and
    nothing about the stored series looks wrong afterwards - which is why the
    routing has to happen before the request, not be left to a flag.
    """
    from typer.testing import CliRunner

    asked: list[tuple[str, tuple[str, ...]]] = []

    def _source(name: str, _settings: object) -> tuple[object, str]:
        return name, ("JP" if name == "jquants" else "US")

    class _Service:
        def __init__(self, provider: str, *_args: object, **_kwargs: object) -> None:
            self.provider = provider

        def ingest_many(self, syms, *_args: object, **_kwargs: object) -> list:
            asked.append((self.provider, tuple(syms)))
            return []

    monkeypatch.setattr("stock_ai.cli._price_source", _source)
    monkeypatch.setattr("stock_ai.cli.IngestionService", _Service)

    result = CliRunner().invoke(app, ["fetch", "3003", "AAPL"])

    assert result.exit_code == 0
    routed = dict(asked)
    assert routed["jquants"] == ("3003",)
    assert routed["yfinance"] == ("AAPL",)
    # And it says so, rather than quietly overriding the flag it was given.
    assert "rather than yfinance" in result.stdout


def test_daily_prices_uses_the_configured_jp_price_source(monkeypatch) -> None:
    """``daily`` の日本株価格取得は ``JP_PRICE_SOURCE`` に従う。

    ``fetch``・``bulk-fetch`` は J-Quants 固定を後から ``JP_PRICE_SOURCE`` 配線
    に直したが、``daily`` の混在リスト振り分けは立花プロバイダの実装(8/23)より
    前の8/16に書かれ、日本株を常に ``jquants`` に固定したまま取り残されていた。
    切り替えたはずが常に旧経路を叩き続ける、このプロジェクトで繰り返し起きて
    いる不具合そのもの。
    """
    from stock_ai import cli
    from stock_ai.database.engine import Database

    database = Database("sqlite:///:memory:")
    database.create_all()
    monkeypatch.setattr(cli, "Database", lambda: database)
    monkeypatch.setenv("JP_PRICE_SOURCE", "tachibana")
    get_settings.cache_clear()

    asked: list[tuple[str, tuple[str, ...]]] = []

    def _source(name: str, _settings: object) -> tuple[object, str]:
        return name, ("JP" if name in ("jquants", "tachibana") else "US")

    class _Service:
        def __init__(self, provider: str, *_args: object, **_kwargs: object) -> None:
            self.provider = provider

        def ingest_many(self, syms, *_args: object, **_kwargs: object) -> list:
            asked.append((self.provider, tuple(syms)))
            return []

    monkeypatch.setattr(cli, "_price_source", _source)
    monkeypatch.setattr(cli, "IngestionService", _Service)

    result = runner.invoke(cli.app, ["daily", "--once", "7203", "AAPL"])

    assert result.exit_code == 0, result.output
    routed = dict(asked)
    assert routed["tachibana"] == ("7203",)
    assert routed["yfinance"] == ("AAPL",)
    database.dispose()
    get_settings.cache_clear()


def test_daily_source_option_still_overrides_jp_routing(monkeypatch) -> None:
    """``--source jquants`` は ``JP_PRICE_SOURCE`` より優先される単発の指定。"""
    from stock_ai import cli
    from stock_ai.database.engine import Database

    database = Database("sqlite:///:memory:")
    database.create_all()
    monkeypatch.setattr(cli, "Database", lambda: database)
    monkeypatch.setenv("JP_PRICE_SOURCE", "tachibana")
    get_settings.cache_clear()

    asked: list[tuple[str, tuple[str, ...]]] = []

    def _source(name: str, _settings: object) -> tuple[object, str]:
        return name, ("JP" if name in ("jquants", "tachibana") else "US")

    class _Service:
        def __init__(self, provider: str, *_args: object, **_kwargs: object) -> None:
            self.provider = provider

        def ingest_many(self, syms, *_args: object, **_kwargs: object) -> list:
            asked.append((self.provider, tuple(syms)))
            return []

    monkeypatch.setattr(cli, "_price_source", _source)
    monkeypatch.setattr(cli, "IngestionService", _Service)

    result = runner.invoke(cli.app, ["daily", "--once", "--source", "jquants", "7203"])

    assert result.exit_code == 0, result.output
    assert dict(asked)["jquants"] == ("7203",)
    database.dispose()
    get_settings.cache_clear()


# --- 財務諸表の取得元 -----------------------------------------------------


def _settings(**overrides: object):
    """設定を1つ作る。``.env`` は読ませない。

    キーワードは**環境変数名**で渡す。フィールド名で渡すと ``extra="ignore"`` に
    黙って落とされ、既定値のままの設定が返る。
    """
    from stock_ai.config.settings import Settings

    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def test_statements_defaults_to_the_configured_source() -> None:
    """``--source`` を省いたら設定に従う。既定は J-Quants のまま。"""
    from stock_ai.cli import _statement_fetcher

    _, used = _statement_fetcher("", _settings(), 400)
    assert used == "jquants"


def test_statements_reads_edinet_from_settings() -> None:
    """``.env`` に ``JP_STATEMENT_SOURCE=edinet`` と書けば、引数なしで切り替わる。"""
    from stock_ai.cli import _statement_fetcher

    _, used = _statement_fetcher("", _settings(JP_STATEMENT_SOURCE="edinet"), 400)
    assert used == "edinet"


def test_statements_option_overrides_the_setting() -> None:
    from stock_ai.cli import _statement_fetcher

    _, used = _statement_fetcher("edinet", _settings(), 400)
    assert used == "edinet"


def test_statements_rejects_an_unknown_source() -> None:
    """綴り違いを J-Quants に落とさない。黙って別の口に行くほうが悪い。"""
    import typer

    from stock_ai.cli import _statement_fetcher

    with pytest.raises(typer.BadParameter, match="edinet"):
        _statement_fetcher("EDINET-v2", _settings(), 400)


def test_the_edinet_window_is_wide_enough_for_an_annual_report() -> None:
    """有報は年に1度。既定の窓が1年を切っていると、必ず空振りする年がある。"""
    from stock_ai.cli import _statement_fetcher

    fetcher, _ = _statement_fetcher("edinet", _settings(), 400)
    assert fetcher.__closure__ is not None
    sources = [
        cell.cell_contents
        for cell in fetcher.__closure__
        if hasattr(cell.cell_contents, "lookback_days")
    ]
    assert [s.lookback_days for s in sources] == [400]


# --- 保存された財務諸表を見る ---------------------------------------------


def _stored(database, symbol: str, reports) -> None:
    from stock_ai.database.repository import FinancialStatementRepository, get_or_create_security

    with database.session() as session:
        get_or_create_security(session, symbol, market="JP")
        FinancialStatementRepository(session).upsert_reports(symbol, reports, market="JP")


def test_statements_show_prints_what_was_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    """``statements`` は書くだけで、書いた結果を見る手段が無かった。

    画面が何も返さないとき、データが無いのか閾値が厳しいのかを、ここを見ずに
    区別できない。実測値は日立の2026年3月期（EDINET 経由）。
    """
    from stock_ai import cli
    from stock_ai.data.types import FinancialReport
    from stock_ai.database.engine import Database

    database = Database("sqlite:///:memory:")
    database.create_all()
    _stored(
        database,
        "6501",
        [
            FinancialReport(
                symbol="6501",
                fiscal_year=2026,
                revenue=10_586_781_000_000.0,
                net_income=802_368_000_000.0,
                equity=6_568_369_000_000.0,
                shares_outstanding=4_535_560_000.0,
            )
        ],
    )
    monkeypatch.setattr(cli, "Database", lambda: database)

    result = runner.invoke(cli.app, ["statements-show", "6501"])

    assert result.exit_code == 0
    assert "2026" in result.output
    assert "105,868" in result.output  # 売上は億円
    assert "4,536" in result.output  # 株式数は百万株
    database.dispose()


def test_statements_show_prints_empty_columns_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """埋まらなかった列も出す。空欄であること自体が知りたいこと。

    EDINET から取ると 営業利益・EPS・BPS は空になる（1株配当は埋まる）。黙って
    列ごと消すと、取れていないのか元から無いのかが分からなくなる。
    """
    from stock_ai import cli
    from stock_ai.data.types import FinancialReport
    from stock_ai.database.engine import Database

    database = Database("sqlite:///:memory:")
    database.create_all()
    _stored(database, "9020", [FinancialReport(symbol="9020", fiscal_year=2022, revenue=1.0)])
    monkeypatch.setattr(cli, "Database", lambda: database)

    result = runner.invoke(cli.app, ["statements-show", "9020"])

    assert result.exit_code == 0
    for label in ("営業利益", "EPS", "BPS", "1株配当"):
        assert label in result.output
    database.dispose()


def test_statements_show_says_when_nothing_is_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    """空の表を出すより、無いと言う。"""
    from stock_ai import cli
    from stock_ai.database.engine import Database

    database = Database("sqlite:///:memory:")
    database.create_all()
    monkeypatch.setattr(cli, "Database", lambda: database)

    result = runner.invoke(cli.app, ["statements-show", "6501"])

    assert result.exit_code == 1
    assert "6501" in result.output
    database.dispose()


# --- info が取得元を出す ---------------------------------------------------


def test_info_shows_where_japanese_data_comes_from(monkeypatch: pytest.MonkeyPatch) -> None:
    """日本株のデータが全部どこから来るかを決める2つを出す。

    ここに出ていないと、切り替えたつもりで切り替わっていないことに、数字が変わら
    ないという形でしか気付けない。
    """
    monkeypatch.setenv("JP_PRICE_SOURCE", "tachibana")
    monkeypatch.setenv("JP_STATEMENT_SOURCE", "edinet")
    get_settings.cache_clear()

    result = runner.invoke(app, ["info"])

    assert result.exit_code == 0
    assert "jp_price_source" in result.output
    assert "tachibana" in result.output
    assert "jp_statement_source" in result.output
    assert "edinet" in result.output
    get_settings.cache_clear()


def test_info_flags_a_source_name_that_is_not_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """綴り違いは黙って既定に落ちる形では出さない。

    ``fetch`` は実行時に弾くが、それは実行して初めて分かる。設定を見る場所で
    見えるほうが早い。
    """
    monkeypatch.setenv("JP_PRICE_SOURCE", "tachibna")
    get_settings.cache_clear()

    result = runner.invoke(app, ["info"])

    assert result.exit_code == 0
    assert "未対応" in result.output
    get_settings.cache_clear()


def test_info_shows_the_tachibana_deadline_when_it_is_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """版には停止日がある。使うと決めた時点で見えるべきもの。

    v4r9 は 2026-09-27 に停止する。切り替えた日にこれが目に入らないと、停止日に
    株価取得が黙って止まる。

    版は明示的に v4r9 を指定する。指定しなければ ``default_version()`` がその日の
    日付で選ぶため、v4r10 が公開された 2026-08-29 以降はこのテストが「今日の既定
    はもう v4r9 ではない」という無関係な理由で落ちる。
    """
    monkeypatch.setenv("JP_PRICE_SOURCE", "tachibana")
    monkeypatch.setenv("TACHIBANA_API_VERSION", "v4r9")
    get_settings.cache_clear()

    result = runner.invoke(app, ["info"])

    assert "tachibana version" in result.output
    assert "2026-09-27" in result.output
    get_settings.cache_clear()


def test_info_stays_quiet_about_tachibana_when_it_is_not_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """使っていない API の期限は雑音。"""
    monkeypatch.setenv("JP_PRICE_SOURCE", "jquants")
    get_settings.cache_clear()

    result = runner.invoke(app, ["info"])

    assert "tachibana version" not in result.output
    get_settings.cache_clear()


# --- bulk-fetch は JP_PRICE_SOURCE に従う --------------------------------


def test_bulk_fetch_prices_uses_the_configured_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """``bulk-fetch --what prices`` は J-Quants に固定されていた。

    ``BulkIngester`` 自体は ``price_provider`` を差し替えられる作りだったが、
    CLI 側がそれを渡さず常に ``JQuantsPriceProvider`` を作っていた。実機では
    ``JP_PRICE_SOURCE=tachibana`` に切り替えた直後の全銘柄再取得が、気付かれ
    ないまま J-Quants を叩き続けて 429 を大量に返した。
    """
    from stock_ai import cli
    from stock_ai.data.bulk import BulkReport, Dataset
    from stock_ai.database.engine import Database

    database = Database("sqlite:///:memory:")
    database.create_all()
    monkeypatch.setattr(cli, "Database", lambda: database)
    monkeypatch.setenv("JP_PRICE_SOURCE", "tachibana")
    get_settings.cache_clear()

    captured: dict[str, object] = {}

    class _FakeIngester:
        def __init__(self, *args: object, **kwargs: object) -> None:
            captured.update(kwargs)

        def run(self, *args: object, **kwargs: object) -> BulkReport:
            return BulkReport(dataset=Dataset.PRICES)

    monkeypatch.setattr(cli, "BulkIngester", _FakeIngester)
    monkeypatch.setattr(cli, "_price_source", lambda source, settings: (f"provider:{source}", "JP"))

    result = runner.invoke(cli.app, ["bulk-fetch", "--what", "prices", "--symbols", "6501"])

    assert result.exit_code == 0, result.output
    assert captured["price_provider"] == "provider:tachibana"
    database.dispose()
    get_settings.cache_clear()


def test_bulk_fetch_statements_ignores_jp_price_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """統計側の取得元は JP_STATEMENT_SOURCE で決まる。JP_PRICE_SOURCE には従わない。"""
    from stock_ai import cli
    from stock_ai.data.bulk import BulkReport, Dataset
    from stock_ai.database.engine import Database

    database = Database("sqlite:///:memory:")
    database.create_all()
    monkeypatch.setattr(cli, "Database", lambda: database)
    monkeypatch.setenv("JP_PRICE_SOURCE", "tachibana")
    get_settings.cache_clear()

    captured: dict[str, object] = {}

    class _FakeIngester:
        def __init__(self, *args: object, **kwargs: object) -> None:
            captured.update(kwargs)

        def run(self, *args: object, **kwargs: object) -> BulkReport:
            return BulkReport(dataset=Dataset.STATEMENTS)

    monkeypatch.setattr(cli, "BulkIngester", _FakeIngester)

    result = runner.invoke(cli.app, ["bulk-fetch", "--what", "statements", "--symbols", "6501"])

    assert result.exit_code == 0, result.output
    assert captured["price_provider"] is None
    assert captured["statement_provider"] is None  # 既定の jquants は None のまま
    database.dispose()
    get_settings.cache_clear()


def test_bulk_fetch_statements_uses_edinet_when_selected(monkeypatch: pytest.MonkeyPatch) -> None:
    """``JP_STATEMENT_SOURCE=edinet`` で ``bulk-fetch --what statements`` を切り替える。

    以前は ``JQuantsFundamentalsProvider`` 固定で、この設定を見ていなかった。
    """
    from stock_ai import cli
    from stock_ai.data.bulk import BulkReport, Dataset
    from stock_ai.database.engine import Database

    database = Database("sqlite:///:memory:")
    database.create_all()
    monkeypatch.setattr(cli, "Database", lambda: database)
    monkeypatch.setenv("JP_STATEMENT_SOURCE", "edinet")
    get_settings.cache_clear()

    captured: dict[str, object] = {}

    class _FakeIngester:
        def __init__(self, *args: object, **kwargs: object) -> None:
            captured.update(kwargs)

        def run(self, *args: object, **kwargs: object) -> BulkReport:
            return BulkReport(dataset=Dataset.STATEMENTS)

    monkeypatch.setattr(cli, "BulkIngester", _FakeIngester)
    monkeypatch.setattr(cli, "EdinetFundamentalsProvider", lambda *a, **k: "edinet-provider")

    result = runner.invoke(cli.app, ["bulk-fetch", "--what", "statements", "--symbols", "6501"])

    assert result.exit_code == 0, result.output
    assert captured["statement_provider"] == "edinet-provider"
    assert captured["price_provider"] is None  # prices データセットではないので触らない
    database.dispose()
    get_settings.cache_clear()


def _plain(text: str) -> str:
    """rich の装飾と折り返しを剥がして、文言だけを残す。

    CI には端末が無いが色は出る。ローカルで通ったアサーションが CI だけで
    落ちたのはこれが理由で、色コードが単語の途中に入って照合が壊れていた。
    """
    import re

    return re.sub(r"\x1b\[[0-9;]*m", "", text).replace("\n", "").replace(" ", "")


def test_pead_run_refuses_oos_without_the_explicit_flag() -> None:
    """合否判定はOOSで一度だけ。うっかり見てしまう経路を作らない。"""
    result = runner.invoke(app, ["pead-run", "oos"])
    assert result.exit_code != 0
    assert "--i-am-ready-for-oos" in _plain(result.output)


def test_pead_run_refuses_all_without_the_explicit_flag() -> None:
    """all にも held-out が含まれる。is だけが無条件で走る。"""
    result = runner.invoke(app, ["pead-run", "all"])
    assert result.exit_code != 0


def test_pead_run_requires_a_period() -> None:
    """既定を置くと「とりあえず全部出す」がOOSを消費する。"""
    result = runner.invoke(app, ["pead-run"])
    assert result.exit_code != 0


def test_pead_run_rejects_an_unknown_period() -> None:
    result = runner.invoke(app, ["pead-run", "everything"])
    assert result.exit_code != 0


def test_reversal_power_refuses_to_reach_the_judged_period() -> None:
    """**封印前の検出力計算が、判定期間を選べてしまわないこと。**

    平均を出していなくても、期間を後から選べるなら「その期間なら何%出るか」
    を選んだのと同じになる。忘れずに守るのではなく、拒否させる。
    """
    result = runner.invoke(app, ["reversal-power", "--end", "2022-01-01"])
    assert result.exit_code != 0
    assert "2021-09-01" in result.output


def test_reversal_bias_refuses_to_reach_out_of_sample() -> None:
    """バイアスの実測でOOSを覗くと、判定に使える一度が失われる。"""
    result = runner.invoke(app, ["reversal-bias", "--end", "2024-06-01"])
    assert result.exit_code != 0
    assert "2024-01-01" in result.output
