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

from stock_ai.config.constants import ENV_FILE, OPEND_HOST, OPEND_PORT
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
        default="tachibana/private.pem", validation_alias="TACHIBANA_PRIVATE_KEY"
    )
    tachibana_api_version: str | None = Field(
        default=None, validation_alias="TACHIBANA_API_VERSION"
    )
    tachibana_base_url: str | None = Field(default=None, validation_alias="TACHIBANA_BASE_URL")
    tachibana_session_file: str = Field(
        default="tachibana/session.json", validation_alias="TACHIBANA_SESSION_FILE"
    )
    # 日本株の株価をどこから取るか。J-Quants の有料プランをやめるなら 'tachibana'。
    jp_price_source: str = Field(default="jquants", validation_alias="JP_PRICE_SOURCE")
    # 日本株の財務諸表をどこから取るか。'edinet' は有報の「主要な経営指標等」から
    # 5期ぶんを読む。無料だが、有報を探すのに1日1リクエストで数百日ぶん走査する。
    jp_statement_source: str = Field(default="jquants", validation_alias="JP_STATEMENT_SOURCE")
    # 日本株の銘柄一覧（市場区分・業種）をどこから取るか。
    #
    # **JP_PRICE_SOURCE も JP_STATEMENT_SOURCE もここには効かない。** 価格と
    # 財務を立花・EDINET に移しても、銘柄一覧だけ J-Quants を叩き続けていた
    # （docs/JQUANTS_EXIT.md）。切り替えられる形にしておかないと、解約した日に
    # 銘柄一覧の更新だけが黙って止まる。
    jp_universe_source: str = Field(default="jquants", validation_alias="JP_UNIVERSE_SOURCE")

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

    # --- Brokerage: moomoo OpenD ---
    #: Where the local OpenD gateway listens. It is loopback by default and
    #: should stay that way: OpenD holds an authenticated brokerage session, so
    #: binding it to a reachable address publishes that session to the network.
    moomoo_opend_host: str = Field(default=OPEND_HOST, validation_alias="MOOMOO_OPEND_HOST")
    moomoo_opend_port: int = Field(default=OPEND_PORT, validation_alias="MOOMOO_OPEND_PORT")
    #: Which moomoo entity holds the account - ``FUTUJP`` for moomoo証券 (Japan).
    #: Naming the wrong one does not raise: the account list simply comes back
    #: empty, which reads as "no accounts" rather than "wrong entity".
    moomoo_security_firm: str = Field(default="FUTUJP", validation_alias="MOOMOO_SECURITY_FIRM")
    moomoo_trd_market: str = Field(default="JP", validation_alias="MOOMOO_TRD_MARKET")
    #: ``SIMULATE`` (paper) or ``REAL``. Paper by default, for the same reason
    #: PaperBroker is the default broker: the live account is opt-in, never the
    #: value a fresh checkout happens to inherit.
    moomoo_trd_env: str = Field(default="SIMULATE", validation_alias="MOOMOO_TRD_ENV")
    #: The 6-digit moomoo trading PIN (取引暗証番号), which is *not* the login
    #: password. Optional: leaving it unset means the live account is never
    #: unlocked, which is the safe state to be in by default.
    moomoo_trade_password: SecretStr | None = Field(
        default=None, validation_alias="MOOMOO_TRADE_PASSWORD"
    )

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

    # --- 自動売買の運用モニタ(正典は別リポジトリ) ---
    # 実弾/ペーパーの自動売買そのものは WSL 上の別リポジトリで動いていて、帳簿・
    # シグナル・キルスイッチはそちらが正典。ダッシュボードの「自動売買 運用」画面は
    # そこを読みに行くだけで、ルールをこちらに複製しない(stock_ai.ops 参照)。
    # 場所を設定にしてあるのは、正典が引っ越したときに黙って古いコピーを読み続ける
    # 事故を避けるため — 実際に一度、Desktop の古いコピーと正典が分岐している。
    ops_wsl_distro: str = Field(default="Ubuntu-24.04", validation_alias="OPS_WSL_DISTRO")
    ops_repo_path: str = Field(default="/home/hayashida/test", validation_alias="OPS_REPO_PATH")
    ops_repo_python: str = Field(default=".venv/bin/python", validation_alias="OPS_REPO_PYTHON")

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
