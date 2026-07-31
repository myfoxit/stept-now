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


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="STEPT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["dev", "test", "prod"] = "dev"
    secret_key: str = "dev-secret-key-change-me"

    backend_port: int = 8600
    public_base_url: str = "http://localhost:8600"
    app_base_url: str = "http://localhost:5273"
    cors_origins: list[str] = []

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

    embedding_dim: int = 384

    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 30
    invitation_ttl_days: int = 7
    rate_limit_enabled: bool = True

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def upload_limit_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Test helper: force settings to be rebuilt from the environment."""
    get_settings.cache_clear()
