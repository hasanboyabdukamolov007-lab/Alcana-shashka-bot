"""Environment configuration. Real secrets stay out of the repository."""

from __future__ import annotations

from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str = ""
    admin_telegram_id: int = 0
    database_url: str = "sqlite+aiosqlite:///./shashka.db"
    app_env: str = "development"
    log_level: str = "INFO"
    backup_storage_path: str = "./backups"
    backup_retention_days: int = 7
    backup_hour_utc: int = 2

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def async_database_url(self) -> str:
        return normalize_database_url(self.database_url)


def normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


def libpq_database_url(url: str) -> str:
    """Return a URL that pg_dump accepts, including sslmode when present."""

    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    for driver in ("postgresql+asyncpg://", "postgresql+psycopg://"):
        if url.startswith(driver):
            return "postgresql://" + url[len(driver) :]
    return url


_SSL_MODES = {"require", "verify-ca", "verify-full", "prefer"}


def asyncpg_engine_options(url: str) -> tuple[str, dict[str, object]]:
    """Convert a hosted Postgres URL into an asyncpg engine URL and connect args.

    DigitalOcean and other managed databases append ``sslmode=require``.
    asyncpg does not accept that query parameter, so it is translated to
    ``connect_args={"ssl": True}``.
    """

    normalized = normalize_database_url(url)
    if not normalized.startswith("postgresql+asyncpg://"):
        return normalized, {}
    parts = urlsplit(normalized)
    kept: list[tuple[str, str]] = []
    sslmode = None
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key == "sslmode":
            sslmode = value
            continue
        if key == "channel_binding":
            continue
        kept.append((key, value))
    connect_args: dict[str, object] = {}
    if sslmode in _SSL_MODES:
        connect_args["ssl"] = True
    cleaned = urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment)
    )
    return cleaned, connect_args


def validate_runtime_settings(settings: Settings) -> None:
    token = settings.bot_token.strip()
    if ":" not in token or token.startswith(":"):
        raise RuntimeError("BOT_TOKEN is required and must be a Telegram bot token.")
    if settings.admin_telegram_id <= 0:
        raise RuntimeError("ADMIN_TELEGRAM_ID is required and must be a positive Telegram id.")
    if not settings.database_url.strip():
        raise RuntimeError("DATABASE_URL is required.")
    if settings.backup_retention_days < 1:
        raise RuntimeError("BACKUP_RETENTION_DAYS must be at least 1.")
    if not 0 <= settings.backup_hour_utc <= 23:
        raise RuntimeError("BACKUP_HOUR_UTC must be between 0 and 23.")


@lru_cache
def get_settings() -> Settings:
    return Settings()
