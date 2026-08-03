"""Tests for settings, constants, and the exception hierarchy."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from stock_ai.config.constants import ENV_FILE, PROJECT_ROOT
from stock_ai.config.settings import Settings
from stock_ai.core import exceptions as exc


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    """Build a hermetic Settings instance from explicit env vars only."""
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    # `_env_file=None` ignores any real project .env so tests stay hermetic.
    return Settings(_env_file=None)


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    assert settings.env == "development"
    assert settings.log_level == "INFO"
    assert settings.is_production is False
    assert settings.jquants_api_key is None


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, STOCK_AI_ENV="production", STOCK_AI_LOG_LEVEL="DEBUG")
    assert settings.env == "production"
    assert settings.log_level == "DEBUG"
    assert settings.is_production is True


def test_secret_is_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, JQUANTS_API_KEY="super-secret")
    assert settings.jquants_api_key is not None
    # SecretStr must not leak the value via repr/str.
    assert "super-secret" not in repr(settings)
    assert "super-secret" not in str(settings.jquants_api_key)
    # ...but the real value is retrievable explicitly.
    assert settings.jquants_api_key.get_secret_value() == "super-secret"


def test_invalid_env_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        _settings(monkeypatch, STOCK_AI_ENV="staging")


def test_paths_are_absolute_and_under_root() -> None:
    assert PROJECT_ROOT.is_absolute()
    assert ENV_FILE.parent == PROJECT_ROOT


def test_exception_hierarchy() -> None:
    for err in (exc.ConfigError, exc.DataError, exc.BrokerError, exc.NotificationError):
        assert issubclass(err, exc.StockAIError)


# --- secrets must not reach the console, the log file, or a pasted report ---


def test_a_key_in_a_query_string_is_redacted() -> None:
    """The exact shape that leaked: httpx logs the URL, the URL holds the key."""
    from stock_ai.core.logging import redact

    key = "edb_8cb0271a2b74ec16409ad03342646bcc"
    line = (
        "HTTP Request: GET https://api.edinet-fsa.go.jp/api/v2/documents.json"
        f"?date=2026-08-03&type=2&Subscription-Key={key} 'HTTP/1.1 200 OK'"
    )
    cleaned = redact(line)
    assert key not in cleaned
    assert "Subscription-Key=<redacted>" in cleaned
    # The rest of the line has to survive, or the redaction destroys the
    # diagnostic value of the log it is protecting.
    assert "HTTP/1.1 200 OK" in cleaned


def test_a_registered_secret_is_redacted_anywhere_it_appears() -> None:
    """Query-parameter matching cannot catch a key echoed in prose."""
    from stock_ai.core.logging import redact, register_secret

    key = "sk-registered-secret-value-12345"
    register_secret(key)
    assert key not in redact(f"authentication failed using {key}")
    assert key not in redact(f"headers={{'x-api-key': '{key}'}}")


def test_a_short_value_is_never_registered() -> None:
    """Redacting a common substring would corrupt unrelated messages."""
    from stock_ai.core.logging import redact, register_secret

    register_secret("dev")
    assert redact("running in dev mode") == "running in dev mode"


def test_an_ordinary_log_line_is_left_alone() -> None:
    from stock_ai.core.logging import redact

    line = "Fetched 6 J-Quants statement(s) for 7203"
    assert redact(line) == line


def test_the_filter_scrubs_a_record_before_a_handler_sees_it() -> None:
    """Redaction has to happen on the record, not just in a helper nobody calls."""
    import logging

    from stock_ai.core.logging import SecretRedactingFilter

    key = "edb_1234567890abcdef1234567890abcdef"
    record = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="GET %s",
        args=(f"https://x/y?Subscription-Key={key}",),
        exc_info=None,
    )
    assert SecretRedactingFilter().filter(record) is True
    assert key not in record.getMessage()


def test_a_broken_format_string_still_gets_through() -> None:
    """Losing a log record to the thing that protects log records is worse."""
    import logging

    from stock_ai.core.logging import SecretRedactingFilter

    record = logging.LogRecord(
        name="x",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="%d %d",
        args=(1,),
        exc_info=None,
    )
    assert SecretRedactingFilter().filter(record) is True
