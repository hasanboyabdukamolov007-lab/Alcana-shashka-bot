"""Dispatcher factory."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.telegram.handlers import setup_routers
from app.telegram.middlewares import DatabaseSessionMiddleware


class SettingsMiddleware(BaseMiddleware):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        data["settings"] = self.settings
        return await handler(event, data)


def build_dispatcher(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> Dispatcher:
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.update.middleware(SettingsMiddleware(settings))
    dispatcher.update.middleware(DatabaseSessionMiddleware(sessionmaker))
    for router in setup_routers(settings):
        dispatcher.include_router(router)
    return dispatcher
