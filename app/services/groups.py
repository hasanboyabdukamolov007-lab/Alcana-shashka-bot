"""Connected Telegram groups."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import ConnectedGroup
from app.domain.exceptions import GroupPermissionError, InvalidConfig, NotFound
from app.services.support import audit, ensure_database, get_live_tournament, require_admin


@dataclass(frozen=True)
class BotMembership:
    status: str
    can_pin_messages: bool
    can_send_messages: bool = True


def membership_is_sufficient(membership: BotMembership) -> bool:
    if not membership.can_send_messages:
        return False
    return membership.status in {"creator", "administrator", "member", "restricted"}


async def connect_group(
    session: AsyncSession,
    *,
    actor_telegram_id: int,
    settings: Settings,
    telegram_group_id: int,
    title: str,
    membership: BotMembership,
) -> ConnectedGroup:
    await ensure_database(session)
    require_admin(actor_telegram_id, settings)
    if not membership_is_sufficient(membership):
        raise GroupPermissionError(
            "Bot guruhda bo‘lishi va xabar yubora olishi kerak. Administrator bo‘lishi shart emas."
        )
    cleaned = title.strip() or "Guruh"
    can_pin = membership.status == "creator" or membership.can_pin_messages
    existing = (
        await session.execute(
            select(ConnectedGroup).where(ConnectedGroup.telegram_group_id == telegram_group_id)
        )
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if existing is None:
        existing = ConnectedGroup(
            telegram_group_id=telegram_group_id,
            title=cleaned[:255],
            connected_by=actor_telegram_id,
            is_active=True,
            can_pin=can_pin,
            created_at=now,
            updated_at=now,
        )
        session.add(existing)
    else:
        existing.title = cleaned[:255]
        existing.is_active = True
        existing.can_pin = can_pin
        existing.updated_at = now
    await session.flush()
    await audit(
        session,
        actor=actor_telegram_id,
        action="group.connect",
        entity_type="connected_group",
        entity_id=existing.id,
        details={"telegram_group_id": telegram_group_id, "title": existing.title},
    )
    await session.commit()
    await session.refresh(existing)
    return existing


async def mark_group_inactive(
    session: AsyncSession,
    *,
    telegram_group_id: int,
    actor_telegram_id: int,
) -> None:
    group = (
        await session.execute(
            select(ConnectedGroup).where(ConnectedGroup.telegram_group_id == telegram_group_id)
        )
    ).scalar_one_or_none()
    if group is None:
        return
    group.is_active = False
    group.updated_at = datetime.now(UTC)
    await audit(
        session,
        actor=actor_telegram_id,
        action="group.removed",
        entity_type="connected_group",
        entity_id=group.id,
        details={"telegram_group_id": telegram_group_id},
    )
    await session.commit()


async def list_active_groups(session: AsyncSession) -> list[ConnectedGroup]:
    statement = (
        select(ConnectedGroup)
        .where(ConnectedGroup.is_active.is_(True))
        .order_by(ConnectedGroup.title)
    )
    return list((await session.execute(statement)).scalars().all())


async def disconnect_group(
    session: AsyncSession,
    *,
    actor_telegram_id: int,
    settings: Settings,
    group_id: int,
) -> None:
    await ensure_database(session)
    require_admin(actor_telegram_id, settings)
    group = await session.get(ConnectedGroup, group_id)
    if group is None:
        raise NotFound("Guruh topilmadi.")
    live = await get_live_tournament(session)
    if live is not None and live.group_id == group.id:
        raise InvalidConfig("Faol turnir shu guruhga ulangan. Avval turnirni yakunlang.")
    group.is_active = False
    group.updated_at = datetime.now(UTC)
    await audit(
        session,
        actor=actor_telegram_id,
        action="group.disconnect",
        entity_type="connected_group",
        entity_id=group.id,
        details={},
    )
    await session.commit()
