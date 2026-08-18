"""Application settings loaded from environment variables and the ``.env`` file.

Configuration is centralised here (no hard-coded values scattered across the
codebase). Secrets are wrapped in :class:`~pydantic.SecretStr` so they never
appear in logs, ``repr`` output, or tracebacks.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from stock_ai.config.constants import ENV_FILE
from stock_ai.core.logging import register_secret

Environment = Literal["development", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class Settings(BaseSettings):
    """Typed application configuration.

    Precedence: environment variables > project ``.env`` file > defaults
    declared below. Unknown environment variables are ignored so the process
    environment can carry unrelated keys without breaking startup.
    """

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Runtime ---
    env: Environment = Field(default="development", validation_alias="STOCK_AI_ENV")
    log_level: LogLevel = Field(default="INFO", validation_alias="STOCK_AI_LOG_LEVEL")

    # --- Market data ---
    jquants_api_key: SecretStr | None = Field(default=None, validation_alias="JQUANTS_API_KEY")
    edinet_api_key: SecretStr | None = Field(default=None, validation_alias="EDINET_API_KEY")

    # --- AI providers (Phase 6) ---
    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    #: Which Claude model to call. ``None`` means the provider's own default.
    #: Configurable because the choice is a twenty-fold cost difference on the
    #: same run, and because ``ai-cost --model`` could otherwise price a model
    #: the run had no way to actually use - an estimate for one model and a
    #: bill for another, with nothing on screen to say so.
    anthropic_model: str | None = Field(default=None, validation_alias="ANTHROPIC_MODEL")
    openai_api_key: SecretStr | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    gemini_api_key: SecretStr | None = Field(default=None, validation_alias="GEMINI_API_KEY")

    # --- Notifications (Phase 8) ---
    discord_webhook_url: SecretStr | None = Field(
        default=None, validation_alias="DISCORD_WEBHOOK_URL"
    )
    line_channel_access_token: SecretStr | None = Field(
        default=None, validation_alias="LINE_CHANNEL_ACCESS_TOKEN"
    )
    telegram_bot_token: SecretStr | None = Field(
        default=None, validation_alias="TELEGRAM_BOT_TOKEN"
    )
    telegram_chat_id: SecretStr | None = Field(default=None, validation_alias="TELEGRAM_CHAT_ID")

    @property
    def is_production(self) -> bool:
        """Whether the application is running in the production environment."""
        return self.env == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a process-wide cached :class:`Settings` instance.

    Cached so configuration is read once. Call ``get_settings.cache_clear()``
    in tests when you need to re-read the environment.

    Loading also registers every secret with the log redactor. :class:`SecretStr`
    keeps a key out of *our* logs, but the HTTP client underneath logs the URL it
    fetched, and an API that authenticates by query parameter puts the key in
    that URL. Registering the values here is what makes them unprintable no
    matter which library does the printing.
    """
    settings = Settings()
    for field in Settings.model_fields:
        value = getattr(settings, field, None)
        if isinstance(value, SecretStr):
            register_secret(value.get_secret_value())
    return settings
