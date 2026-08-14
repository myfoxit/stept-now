"""Application settings.

Everything defaults to a zero-dependency dev setup: SQLite database, in-memory
pub/sub + task queue, local disk storage, mock AI provider. Set STEPT_DATABASE_URL
/ STEPT_REDIS_URL to switch to the full Postgres + Redis stack.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SECRET_KEY = "dev-secret-key-change-me"
# Values that must never reach prod: our own default plus the one .env.example
# suggests, which is exactly what a hurried operator copies verbatim.
INSECURE_SECRET_KEYS = frozenset({DEFAULT_SECRET_KEY, "change-me-in-prod", "changeme", "secret"})
MIN_SECRET_KEY_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="STEPT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["dev", "test", "prod"] = "dev"
    secret_key: str = DEFAULT_SECRET_KEY

    backend_port: int = 8600
    public_base_url: str = "http://localhost:8600"
    app_base_url: str = "http://localhost:5273"
    cors_origins: list[str] = []

    # Set once the recorder is listed, so the dashboard can offer a one-click
    # install instead of the load-unpacked walkthrough.
    extension_web_store_url: str = ""

    database_url: str = "sqlite+aiosqlite:///./stept.db"
    redis_url: str | None = None

    storage_dir: Path = Path("./data/uploads")
    max_upload_mb: int = 25

    smtp_host: str | None = None
    smtp_port: int = 25
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_tls: bool = False
    email_from: str = "Stept <no-reply@stept.local>"

    # W11 integrations. Instance-wide OAuth app credentials (a workspace-scoped
    # IntegrationAppCredential row overrides these). Absent ⇒ the provider shows
    # as "needs setup" until a workspace credential is saved.
    google_client_id: str | None = None
    google_client_secret: str | None = None
    microsoft_client_id: str | None = None
    microsoft_client_secret: str | None = None
    slack_client_id: str | None = None
    slack_client_secret: str | None = None
    slack_signing_secret: str | None = None
    notion_client_id: str | None = None
    notion_client_secret: str | None = None
    confluence_client_id: str | None = None
    confluence_client_secret: str | None = None
    github_client_id: str | None = None
    github_client_secret: str | None = None
    # Social login. Google login reuses google_client_id unless a dedicated
    # login app is configured here; GitHub login uses github_client_id.
    google_login_client_id: str | None = None
    google_login_client_secret: str | None = None
    # Domain that receives forwarded support mail; each email inbox gets an
    # auto-generated in-{hex}@{this} forward-to address when set.
    inbound_email_domain: str | None = None
    # Test/dev only: per-provider base-URL overrides ({"google": "http://…"})
    # so OAuth round trips are provable against a local stub provider.
    oauth_base_override: dict[str, str] = {}

    embedding_dim: int = 384

    # Billing (Stripe). All unset ⇒ billing is disabled and every feature is
    # entitled — self-hosted installs must never be gated.
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    stripe_publishable_key: str | None = None
    stripe_price_cloud: str | None = None
    stripe_price_business: str | None = None

    # Outbound crawler identity + optional egress proxy (per-instance; the
    # crawl fleet sets a different proxy per worker for distinct egress IPs).
    crawl_user_agent: str = "SteptBot/1.0 (+https://stepped.ai)"
    crawl_proxy_url: str | None = None

    # In-process periodic scheduler (source re-sync, SLA scans, campaign sends).
    # Never runs under env=test — tests invoke scheduled jobs directly.
    scheduler_enabled: bool = True
    scheduler_tick_seconds: float = 15.0

    # Open registration. False turns an internet-facing instance invite-only:
    # password signup and first-time social login are refused unless the email
    # holds a pending invitation — existing accounts and invitation acceptance
    # are never affected.
    allow_signup: bool = True

    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 30
    # Reuse of a just-rotated refresh token within this window is treated as a
    # benign race (second tab, lost Set-Cookie response, bfcache replay) and gets
    # a sibling token instead of tripping theft detection. Auth0-style.
    refresh_rotation_grace_seconds: int = 60
    invitation_ttl_days: int = 7
    rate_limit_enabled: bool = True
    # Per-API-key ceiling for MCP traffic (the /mcp workspace surface and
    # /mcp/agents/{id} alike), calls per 60s window. 0 disables. Without Redis
    # the window is per-process, like every limit in app/core/ratelimit.py.
    mcp_rate_limit_per_minute: int = 120
    # How many reverse proxies append to X-Forwarded-For before the request
    # reaches us. 0 (default) = we are the edge, so the header is untrusted.
    trusted_proxy_hops: int = 0

    # Swagger UI + the OpenAPI document. Handy in dev, an inventory of the whole
    # attack surface in prod — off there unless deliberately re-enabled.
    expose_api_docs: bool | None = None

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def upload_limit_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def docs_enabled(self) -> bool:
        return self.env != "prod" if self.expose_api_docs is None else self.expose_api_docs

    def assert_production_ready(self) -> None:
        """Refuse to serve prod traffic on a config that cannot keep a secret.

        ``secret_key`` signs every JWT (access, refresh, widget, extension) and
        derives the Fernet key for stored provider credentials. Booting prod with
        the shipped default means anyone who has read the source can mint an
        access token for any user and decrypt every workspace's API keys, so this
        is a startup failure rather than a warning someone scrolls past.
        """
        if self.env != "prod":
            return
        problems: list[str] = []
        if self.secret_key in INSECURE_SECRET_KEYS:
            problems.append(
                "STEPT_SECRET_KEY is still the built-in default — generate one with "
                "`python -c 'import secrets; print(secrets.token_urlsafe(48))'`"
            )
        elif len(self.secret_key) < MIN_SECRET_KEY_LENGTH:
            problems.append(
                f"STEPT_SECRET_KEY is {len(self.secret_key)} chars; "
                f"HMAC-SHA256 wants at least {MIN_SECRET_KEY_LENGTH}"
            )
        if self.public_base_url.startswith("http://") and "localhost" not in self.public_base_url:
            problems.append("STEPT_PUBLIC_BASE_URL must be https in prod")
        if self.app_base_url.startswith("http://") and "localhost" not in self.app_base_url:
            problems.append("STEPT_APP_BASE_URL must be https in prod")
        if problems:
            raise RuntimeError("Refusing to start with env=prod:\n  - " + "\n  - ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Test helper: force settings to be rebuilt from the environment."""
    get_settings.cache_clear()
