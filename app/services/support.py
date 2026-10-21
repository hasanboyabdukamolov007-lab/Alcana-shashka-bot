"""Shared database guards, audit records, and tournament queries."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import (
    AuditLog,
    ByeAssignment,
    ConnectedGroup,
    Match,
    MatchResult,
    Round,
    Tournament,
    TournamentParticipant,
    TournamentSettings,
    utcnow,
)
from app.domain.enums import ParticipantStatus, ResultKind, TournamentStatus
from app.domain.exceptions import DatabaseUnavailable, NotFound, PermissionDenied
from app.domain.scoring import SwissGame
from app.domain.state_machine import assert_transition


async def ensure_database(session: AsyncSession) -> None:
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:
        raise DatabaseUnavailable() from exc


def require_admin(actor_telegram_id: int, settings: Settings) -> None:
    if actor_telegram_id != settings.admin_telegram_id:
        raise PermissionDenied()


async def audit(
    session: AsyncSession,
    *,
    actor: int,
    action: str,
    entity_type: str,
    entity_id: object,
    details: dict | None = None,
) -> None:
    session.add(
        AuditLog(
            actor_telegram_id=actor,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id),
            details=details,
            created_at=utcnow(),
        )
    )


async def get_live_tournament(session: AsyncSession) -> Tournament | None:
    statement = select(Tournament).where(Tournament.is_live.is_(True))
    return (await session.execute(statement)).scalar_one_or_none()


async def require_live_tournament(session: AsyncSession) -> Tournament:
    tournament = await get_live_tournament(session)
    if tournament is None:
        raise NotFound("Faol turnir yo‘q.")
    return tournament


async def get_settings_row(session: AsyncSession, tournament_id: int) -> TournamentSettings:
    statement = select(TournamentSettings).where(TournamentSettings.tournament_id == tournament_id)
    row = (await session.execute(statement)).scalar_one_or_none()
    if row is None:
        raise NotFound("Turnir sozlamalari topilmadi.")
    return row


async def list_participants(
    session: AsyncSession,
    tournament_id: int,
) -> list[TournamentParticipant]:
    statement = (
        select(TournamentParticipant)
        .where(TournamentParticipant.tournament_id == tournament_id)
        .order_by(TournamentParticipant.id)
    )
    return list((await session.execute(statement)).scalars().all())


async def get_group(session: AsyncSession, group_id: int | None) -> ConnectedGroup | None:
    if group_id is None:
        return None
    return await session.get(ConnectedGroup, group_id)


def transition(
    tournament: Tournament,
    new_status: TournamentStatus,
) -> None:
    assert_transition(
        tournament.status,
        new_status,
        previous=tournament.previous_status,
    )
    tournament.status = new_status.value
    tournament.updated_at = datetime.now(UTC)


def pause_from(tournament: Tournament) -> None:
    current = TournamentStatus(tournament.status)
    assert_transition(current, TournamentStatus.ROUND_PAUSED)
    tournament.previous_status = current.value
    tournament.status = TournamentStatus.ROUND_PAUSED.value
    tournament.updated_at = datetime.now(UTC)


def resume_from(tournament: Tournament) -> None:
    assert_transition(
        tournament.status,
        tournament.previous_status or "",
        previous=tournament.previous_status,
    )
    tournament.status = str(tournament.previous_status)
    tournament.previous_status = None
    tournament.updated_at = datetime.now(UTC)


async def load_swiss_games(session: AsyncSession, tournament_id: int) -> list[SwissGame]:
    games: list[SwissGame] = []
    rounds = list(
        (await session.execute(select(Round).where(Round.tournament_id == tournament_id)))
        .scalars()
        .all()
    )
    round_numbers = {item.id: item.number for item in rounds}
    byes = list(
        (
            await session.execute(
                select(ByeAssignment).where(ByeAssignment.tournament_id == tournament_id)
            )
        )
        .scalars()
        .all()
    )
    for bye in byes:
        games.append(
            SwissGame(
                participant_id=bye.participant_id,
                round_number=round_numbers[bye.round_id],
                opponent_id=None,
                points=bye.points_awarded,
                kind=ResultKind.BYE,
            )
        )
    matches = list(
        (await session.execute(select(Match).where(Match.tournament_id == tournament_id)))
        .scalars()
        .all()
    )
    if not matches:
        return games
    match_by_id = {match.id: match for match in matches}
    results = list(
        (
            await session.execute(
                select(MatchResult).where(
                    MatchResult.match_id.in_(match_by_id),
                    MatchResult.is_current.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    for result in results:
        match = match_by_id[result.match_id]
        kind = ResultKind(result.kind)
        games.append(
            SwissGame(
                participant_id=match.player1_id,
                round_number=round_numbers[match.round_id],
                opponent_id=match.player2_id,
                points=result.p1_points,
                kind=kind,
            )
        )
        games.append(
            SwissGame(
                participant_id=match.player2_id,
                round_number=round_numbers[match.round_id],
                opponent_id=match.player1_id,
                points=result.p2_points,
                kind=kind,
            )
        )
    return games


async def recompute_stored_scores(session: AsyncSession, tournament_id: int) -> None:
    games = await load_swiss_games(session, tournament_id)
    participants = await list_participants(session, tournament_id)
    for participant in participants:
        participant.score = sum(
            game.points for game in games if game.participant_id == participant.id
        )
        participant.bye_count = sum(
            1
            for game in games
            if game.participant_id == participant.id and game.kind == ResultKind.BYE
        )
        participant.updated_at = datetime.now(UTC)


def active_ids(participants: list[TournamentParticipant]) -> list[TournamentParticipant]:
    return [
        participant
        for participant in participants
        if participant.status == ParticipantStatus.ACTIVE.value
    ]
