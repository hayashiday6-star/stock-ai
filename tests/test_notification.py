"""Tests for notifiers, the factory, and the ``notify`` CLI (no network)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from typer.testing import CliRunner

from stock_ai import cli
from stock_ai.config.settings import Settings
from stock_ai.core.exceptions import NotificationError
from stock_ai.notification.channels import (
    DiscordNotifier,
    LineNotifier,
    TelegramNotifier,
)
from stock_ai.notification.console import ConsoleNotifier
from stock_ai.notification.factory import get_notifier

runner = CliRunner()


@dataclass
class _FakeResponse:
    status_code: int = 200

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@dataclass
class _FakeClient:
    response: _FakeResponse = field(default_factory=_FakeResponse)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return self.response


def _settings(**env: str) -> Settings:
    return Settings(_env_file=None, **env)  # type: ignore[arg-type]


# --- channels --------------------------------------------------------------


def test_discord_posts_content() -> None:
    client = _FakeClient()
    DiscordNotifier("https://discord/webhook", client=client).send("hello")
    assert client.calls[0]["url"] == "https://discord/webhook"
    assert client.calls[0]["json"] == {"content": "hello"}


def test_discord_unconfigured_raises() -> None:
    with pytest.raises(NotificationError):
        DiscordNotifier(None, client=_FakeClient()).send("x")


def test_discord_http_error_wrapped() -> None:
    client = _FakeClient(response=_FakeResponse(status_code=500))
    with pytest.raises(NotificationError):
        DiscordNotifier("https://discord/webhook", client=client).send("x")


def test_telegram_posts_to_bot_api() -> None:
    client = _FakeClient()
    TelegramNotifier("TOKEN", "42", client=client).send("hi")
    assert client.calls[0]["url"] == "https://api.telegram.org/botTOKEN/sendMessage"
    assert client.calls[0]["json"] == {"chat_id": "42", "text": "hi"}


def test_telegram_requires_token_and_chat() -> None:
    with pytest.raises(NotificationError):
        TelegramNotifier("TOKEN", None, client=_FakeClient()).send("x")


def test_line_broadcasts_with_auth_header() -> None:
    client = _FakeClient()
    LineNotifier("LINE_TOKEN", client=client).send("news")
    call = client.calls[0]
    assert call["url"].endswith("/message/broadcast")
    assert call["headers"]["Authorization"] == "Bearer LINE_TOKEN"
    assert call["json"]["messages"][0]["text"] == "news"


def test_console_notifier_prints(capsys: pytest.CaptureFixture[str]) -> None:
    ConsoleNotifier().send("hey")
    assert "hey" in capsys.readouterr().out


# --- factory ---------------------------------------------------------------


def test_factory_returns_console() -> None:
    assert isinstance(get_notifier("console", _settings()), ConsoleNotifier)


def test_factory_returns_channels() -> None:
    settings = _settings()
    assert isinstance(get_notifier("discord", settings), DiscordNotifier)
    assert isinstance(get_notifier("telegram", settings), TelegramNotifier)
    assert isinstance(get_notifier("line", settings), LineNotifier)


def test_factory_unknown_raises() -> None:
    with pytest.raises(NotificationError):
        get_notifier("smoke-signal", _settings())


# --- CLI -------------------------------------------------------------------


def test_notify_cli_console() -> None:
    result = runner.invoke(cli.app, ["notify", "buy candidate: AAPL"])
    assert result.exit_code == 0
    assert "AAPL" in result.stdout


def test_notify_cli_unconfigured_channel_exits_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force the channel to be unconfigured regardless of the developer's .env.
    monkeypatch.setattr(cli, "get_notifier", lambda *_: DiscordNotifier(None))
    result = runner.invoke(cli.app, ["notify", "hi", "--channel", "discord"])
    assert result.exit_code == 1


# --- credentials must not escape via error messages ------------------------


def test_telegram_error_does_not_leak_the_bot_token() -> None:
    """The bot token rides in the URL path; a failure must not print it."""
    client = _FakeClient(response=_FakeResponse(status_code=500))
    with pytest.raises(NotificationError) as excinfo:
        TelegramNotifier("SUPER-SECRET-TOKEN", "42", client=client).send("x")
    assert "SUPER-SECRET-TOKEN" not in str(excinfo.value)


def test_discord_error_does_not_leak_the_webhook_url() -> None:
    """The Discord webhook URL is itself the credential."""
    client = _FakeClient(response=_FakeResponse(status_code=500))
    with pytest.raises(NotificationError) as excinfo:
        DiscordNotifier(
            "https://discord.com/api/webhooks/123/SUPER-SECRET-HOOK", client=client
        ).send("x")
    assert "SUPER-SECRET-HOOK" not in str(excinfo.value)


def test_error_still_names_the_host_and_the_cause() -> None:
    """Redaction must keep the message diagnosable."""
    client = _FakeClient(response=_FakeResponse(status_code=500))
    with pytest.raises(NotificationError) as excinfo:
        TelegramNotifier("TOKEN", "42", client=client).send("x")
    message = str(excinfo.value)
    assert "api.telegram.org" in message
    assert "HTTP 500" in message


def test_transport_error_quoting_the_url_is_scrubbed() -> None:
    """A client that echoes the target URL must not smuggle the token out."""

    class _EchoingClient:
        def post(self, url: str, **kwargs: Any) -> _FakeResponse:
            raise RuntimeError(f"connection failed for url '{url}'")

    with pytest.raises(NotificationError) as excinfo:
        TelegramNotifier("SUPER-SECRET-TOKEN", "42", client=_EchoingClient()).send("x")
    assert "SUPER-SECRET-TOKEN" not in str(excinfo.value)


def test_a_failed_unattended_run_speaks(monkeypatch) -> None:
    """Alerts are only sent when there are alerts, which for a scheduled job
    makes the channel silent in four situations that mean opposite things:
    nothing was filed, nothing cleared the threshold, the run was skipped by
    --max-cost, and the run failed outright. A channel that says the same
    thing when all is well and when everything is broken is not a channel.
    """
    from stock_ai.cli import _report_run_outcome
    from stock_ai.core.scheduler import JobResult

    sent: list[str] = []

    class _Spy:
        name = "spy"

        def send(self, message: str) -> None:
            sent.append(message)

    ok = JobResult(name="prices", ok=True, error=None)
    bad = JobResult(name="monitor", ok=False, error="priced above --max-cost")

    _report_run_outcome(_Spy(), [bad], [ok, bad], heartbeat=False)
    assert len(sent) == 1
    assert "monitor" in sent[0]
    assert "priced above --max-cost" in sent[0]


def test_a_clean_run_stays_quiet_unless_a_heartbeat_is_asked_for() -> None:
    """A message every morning is one people stop reading - and then the
    failure message is unread too."""
    from stock_ai.cli import _report_run_outcome
    from stock_ai.core.scheduler import JobResult

    sent: list[str] = []

    class _Spy:
        name = "spy"

        def send(self, message: str) -> None:
            sent.append(message)

    results = [JobResult(name="monitor", ok=True, error=None)]

    _report_run_outcome(_Spy(), [], results, heartbeat=False)
    assert sent == []

    _report_run_outcome(_Spy(), [], results, heartbeat=True)
    assert len(sent) == 1
    assert "ok" in sent[0]


def test_a_dead_notifier_does_not_take_the_run_down() -> None:
    """The jobs already ran; their outcome is in the log either way."""
    from stock_ai.cli import _report_run_outcome
    from stock_ai.core.exceptions import NotificationError
    from stock_ai.core.scheduler import JobResult

    class _Broken:
        name = "broken"

        def send(self, message: str) -> None:
            raise NotificationError("webhook 404")

    _report_run_outcome(
        _Broken(), [JobResult(name="monitor", ok=False, error="x")], [], heartbeat=False
    )


def test_a_busy_day_is_split_rather_than_rejected() -> None:
    """39 alerts in one Discord payload came back 400, and the run died with it.

    A monitor pass has no say in how many alerts a day produces. Sending them
    as one string turns a *good* day - lots to report - into a delivery
    failure, which is exactly backwards.
    """
    from stock_ai.notification.channels import DiscordNotifier, split_message

    alerts = "\n\n".join(f"[HIGH] {i:04d}\ntitle {i}\n{'summary ' * 30}" for i in range(39))
    assert len(alerts) > DiscordNotifier.MAX_CHARS

    parts = split_message(alerts, DiscordNotifier.MAX_CHARS)
    assert len(parts) > 1
    assert all(len(part) <= DiscordNotifier.MAX_CHARS for part in parts)
    # Nothing is lost, and no alert is torn in half.
    assert "\n\n".join(parts) == alerts
    for part in parts:
        assert part.startswith("[HIGH]")


def test_an_over_long_single_alert_still_gets_through() -> None:
    """One alert past the cap on its own must not block the whole delivery."""
    from stock_ai.notification.channels import split_message

    parts = split_message("x" * 250, 100)
    assert [len(p) for p in parts] == [100, 100, 50]
    assert "".join(parts) == "x" * 250


def test_discord_posts_each_part_separately() -> None:
    """The split has to reach the wire, not just the helper."""
    from stock_ai.notification.channels import DiscordNotifier

    posted: list[str] = []

    class _Response:
        def raise_for_status(self) -> None:
            return None

    class _Client:
        def post(self, url: str, json: dict, headers: dict, timeout: float) -> _Response:
            posted.append(json["content"])
            return _Response()

    message = "\n\n".join(f"block {i} " + "y" * 500 for i in range(10))
    DiscordNotifier("https://discord.com/api/webhooks/x/y", client=_Client()).send(message)

    assert len(posted) > 1
    assert all(len(part) <= DiscordNotifier.MAX_CHARS for part in posted)
