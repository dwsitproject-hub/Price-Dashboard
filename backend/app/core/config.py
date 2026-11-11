"""Application settings, loaded from environment variables (and backend/.env locally)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]

APP_VERSION = "2.0.0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ── App ──────────────────────────────────────────────────────────────────
    app_env: str = "local"                       # local | sit | uat | prod
    app_base_url: str = "http://localhost:5181"  # public origin (frontend)
    app_allowed_origins: str = "http://localhost:5181"
    app_secret_key: str = Field(min_length=32)
    app_encryption_key: str                      # Fernet key (urlsafe base64, 32 bytes)
    app_release: str = "dev"
    log_level: str = "INFO"
    cookie_secure: bool | None = None            # default: true when APP_BASE_URL is https://

    # ── Database ─────────────────────────────────────────────────────────────
    database_url: str
    migration_database_url: str | None = None
    db_pool_size: int = 10

    # ── Auth ─────────────────────────────────────────────────────────────────
    auth_local_enabled: bool = True
    session_idle_minutes: int = 30
    session_absolute_hours: int = 12
    login_max_failures: int = 5
    login_lockout_minutes: int = 15
    login_rate_per_minute: int = 10

    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: str | None = None
    bootstrap_admin_name: str = "System Administrator"

    # ── DWS Hub SSO (OIDC) ───────────────────────────────────────────────────
    sso_enabled: bool = False
    sso_label: str = "Sign in with Downstream Hub"
    hub_issuer: str = "http://localhost:4000"
    hub_client_id: str = ""
    hub_redirect_uri: str = "http://localhost:5181/api/auth/sso/callback"
    sso_auto_provision: bool = False
    sso_default_role: str = "viewer"
    sso_http_timeout_seconds: float = 10.0

    # ── LSEG DataScope ───────────────────────────────────────────────────────
    lseg_username: str = ""
    lseg_password: str = ""
    lseg_base_url: str = "https://selectapi.datascope.lseg.com/RestApi/v1"
    lseg_lookback_days: int = 7
    lseg_history_start: str = "2015-01-01"
    lseg_ric_chunk: int = 100

    # ── Pipeline ─────────────────────────────────────────────────────────────
    pipeline_cron: str = "0 8 * * mon-fri"
    pipeline_tz: str = "Asia/Jakarta"
    demo_start_date: str = "2017-01-02"

    # ── Claude API (AI narrative, News extraction, event Calendar) ───────────
    anthropic_api_key: str = ""
    claude_model: str = "claude-opus-5-5"
    claude_fallbacks: bool = True          # server-side refusal fallback (beta server-side-fallback-2026-07-01)

    # ── Intelligence sections ────────────────────────────────────────────────
    cftc_cron: str = "0 7 * * sat"          # CFTC publishes Friday 15:30 ET = Saturday ~03:30 WIB
    cftc_history_years: int = 3
    calendar_cron: str = "45 7 * * mon-fri"
    document_max_mb: int = 30

    @field_validator("app_env")
    @classmethod
    def _env(cls, v: str) -> str:
        v = v.lower()
        if v not in {"local", "dev", "sit", "uat", "prod"}:
            raise ValueError("APP_ENV must be local|dev|sit|uat|prod")
        return v

    @property
    def is_local(self) -> bool:
        return self.app_env in {"local", "dev"}

    @property
    def is_https(self) -> bool:
        return self.app_base_url.lower().startswith("https://")

    @property
    def secure_cookies(self) -> bool:
        # Browsers drop Secure cookies on plain-HTTP origins (the staging Hub and apps are HTTP).
        return self.is_https if self.cookie_secure is None else self.cookie_secure

    @property
    def allowed_origins(self) -> set[str]:
        return {o.strip().rstrip("/") for o in self.app_allowed_origins.split(",") if o.strip()}

    @property
    def lseg_configured(self) -> bool:
        return bool(self.lseg_username and self.lseg_password)

    @property
    def claude_configured(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def sso_configured(self) -> bool:
        return self.sso_enabled and bool(self.hub_client_id and self.hub_issuer and self.hub_redirect_uri)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
