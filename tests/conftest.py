"""Shared fixtures."""

from __future__ import annotations

import pytest
from app.config import Settings
from app.db.base import Base
from app.db.session import make_engine, make_sessionmaker
from app.services.flow import (
    choose_group,
    create_draft,
    open_registration,
    update_limits,
    update_prizes,
)
from app.services.groups import BotMembership, connect_group
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
async def session() -> AsyncSession:
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = make_sessionmaker(engine)
    async with factory() as db:
        yield db
    await engine.dispose()


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        bot_token="123456:TESTTOKEN",
        admin_telegram_id=100,
        database_url="sqlite+aiosqlite:///:memory:",
        app_env="test",
        log_level="WARNING",
        backup_storage_path=str(tmp_path / "backups"),
        backup_retention_days=7,
        backup_hour_utc=2,
    )


async def open_cup(
    session: AsyncSession,
    settings: Settings,
    *,
    name: str = "Bahor kubogi",
    min_participants: int = 2,
    max_participants: int = 8,
    swiss_rounds: int = 1,
    group_id: int = -1001,
) -> int:
    group = await connect_group(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
        telegram_group_id=group_id,
        title="Shashka klubi",
        membership=BotMembership(status="administrator", can_pin_messages=True),
    )
    await create_draft(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
        name=name,
    )
    await update_limits(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
        min_participants=min_participants,
        max_participants=max_participants,
        swiss_rounds=swiss_rounds,
    )
    await update_prizes(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
        first=200_000,
        second=100_000,
        third=50_000,
    )
    await choose_group(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
        group_id=group.id,
    )
    tournament = await open_registration(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
    )
    return tournament.id
