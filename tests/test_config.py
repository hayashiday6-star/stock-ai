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
