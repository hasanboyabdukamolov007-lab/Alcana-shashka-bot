"""Restore a backup.

Logical JSON backups produced when pg_dump is unavailable:

    python scripts/restore.py path/to/shashka-backup-....json --force

Custom-format PostgreSQL dumps produced by pg_dump:

    pg_restore --clean --if-exists --dbname "$DATABASE_URL" path/to/file.dump

Restart recovery is not database restoration. Use this script only after the
primary database is lost or you intentionally replace it. `--force` is required
when a live tournament exists. The bot does not declare results saved if the
database is down.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.db.session import engine_from_settings, make_sessionmaker  # noqa: E402
from app.services.backup import restore_logical_backup  # noqa: E402


async def _run(path: Path, force: bool) -> None:
    if path.suffix == ".dump":
        raise SystemExit(
            "This file is a pg_dump custom archive. Restore it with pg_restore, "
            "not the JSON loader. See DEPLOYMENT.md."
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    settings = get_settings()
    engine = engine_from_settings(settings)
    sessionmaker = make_sessionmaker(engine)
    async with sessionmaker() as session:
        await restore_logical_backup(session, payload, force=force)
    await engine.dispose()
    print(f"Restored logical backup from {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Restore a Shashka logical backup")
    parser.add_argument("path")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    asyncio.run(_run(Path(args.path), args.force))


if __name__ == "__main__":
    main()
