"""Async engine helpers."""

from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings, normalize_database_url


def _enable_sqlite_foreign_keys(engine: AsyncEngine) -> None:
    @event.listens_for(engine.sync_engine, "connect")
    def _on_connect(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def make_engine(url: str) -> AsyncEngine:
    normalized = normalize_database_url(url)
    engine = create_async_engine(normalized, pool_pre_ping=True)
    if normalized.startswith("sqlite"):
        _enable_sqlite_foreign_keys(engine)
    return engine


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


def engine_from_settings(settings: Settings) -> AsyncEngine:
    return make_engine(settings.database_url)
