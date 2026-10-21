"""Daily logical or pg_dump backups with a seven-day retention window.

A backup file is not a substitute for the restart recovery path. Restart
recovery reloads PostgreSQL. Restoration is a separate, explicit operation
used after the primary database is lost.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import DateTime

from app.config import Settings
from app.db.base import Base
from app.db.models import BackupJob, utcnow
from app.domain.enums import BackupStatus
from app.domain.exceptions import InvalidConfig
from app.services.support import ensure_database, get_live_tournament


def retention_cutoff(now: datetime, days: int) -> datetime:
    return now - timedelta(days=days)


def expired_backup_files(
    entries: list[tuple[Path, datetime]],
    now: datetime,
    days: int,
) -> list[Path]:
    cutoff = retention_cutoff(now, days)
    return [path for path, modified in entries if modified < cutoff]


def apply_retention(directory: Path, now: datetime, days: int) -> list[Path]:
    entries: list[tuple[Path, datetime]] = []
    for path in directory.glob("shashka-backup-*"):
        if not path.is_file():
            continue
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        entries.append((path, modified))
    removed: list[Path] = []
    for path in expired_backup_files(entries, now, days):
        path.unlink(missing_ok=True)
        removed.append(path)
    return removed


def _serialize(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


async def dump_database(session: AsyncSession) -> dict:
    tables: dict[str, list[dict]] = {}
    for table in Base.metadata.sorted_tables:
        result = await session.execute(select(table))
        rows = []
        for mapping in result.mappings().all():
            rows.append({key: _serialize(value) for key, value in dict(mapping).items()})
        tables[table.name] = rows
    return {"version": 1, "created_at": datetime.now(UTC).isoformat(), "tables": tables}


def _coerce(column, value):  # noqa: ANN001
    if value is None:
        return None
    if isinstance(column.type, DateTime) and isinstance(value, str):
        return datetime.fromisoformat(value)
    return value


async def restore_logical_backup(
    session: AsyncSession,
    payload: dict,
    *,
    force: bool = False,
) -> None:
    live = await get_live_tournament(session)
    if live is not None and not force:
        raise InvalidConfig("Faol turnir bor. Tiklash faqat aniq tasdiq bilan bajariladi.")
    for table in reversed(Base.metadata.sorted_tables):
        await session.execute(delete(table))
    for table in Base.metadata.sorted_tables:
        raw_rows = payload.get("tables", {}).get(table.name, [])
        if not raw_rows:
            continue
        prepared = []
        for raw in raw_rows:
            prepared.append(
                {column.name: _coerce(column, raw.get(column.name)) for column in table.columns}
            )
        await session.execute(insert(table), prepared)
    await session.commit()
    # The wipe replaces every row. Drop cached instances so later reads
    # see the restored database instead of pre-restore objects.
    session.expire_all()


def _postgres_url(settings: Settings) -> str | None:
    url = settings.database_url
    if (
        url.startswith("postgres://")
        or url.startswith("postgresql://")
        or "+asyncpg" in url
        or "+psycopg" in url
    ):
        from app.config import to_sync_database_url

        return to_sync_database_url(url)
    return None


async def create_backup(
    session: AsyncSession,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> BackupJob:
    await ensure_database(session)
    moment = now or datetime.now(UTC)
    directory = Path(settings.backup_storage_path)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = moment.strftime("%Y%m%dT%H%M%SZ")
    job = BackupJob(
        status=BackupStatus.RUNNING.value,
        path="",
        method="",
        started_at=moment,
    )
    session.add(job)
    await session.commit()
    try:
        sync_url = _postgres_url(settings)
        dump_bin = shutil.which("pg_dump")
        if sync_url and dump_bin and sync_url.startswith("postgresql"):
            path = directory / f"shashka-backup-{stamp}.dump"
            subprocess.run(
                [dump_bin, "--format=custom", "--file", str(path), "--dbname", sync_url],
                check=True,
                capture_output=True,
                text=True,
            )
            method = "pg_dump"
        else:
            path = directory / f"shashka-backup-{stamp}.json"
            payload = await dump_database(session)
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            method = "logical-json"
        apply_retention(directory, moment, settings.backup_retention_days)
        job.status = BackupStatus.SUCCEEDED.value
        job.path = str(path)
        job.method = method
        job.size_bytes = path.stat().st_size
        job.finished_at = utcnow()
        await session.commit()
    except Exception as exc:
        job.status = BackupStatus.FAILED.value
        job.error = str(exc)
        job.finished_at = utcnow()
        await session.commit()
        raise
    return job
