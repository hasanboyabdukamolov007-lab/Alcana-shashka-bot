"""Process entrypoint."""

from __future__ import annotations

import asyncio
import logging
import os

from aiogram import Bot

from app.config import get_settings, validate_runtime_settings
from app.db.session import engine_from_settings, make_sessionmaker
from app.jobs import backup_loop, outbox_loop
from app.logging_setup import setup_logging
from app.services.recovery import recover_after_restart
from app.telegram.bot import build_dispatcher
from app.telegram.sender import AiogramSender

logger = logging.getLogger(__name__)


async def _health_server(port: int) -> asyncio.AbstractServer:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.read(1024)
        except Exception:
            pass
        body = b'{"status":"ok"}'
        header = (
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
            + str(len(body)).encode()
            + b"\r\nConnection: close\r\n\r\n"
        )
        writer.write(header + body)
        await writer.drain()
        writer.close()

    return await asyncio.start_server(handle, "0.0.0.0", port)


async def run() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    validate_runtime_settings(settings)
    engine = engine_from_settings(settings)
    sessionmaker = make_sessionmaker(engine)
    async with sessionmaker() as session:
        report = await recover_after_restart(session)
    logger.info(
        "recovered tournament=%s status=%s uncertain=%s pending=%s",
        report.active_tournament_id,
        report.active_status,
        report.uncertain_notifications,
        report.pending_notifications,
    )
    bot = Bot(token=settings.bot_token)
    dispatcher = build_dispatcher(settings, sessionmaker)
    port = os.environ.get("PORT")
    health = None
    if port:
        health = await _health_server(int(port))
        logger.info("health endpoint listening on %s", port)
    sender = AiogramSender(bot)
    tasks = [
        asyncio.create_task(outbox_loop(sessionmaker, sender)),
        asyncio.create_task(backup_loop(sessionmaker, settings)),
    ]
    try:
        await dispatcher.start_polling(
            bot,
            allowed_updates=["message", "callback_query", "my_chat_member"],
        )
    finally:
        for task in tasks:
            task.cancel()
        if health is not None:
            health.close()
            await health.wait_closed()
        await bot.session.close()
        await engine.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
