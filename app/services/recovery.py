"""Process restart recovery. This does not restore a lost database."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import NotificationOutbox
from app.domain.enums import NotificationStatus
from app.services.notifications import mark_sending_uncertain
from app.services.support import get_live_tournament


@dataclass(frozen=True)
class RecoveryReport:
    active_tournament_id: int | None
    active_status: str | None
    uncertain_notifications: int
    pending_notifications: int


async def recover_after_restart(session: AsyncSession) -> RecoveryReport:
    uncertain = await mark_sending_uncertain(session)
    tournament = await get_live_tournament(session)
    pending = int(
        (
            await session.execute(
                select(func.count())
                .select_from(NotificationOutbox)
                .where(NotificationOutbox.status == NotificationStatus.PENDING.value)
            )
        ).scalar_one()
    )
    return RecoveryReport(
        active_tournament_id=None if tournament is None else tournament.id,
        active_status=None if tournament is None else tournament.status,
        uncertain_notifications=uncertain,
        pending_notifications=pending,
    )
