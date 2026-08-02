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
