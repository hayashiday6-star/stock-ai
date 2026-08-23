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

    # 立花証券・ｅ支店・ＡＰＩ。認証ＩＤは秘密だが、秘密鍵のパスと版は秘密ではない。
    # 版を固定値で持たないのは、旧版が後継の並行リリースから約60日で停止するため
    # (docs/TACHIBANA.md)。空にしておけば、その日の既定が使われる。
    tachibana_auth_id: SecretStr | None = Field(default=None, validation_alias="TACHIBANA_AUTH_ID")
    tachibana_private_key: str = Field(
        default="tachibana_private.pem", validation_alias="TACHIBANA_PRIVATE_KEY"
    )
    tachibana_api_version: str | None = Field(
        default=None, validation_alias="TACHIBANA_API_VERSION"
    )
    tachibana_base_url: str | None = Field(default=None, validation_alias="TACHIBANA_BASE_URL")
    tachibana_session_file: str = Field(
        default="tachibana_session.json", validation_alias="TACHIBANA_SESSION_FILE"
    )
    # 日本株の株価をどこから取るか。J-Quants の有料プランをやめるなら 'tachibana'。
    jp_price_source: str = Field(default="jquants", validation_alias="JP_PRICE_SOURCE")
    # 日本株の財務諸表をどこから取るか。'edinet' は有報の「主要な経営指標等」から
    # 5期ぶんを読む。無料だが、有報を探すのに1日1リクエストで数百日ぶん走査する。
    jp_statement_source: str = Field(default="jquants", validation_alias="JP_STATEMENT_SOURCE")

    # --- AI providers (Phase 6) ---
    #: Which provider the commands use when none is named on the command line.
    #: It defaults to ``dummy`` so a fresh checkout runs without an API key,
    #: and that default is exactly the trap it has to be configurable for: a
    #: dummy run completes, reports alerts, and bills nothing, so nothing on
    #: screen distinguishes it from a real one until the ratings are read.
    ai_provider: str = Field(default="dummy", validation_alias="AI_PROVIDER")
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
