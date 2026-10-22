"""Alembic environment. Runtime uses the async driver; migrations share the metadata."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from app.config import asyncpg_engine_options, get_settings
from app.db import models  # noqa: F401
from app.db.base import Base
from sqlalchemy.ext.asyncio import create_async_engine

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = get_settings().async_database_url
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:  # noqa: ANN001
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    url, connect_args = asyncpg_engine_options(get_settings().database_url)
    engine = create_async_engine(url, pool_pre_ping=True, connect_args=connect_args)
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
