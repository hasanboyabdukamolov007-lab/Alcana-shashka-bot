"""Create a backup file. Usage: python scripts/backup.py"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.db.session import engine_from_settings, make_sessionmaker  # noqa: E402
from app.services.backup import create_backup  # noqa: E402


async def _run() -> None:
    settings = get_settings()
    engine = engine_from_settings(settings)
    sessionmaker = make_sessionmaker(engine)
    async with sessionmaker() as session:
        job = await create_backup(session, settings)
    print(f"{job.status} {job.method} {job.path}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_run())
