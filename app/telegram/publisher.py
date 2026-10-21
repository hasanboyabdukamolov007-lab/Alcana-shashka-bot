"""Persist and refresh group messages."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ConnectedGroup, TelegramMessage, Tournament
from app.domain.enums import GroupMessageType, NotificationType, TournamentStatus
from app.domain.exceptions import MessageNotFoundError
from app.presentation.messages import split_text
from app.services.groups import mark_group_inactive
from app.services.notifications import DeliveryError, enqueue
from app.services.views import (
    final_public_text,
    registration_message,
    round_public_text,
    standings_text,
    tiebreak_public_text,
)


class GroupPublisher:
    def __init__(self, sender, bot_username: str) -> None:
        self.sender = sender
        self.bot_username = bot_username

    async def sync(self, session: AsyncSession, tournament: Tournament) -> None:
        if tournament.group_id is None:
            return
        group = await session.get(ConnectedGroup, tournament.group_id)
        if group is None:
            return
        if not group.is_active:
            return
        try:
            await self._sync(session, tournament, group)
        except (DeliveryError, MessageNotFoundError):
            await mark_group_inactive(
                session,
                telegram_group_id=group.telegram_group_id,
                actor_telegram_id=tournament.created_by,
            )
            await enqueue(
                session,
                event_key=f"admin-alert:group:{tournament.id}:{group.id}",
                tournament_id=tournament.id,
                recipient_telegram_id=tournament.created_by,
                message_type=NotificationType.ADMIN_ALERT.value,
                text=(
                    f"Bot “{group.title}” guruhida xabar yubora olmadi. "
                    "Turnir shaxsiy chatda davom etadi."
                ),
            )
            await session.commit()

    async def _sync(
        self,
        session: AsyncSession,
        tournament: Tournament,
        group: ConnectedGroup,
    ) -> None:
        status = tournament.status
        if status in {
            TournamentStatus.REGISTRATION_OPEN.value,
            TournamentStatus.REGISTRATION_CLOSED.value,
        } or (
            status == TournamentStatus.ROUND_PAUSED.value and tournament.current_round_number == 0
        ):
            from app.telegram.keyboards import registration_keyboard

            text = await registration_message(session, tournament)
            await self.upsert(
                session,
                tournament=tournament,
                group=group,
                message_type=GroupMessageType.REGISTRATION.value,
                text=text,
                reply_markup=registration_keyboard(self.bot_username, tournament.id),
                pin=status == TournamentStatus.REGISTRATION_OPEN.value,
            )
            if status == TournamentStatus.REGISTRATION_OPEN.value:
                await self._unpin_previous_registrations(session, group, tournament.id)
        if tournament.current_round_number > 0 and status not in {
            TournamentStatus.DRAFT.value,
            TournamentStatus.PAIRING_PREVIEW.value,
        }:
            text = await round_public_text(session, tournament, tournament.current_round_number)
            await self.upsert(
                session,
                tournament=tournament,
                group=group,
                message_type=f"{GroupMessageType.ROUND.value}:{tournament.current_round_number}",
                text=text,
            )
            await self.upsert(
                session,
                tournament=tournament,
                group=group,
                message_type=GroupMessageType.STANDINGS.value,
                text=await standings_text(session, tournament),
            )
        if status in {
            TournamentStatus.TIEBREAK_PENDING.value,
            TournamentStatus.TIEBREAK_ACTIVE.value,
        }:
            await self.upsert(
                session,
                tournament=tournament,
                group=group,
                message_type=GroupMessageType.TIEBREAK.value,
                text=await tiebreak_public_text(session, tournament),
            )
        if status == TournamentStatus.COMPLETED.value:
            await self.upsert(
                session,
                tournament=tournament,
                group=group,
                message_type=GroupMessageType.FINAL.value,
                text=await final_public_text(session, tournament),
            )
        if status == TournamentStatus.CANCELLED.value:
            await self.upsert(
                session,
                tournament=tournament,
                group=group,
                message_type=GroupMessageType.CANCELLED.value,
                text=await final_public_text(session, tournament),
            )

    async def upsert(
        self,
        session: AsyncSession,
        *,
        tournament: Tournament,
        group: ConnectedGroup,
        message_type: str,
        text: str,
        reply_markup=None,
        pin: bool = False,
    ) -> None:
        parts = split_text(text)
        for index, part in enumerate(parts):
            markup = reply_markup if index == 0 else None
            await self._upsert_part(
                session,
                tournament=tournament,
                group=group,
                message_type=message_type,
                part=index,
                text=part,
                reply_markup=markup,
                pin=pin and index == 0,
            )

    async def _upsert_part(
        self,
        session: AsyncSession,
        *,
        tournament: Tournament,
        group: ConnectedGroup,
        message_type: str,
        part: int,
        text: str,
        reply_markup,
        pin: bool,
    ) -> TelegramMessage:
        statement = select(TelegramMessage).where(
            TelegramMessage.tournament_id == tournament.id,
            TelegramMessage.message_type == message_type,
            TelegramMessage.part == part,
        )
        row = (await session.execute(statement)).scalar_one_or_none()
        if row is not None and row.telegram_message_id is not None:
            try:
                await self.sender.edit_message(
                    group.telegram_group_id,
                    row.telegram_message_id,
                    text,
                    reply_markup,
                )
                row.updated_at = datetime.now(UTC)
                await session.commit()
                return row
            except MessageNotFoundError:
                row.telegram_message_id = None
        message_id = await self.sender.send_message(group.telegram_group_id, text, reply_markup)
        if row is None:
            row = TelegramMessage(
                tournament_id=tournament.id,
                group_id=group.id,
                chat_id=group.telegram_group_id,
                message_type=message_type,
                part=part,
                telegram_message_id=message_id,
                is_pinned=pin,
            )
            session.add(row)
        else:
            row.telegram_message_id = message_id
            row.chat_id = group.telegram_group_id
            row.updated_at = datetime.now(UTC)
        if pin:
            await self.sender.pin_message(group.telegram_group_id, message_id)
            row.is_pinned = True
        await session.commit()
        await session.refresh(row)
        return row

    async def _unpin_previous_registrations(
        self,
        session: AsyncSession,
        group: ConnectedGroup,
        tournament_id: int,
    ) -> None:
        statement = select(TelegramMessage).where(
            TelegramMessage.chat_id == group.telegram_group_id,
            TelegramMessage.message_type == GroupMessageType.REGISTRATION.value,
            TelegramMessage.is_pinned.is_(True),
            TelegramMessage.tournament_id != tournament_id,
        )
        rows = (await session.execute(statement)).scalars().all()
        for row in rows:
            if row.telegram_message_id is None:
                continue
            try:
                await self.sender.unpin_message(group.telegram_group_id, row.telegram_message_id)
            except Exception:
                pass
            row.is_pinned = False
        if rows:
            await session.commit()
