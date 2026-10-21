"""Registration, waiting list, and name changes."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import (
    RegistrationWaitlist,
    Tournament,
    TournamentParticipant,
    TournamentSettings,
    User,
)
from app.domain.enums import (
    CloseReason,
    NotificationType,
    ParticipantStatus,
    TournamentStatus,
)
from app.domain.exceptions import (
    DuplicateRegistration,
    InvalidTransition,
    NotFound,
    PermissionDenied,
)
from app.domain.validation import clean_person_name
from app.presentation.messages import promoted_text
from app.services.notifications import enqueue
from app.services.support import (
    audit,
    ensure_database,
    get_settings_row,
    list_participants,
    require_admin,
    require_live_tournament,
    transition,
)


def display_name(first_name: str, last_name: str) -> str:
    return f"{first_name} {last_name}"


async def get_user_by_telegram(session: AsyncSession, telegram_id: int) -> User | None:
    statement = select(User).where(User.telegram_id == telegram_id)
    return (await session.execute(statement)).scalar_one_or_none()


async def upsert_user(
    session: AsyncSession,
    *,
    telegram_id: int,
    first_name: str,
    last_name: str,
    username: str | None,
) -> User:
    first = clean_person_name(first_name, field="Ism")
    last = clean_person_name(last_name, field="Familiya")
    user = await get_user_by_telegram(session, telegram_id)
    now = datetime.now(UTC)
    if user is None:
        user = User(
            telegram_id=telegram_id,
            username=username,
            first_name=first,
            last_name=last,
            created_at=now,
            updated_at=now,
        )
        session.add(user)
        await session.flush()
        return user
    user.first_name = first
    user.last_name = last
    user.username = username
    user.updated_at = now
    return user


async def _count(
    session: AsyncSession,
    tournament_id: int,
    status: ParticipantStatus,
) -> int:
    statement = (
        select(func.count())
        .select_from(TournamentParticipant)
        .where(
            TournamentParticipant.tournament_id == tournament_id,
            TournamentParticipant.status == status.value,
        )
    )
    return int((await session.execute(statement)).scalar_one())


def _registration_allowed(tournament: Tournament, *, by_admin: bool) -> None:
    status = TournamentStatus(tournament.status)
    if tournament.current_round_number != 0:
        raise InvalidTransition("Turnir boshlangan. Yangi ro‘yxat qabul qilinmaydi.")
    if status == TournamentStatus.ROUND_PAUSED:
        raise InvalidTransition("Turnir vaqtincha to‘xtatilgan.")
    if by_admin:
        if status not in {TournamentStatus.REGISTRATION_OPEN, TournamentStatus.REGISTRATION_CLOSED}:
            raise InvalidTransition("Hozir ishtirokchi qo‘shib bo‘lmaydi.")
        return
    if status == TournamentStatus.REGISTRATION_OPEN:
        return
    if (
        status == TournamentStatus.REGISTRATION_CLOSED
        and tournament.registration_closed_by == CloseReason.CAPACITY.value
    ):
        return
    raise InvalidTransition("Ro‘yxatdan o‘tish yopiq.")


async def _balance_roster(
    session: AsyncSession,
    tournament: Tournament,
    settings_row: TournamentSettings,
) -> list[TournamentParticipant]:
    promoted: list[TournamentParticipant] = []
    if tournament.current_round_number != 0:
        return promoted
    while (
        await _count(session, tournament.id, ParticipantStatus.REGISTERED)
        < settings_row.max_participants
    ):
        statement = (
            select(RegistrationWaitlist)
            .where(
                RegistrationWaitlist.tournament_id == tournament.id,
                RegistrationWaitlist.promoted_at.is_(None),
            )
            .order_by(RegistrationWaitlist.joined_at, RegistrationWaitlist.id)
        )
        waiting = (await session.execute(statement)).scalars().first()
        if waiting is None:
            break
        participant = await session.get(TournamentParticipant, waiting.participant_id)
        if participant is None or participant.status != ParticipantStatus.WAITLIST.value:
            waiting.promoted_at = datetime.now(UTC)
            continue
        participant.status = ParticipantStatus.REGISTERED.value
        participant.updated_at = datetime.now(UTC)
        waiting.promoted_at = datetime.now(UTC)
        user = await session.get(User, participant.user_id)
        if user is not None:
            await enqueue(
                session,
                event_key=f"promote:{tournament.id}:{participant.id}",
                tournament_id=tournament.id,
                recipient_telegram_id=user.telegram_id,
                message_type=NotificationType.WAITLIST_PROMOTED.value,
                text=promoted_text(tournament_name=tournament.name),
            )
        promoted.append(participant)

    seated = await _count(session, tournament.id, ParticipantStatus.REGISTERED)
    status = TournamentStatus(tournament.status)
    if seated >= settings_row.max_participants and status == TournamentStatus.REGISTRATION_OPEN:
        transition(tournament, TournamentStatus.REGISTRATION_CLOSED)
        tournament.registration_closed_by = CloseReason.CAPACITY.value
    elif (
        seated < settings_row.max_participants
        and status == TournamentStatus.REGISTRATION_CLOSED
        and tournament.registration_closed_by == CloseReason.CAPACITY.value
    ):
        waiting_left = await _count(session, tournament.id, ParticipantStatus.WAITLIST)
        if waiting_left == 0:
            transition(tournament, TournamentStatus.REGISTRATION_OPEN)
            tournament.registration_closed_by = None
    return promoted


async def register_participant(
    session: AsyncSession,
    *,
    telegram_id: int,
    first_name: str,
    last_name: str,
    username: str | None,
    actor_telegram_id: int,
    settings: Settings,
    by_admin: bool = False,
) -> TournamentParticipant:
    await ensure_database(session)
    if by_admin:
        require_admin(actor_telegram_id, settings)
    tournament = await require_live_tournament(session)
    _registration_allowed(tournament, by_admin=by_admin)
    user = await upsert_user(
        session,
        telegram_id=telegram_id,
        first_name=first_name,
        last_name=last_name,
        username=username,
    )
    existing = (
        await session.execute(
            select(TournamentParticipant).where(
                TournamentParticipant.tournament_id == tournament.id,
                TournamentParticipant.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise DuplicateRegistration()

    settings_row = await get_settings_row(session, tournament.id)
    seated = await _count(session, tournament.id, ParticipantStatus.REGISTERED)
    now = datetime.now(UTC)
    status = (
        ParticipantStatus.REGISTERED
        if seated < settings_row.max_participants
        else ParticipantStatus.WAITLIST
    )
    participant = TournamentParticipant(
        tournament_id=tournament.id,
        user_id=user.id,
        display_name=display_name(user.first_name, user.last_name),
        status=status.value,
        score=0,
        bye_count=0,
        created_at=now,
        updated_at=now,
    )
    session.add(participant)
    await session.flush()
    if status == ParticipantStatus.WAITLIST:
        session.add(
            RegistrationWaitlist(
                tournament_id=tournament.id,
                user_id=user.id,
                participant_id=participant.id,
                joined_at=now,
            )
        )
    await _balance_roster(session, tournament, settings_row)
    await audit(
        session,
        actor=actor_telegram_id,
        action="registration.add",
        entity_type="tournament_participant",
        entity_id=participant.id,
        details={"status": participant.status, "by_admin": by_admin},
    )
    await session.commit()
    await session.refresh(participant)
    return participant


async def update_participant_name(
    session: AsyncSession,
    *,
    participant_id: int,
    first_name: str,
    last_name: str,
    actor_telegram_id: int,
    settings: Settings,
) -> TournamentParticipant:
    await ensure_database(session)
    participant = await session.get(TournamentParticipant, participant_id)
    if participant is None:
        raise NotFound("Ishtirokchi topilmadi.")
    tournament = await session.get(Tournament, participant.tournament_id)
    if tournament is None or tournament.is_live is not True:
        raise NotFound("Faol turnir yo‘q.")
    user = await session.get(User, participant.user_id)
    if user is None:
        raise NotFound("Foydalanuvchi topilmadi.")
    started = TournamentStatus(tournament.status) not in {
        TournamentStatus.DRAFT,
        TournamentStatus.REGISTRATION_OPEN,
        TournamentStatus.REGISTRATION_CLOSED,
    }
    is_admin = actor_telegram_id == settings.admin_telegram_id
    if started and not is_admin:
        raise PermissionDenied(
            "Turnir boshlanganidan keyin ismni faqat administrator o‘zgartiradi."
        )
    if not started and actor_telegram_id != user.telegram_id and not is_admin:
        raise PermissionDenied()
    user.first_name = clean_person_name(first_name, field="Ism")
    user.last_name = clean_person_name(last_name, field="Familiya")
    user.updated_at = datetime.now(UTC)
    participant.display_name = display_name(user.first_name, user.last_name)
    participant.updated_at = datetime.now(UTC)
    if is_admin and actor_telegram_id != user.telegram_id:
        await audit(
            session,
            actor=actor_telegram_id,
            action="registration.rename",
            entity_type="tournament_participant",
            entity_id=participant.id,
            details={"display_name": participant.display_name},
        )
    await session.commit()
    return participant


async def remove_registration(
    session: AsyncSession,
    *,
    participant_id: int,
    actor_telegram_id: int,
    settings: Settings,
) -> None:
    await ensure_database(session)
    require_admin(actor_telegram_id, settings)
    tournament = await require_live_tournament(session)
    if tournament.current_round_number != 0:
        raise InvalidTransition(
            "Turnir boshlangan. Ishtirokchini faqat chiqarish orqali olib tashlash mumkin."
        )
    if TournamentStatus(tournament.status) not in {
        TournamentStatus.REGISTRATION_OPEN,
        TournamentStatus.REGISTRATION_CLOSED,
    }:
        raise InvalidTransition("Hozir ro‘yxatdan o‘chirish mumkin emas.")
    participant = await session.get(TournamentParticipant, participant_id)
    if participant is None or participant.tournament_id != tournament.id:
        raise NotFound("Ishtirokchi topilmadi.")
    await session.execute(
        delete(RegistrationWaitlist).where(RegistrationWaitlist.participant_id == participant.id)
    )
    await session.delete(participant)
    settings_row = await get_settings_row(session, tournament.id)
    await _balance_roster(session, tournament, settings_row)
    await audit(
        session,
        actor=actor_telegram_id,
        action="registration.remove",
        entity_type="tournament_participant",
        entity_id=participant_id,
        details={},
    )
    await session.commit()


async def list_roster(session: AsyncSession, tournament_id: int) -> list[TournamentParticipant]:
    return await list_participants(session, tournament_id)
