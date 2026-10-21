"""Notification outbox. Event keys make ordinary retries idempotent."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import NotificationOutbox, utcnow
from app.domain.enums import NotificationStatus, NotificationType
from app.domain.exceptions import InvalidConfig
from app.presentation.messages import blocked_admin_text
from app.services.support import audit, ensure_database, require_admin

MAX_AUTO_ATTEMPTS = 5


class DeliveryResult(StrEnum):
    SENT = "SENT"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    UNCERTAIN = "UNCERTAIN"
    RATE_LIMITED = "RATE_LIMITED"


class DeliveryError(Exception):
    def __init__(self, result: DeliveryResult, retry_after: int = 0) -> None:
        super().__init__(result.value)
        self.result = result
        self.retry_after = retry_after


def backoff_seconds(attempt: int) -> int:
    return min(300, 2**attempt)


def apply_delivery(
    row: NotificationOutbox,
    result: DeliveryResult,
    now: datetime,
    *,
    retry_after: int | None = None,
) -> str | None:
    """Update one outbox row. Return ``blocked`` when an admin alert is required."""

    row.updated_at = now
    if result == DeliveryResult.SENT:
        row.status = NotificationStatus.SENT.value
        row.last_error = None
        return None
    if result == DeliveryResult.UNCERTAIN:
        row.status = NotificationStatus.UNCERTAIN.value
        row.last_error = "Yuborish natijasi noma’lum. Avtomatik qayta yuborilmaydi."
        return None
    if result == DeliveryResult.RATE_LIMITED:
        row.status = NotificationStatus.PENDING.value
        delay = retry_after if retry_after is not None else 5
        row.next_attempt_at = now + timedelta(seconds=delay)
        row.last_error = "Telegram tezlik limiti."
        return None
    row.attempt_count += 1
    if result == DeliveryResult.BLOCKED:
        row.status = NotificationStatus.FAILED.value
        row.last_error = "Foydalanuvchi botni bloklagan."
        return "blocked"
    row.last_error = "Yuborib bo‘lmadi."
    if row.attempt_count >= MAX_AUTO_ATTEMPTS:
        row.status = NotificationStatus.FAILED.value
        return None
    row.status = NotificationStatus.PENDING.value
    row.next_attempt_at = now + timedelta(seconds=backoff_seconds(row.attempt_count))
    return None


async def enqueue(
    session: AsyncSession,
    *,
    event_key: str,
    tournament_id: int | None,
    recipient_telegram_id: int,
    message_type: str,
    text: str,
) -> NotificationOutbox:
    existing = (
        await session.execute(
            select(NotificationOutbox).where(NotificationOutbox.event_key == event_key)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    row = NotificationOutbox(
        event_key=event_key,
        tournament_id=tournament_id,
        recipient_telegram_id=recipient_telegram_id,
        message_type=message_type,
        payload={"text": text},
        status=NotificationStatus.PENDING.value,
        attempt_count=0,
        next_attempt_at=utcnow(),
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    session.add(row)
    await session.flush()
    return row


async def list_failed(
    session: AsyncSession, tournament_id: int | None = None
) -> list[NotificationOutbox]:
    statement = select(NotificationOutbox).where(
        NotificationOutbox.status.in_(
            [NotificationStatus.FAILED.value, NotificationStatus.UNCERTAIN.value]
        )
    )
    if tournament_id is not None:
        statement = statement.where(NotificationOutbox.tournament_id == tournament_id)
    statement = statement.order_by(NotificationOutbox.id)
    return list((await session.execute(statement)).scalars().all())


async def retry_notification(
    session: AsyncSession,
    *,
    actor_telegram_id: int,
    settings: Settings,
    notification_id: int,
    explicit_uncertain: bool = False,
) -> NotificationOutbox:
    await ensure_database(session)
    require_admin(actor_telegram_id, settings)
    row = await session.get(NotificationOutbox, notification_id)
    if row is None:
        raise InvalidConfig("Bildirishnoma topilmadi.")
    if row.status == NotificationStatus.UNCERTAIN.value:
        if not explicit_uncertain:
            raise InvalidConfig(
                "Noma’lum holatdagi xabar avtomatik qayta yuborilmaydi. Aniq tasdiq kerak."
            )
    elif (
        row.status == NotificationStatus.FAILED.value
        and row.message_type == NotificationType.PRIZE.value
    ):
        pass
    else:
        raise InvalidConfig("Faqat yuborilmagan mukofot tabrigini qayta yuborish mumkin.")
    row.status = NotificationStatus.PENDING.value
    row.attempt_count = 0
    row.next_attempt_at = datetime.now(UTC)
    row.updated_at = datetime.now(UTC)
    await audit(
        session,
        actor=actor_telegram_id,
        action="notification.retry",
        entity_type="notification_outbox",
        entity_id=row.id,
        details={"explicit_uncertain": explicit_uncertain, "event_key": row.event_key},
    )
    await session.commit()
    return row


async def process_outbox_once(session: AsyncSession, sender, *, now: datetime | None = None) -> int:
    """Send due PENDING rows. Returns how many were attempted.

    Rows are marked SENDING and committed before the network call. A crash in
    that window is recovered as UNCERTAIN on the next process start.
    """

    moment = now or datetime.now(UTC)
    statement = (
        select(NotificationOutbox)
        .where(
            NotificationOutbox.status == NotificationStatus.PENDING.value,
            NotificationOutbox.next_attempt_at <= moment,
        )
        .order_by(NotificationOutbox.id)
        .limit(20)
    )
    rows = list((await session.execute(statement)).scalars().all())
    if not rows:
        return 0
    for row in rows:
        row.status = NotificationStatus.SENDING.value
        row.updated_at = moment
    await session.commit()
    for row in rows:
        text = str(row.payload.get("text", ""))
        try:
            await sender.send_message(row.recipient_telegram_id, text)
            flag = apply_delivery(row, DeliveryResult.SENT, moment)
        except DeliveryError as exc:
            flag = apply_delivery(
                row,
                exc.result,
                moment,
                retry_after=exc.retry_after,
            )
        except Exception:
            flag = apply_delivery(row, DeliveryResult.FAILED, moment)
        if flag == "blocked":
            await enqueue(
                session,
                event_key=f"admin-alert:blocked:{row.id}",
                tournament_id=row.tournament_id,
                recipient_telegram_id=await _admin_recipient(session, row),
                message_type=NotificationType.ADMIN_ALERT.value,
                text=blocked_admin_text(
                    telegram_id=row.recipient_telegram_id,
                    message_type=row.message_type,
                ),
            )
    await session.commit()
    return len(rows)


async def _admin_recipient(session: AsyncSession, row: NotificationOutbox) -> int:
    """Alert the configured admin. The id is stored on the alert payload path via settings later.

    The worker does not always have Settings. The blocked alert recipient is the
    actor recorded as created_by of the tournament when available; otherwise the
    recipient field is filled by `enqueue_admin_alert` at the call site.
    """

    if row.tournament_id is None:
        return row.recipient_telegram_id
    from app.db.models import Tournament

    tournament = await session.get(Tournament, row.tournament_id)
    if tournament is None:
        return row.recipient_telegram_id
    return tournament.created_by


async def mark_sending_uncertain(session: AsyncSession) -> int:
    statement = select(NotificationOutbox).where(
        NotificationOutbox.status == NotificationStatus.SENDING.value
    )
    rows = list((await session.execute(statement)).scalars().all())
    for row in rows:
        apply_delivery(row, DeliveryResult.UNCERTAIN, datetime.now(UTC))
        row.last_error = "Jarayon qayta ishga tushganda yuborish natijasi noma’lum."
    if rows:
        await session.commit()
    return len(rows)
