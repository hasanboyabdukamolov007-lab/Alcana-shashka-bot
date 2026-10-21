"""Environment configuration. Real secrets stay out of the repository."""

from __future__ import annotations

from functools import lru_cache

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


def to_sync_database_url(url: str) -> str:
    normalized = normalize_database_url(url)
    return normalized.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)


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
