"""Authorization and database session middleware."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class AdminOnlyMiddleware(BaseMiddleware):
    """Reject every admin command and callback that is not from the configured id."""

    def __init__(self, admin_id: int) -> None:
        self.admin_id = admin_id

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        user = getattr(event, "from_user", None)
        user_id = None if user is None else getattr(user, "id", None)
        if user_id != self.admin_id:
            answer = getattr(event, "answer", None)
            if answer is not None:
                text = "Bu amal faqat administrator uchun."
                if getattr(event, "data", None) is not None:
                    await answer(text, show_alert=True)
                else:
                    await answer(text)
            return None
        return await handler(event, data)


class DatabaseSessionMiddleware(BaseMiddleware):
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self.sessionmaker = sessionmaker

    async def __call__(
        self,
        handler: Callable[[Any, dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: dict[str, Any],
    ) -> Any:
        async with self.sessionmaker() as session:
            try:
                await session.execute(text("SELECT 1"))
            except Exception:
                await _report_database_down(event)
                return None
            data["session"] = session
            return await handler(event, data)


async def _report_database_down(event: Any) -> None:
    answer = getattr(event, "answer", None)
    if answer is None:
        return
    text = (
        "Ma’lumotlar bazasi vaqtincha ishlamayapti. O‘zgarish saqlanmadi. Keyinroq urinib ko‘ring."
    )
    if getattr(event, "data", None) is not None:
        await answer(text, show_alert=True)
    else:
        await answer(text)
