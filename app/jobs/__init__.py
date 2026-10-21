"""Scheduled outbox delivery and database backups."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from app.services.backup import create_backup
from app.services.notifications import process_outbox_once

logger = logging.getLogger(__name__)


async def outbox_loop(sessionmaker, sender) -> None:
    while True:
        try:
            async with sessionmaker() as session:
                await process_outbox_once(session, sender)
        except Exception:
            logger.exception("notification outbox failed")
        await asyncio.sleep(2)


async def backup_loop(sessionmaker, settings) -> None:
    while True:
        now = datetime.now(UTC)
        target = now.replace(
            hour=settings.backup_hour_utc,
            minute=0,
            second=0,
            microsecond=0,
        )
        if target <= now:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        try:
            async with sessionmaker() as session:
                job = await create_backup(session, settings)
            logger.info("backup finished path=%s method=%s", job.path, job.method)
        except Exception:
            logger.exception("backup failed")
