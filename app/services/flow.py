"""Tournament lifecycle: draft, rounds, results, prize playoffs, and final approval."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import (
    ByeAssignment,
    ConnectedGroup,
    Match,
    MatchResult,
    PairingVariant,
    PairingVariantMatch,
    Round,
    StandingsSnapshot,
    TiebreakMatch,
    TiebreakStage,
    Tournament,
    TournamentParticipant,
    TournamentSettings,
    User,
    Withdrawal,
)
from app.domain.enums import (
    BYE_POINTS,
    TERMINAL_STATUSES,
    CloseReason,
    MatchStatus,
    NotificationType,
    Outcome,
    ParticipantStatus,
    ResultKind,
    RoundStatus,
    TiebreakFormat,
    TiebreakStageStatus,
    TournamentStatus,
)
from app.domain.exceptions import (
    ActiveTournamentExists,
    AlreadyFinalized,
    InfeasiblePairing,
    InvalidConfig,
    InvalidTransition,
    MinimumNotMet,
    NoAlternativePairing,
    NotFound,
    PairingLocked,
    RerollLimitReached,
    ResultLocked,
    ResultsIncomplete,
    UnresolvedPrizes,
)
from app.domain.numbers import assign_tournament_numbers
from app.domain.pairing import (
    PairingObjective,
    PairingOutcome,
    PairingPlayer,
    canonical_pairs,
    find_distinct_optimal,
    generate_pairing,
)
from app.domain.prizes import (
    TiebreakGame,
    assigned_prize_places,
    build_clusters,
    first_unresolved_prize_cluster,
    head_to_head_split,
    prize_slot_count,
    prizes_resolved,
    rank_mini_tournament,
    remove_contender,
    replace_cluster,
    unawarded_prize_places,
)
from app.domain.scoring import StandingRow, calculate_standings, strictly_tied
from app.domain.state_machine import assert_transition
from app.domain.validation import (
    assert_field_can_play,
    clean_tournament_name,
    validate_limits,
    validate_prizes,
)
from app.presentation.messages import (
    bye_private_text,
    final_stats_text,
    opponent_private_text,
    prize_congratulations,
    unawarded_note,
)
from app.services.notifications import enqueue
from app.services.support import (
    active_ids,
    audit,
    ensure_database,
    get_group,
    get_live_tournament,
    get_settings_row,
    list_participants,
    load_swiss_games,
    pause_from,
    recompute_stored_scores,
    require_admin,
    require_live_tournament,
    resume_from,
    transition,
)

_TECHNICAL = {Outcome.TECHNICAL_P1.value, Outcome.TECHNICAL_P2.value}


def _points(outcome: str) -> tuple[int, int, str]:
    if outcome == Outcome.P1_WIN.value:
        return 2, 0, ResultKind.REAL.value
    if outcome == Outcome.DRAW.value:
        return 1, 1, ResultKind.REAL.value
    if outcome == Outcome.P2_WIN.value:
        return 0, 2, ResultKind.REAL.value
    if outcome == Outcome.TECHNICAL_P1.value:
        return 2, 0, ResultKind.TECHNICAL.value
    if outcome == Outcome.TECHNICAL_P2.value:
        return 0, 2, ResultKind.TECHNICAL.value
    raise InvalidConfig("Noma’lum natija.")


async def _commit(session: AsyncSession) -> None:
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ActiveTournamentExists() from exc


async def create_draft(
    session: AsyncSession,
    *,
    actor_telegram_id: int,
    settings: Settings,
    name: str,
) -> Tournament:
    await ensure_database(session)
    require_admin(actor_telegram_id, settings)
    if await get_live_tournament(session) is not None:
        raise ActiveTournamentExists()
    now = datetime.now(UTC)
    tournament = Tournament(
        name=clean_tournament_name(name),
        status=TournamentStatus.DRAFT.value,
        created_by=actor_telegram_id,
        is_live=True,
        created_at=now,
        updated_at=now,
    )
    session.add(tournament)
    await session.flush()
    session.add(TournamentSettings(tournament_id=tournament.id))
    await audit(
        session,
        actor=actor_telegram_id,
        action="tournament.create",
        entity_type="tournament",
        entity_id=tournament.id,
        details={"name": tournament.name},
    )
    await _commit(session)
    await session.refresh(tournament)
    return tournament


async def copy_previous_settings(
    session: AsyncSession,
    *,
    actor_telegram_id: int,
    settings: Settings,
) -> Tournament:
    await ensure_database(session)
    require_admin(actor_telegram_id, settings)
    if await get_live_tournament(session) is not None:
        raise ActiveTournamentExists()
    previous = (
        (
            await session.execute(
                select(Tournament)
                .where(Tournament.status == TournamentStatus.COMPLETED.value)
                .order_by(Tournament.completed_at.desc())
            )
        )
        .scalars()
        .first()
    )
    if previous is None:
        raise NotFound("Nusxa olish uchun yakunlangan turnir yo‘q.")
    previous_settings = await get_settings_row(session, previous.id)
    draft = await create_draft(
        session,
        actor_telegram_id=actor_telegram_id,
        settings=settings,
        name=previous.name,
    )
    current = await get_settings_row(session, draft.id)
    current.min_participants = previous_settings.min_participants
    current.max_participants = previous_settings.max_participants
    current.swiss_rounds = previous_settings.swiss_rounds
    current.prize_first = previous_settings.prize_first
    current.prize_second = previous_settings.prize_second
    current.prize_third = previous_settings.prize_third
    if previous.group_id is not None:
        group = await session.get(ConnectedGroup, previous.group_id)
        if group is not None and group.is_active:
            draft.group_id = group.id
    await audit(
        session,
        actor=actor_telegram_id,
        action="tournament.copy_settings",
        entity_type="tournament",
        entity_id=draft.id,
        details={"source_tournament_id": previous.id},
    )
    await session.commit()
    return draft


def _editable(tournament: Tournament) -> None:
    if tournament.current_round_number != 0:
        raise InvalidTransition("Turnir boshlangan. Sozlamalar o‘zgarmaydi.")
    if TournamentStatus(tournament.status) not in {
        TournamentStatus.DRAFT,
        TournamentStatus.REGISTRATION_OPEN,
        TournamentStatus.REGISTRATION_CLOSED,
    }:
        raise InvalidTransition("Sozlamalarni hozir o‘zgartirib bo‘lmaydi.")


async def rename_tournament(
    session: AsyncSession,
    *,
    actor_telegram_id: int,
    settings: Settings,
    name: str,
) -> Tournament:
    await ensure_database(session)
    require_admin(actor_telegram_id, settings)
    tournament = await require_live_tournament(session)
    _editable(tournament)
    tournament.name = clean_tournament_name(name)
    tournament.updated_at = datetime.now(UTC)
    await session.commit()
    return tournament


async def update_limits(
    session: AsyncSession,
    *,
    actor_telegram_id: int,
    settings: Settings,
    min_participants: int,
    max_participants: int,
    swiss_rounds: int,
) -> TournamentSettings:
    await ensure_database(session)
    require_admin(actor_telegram_id, settings)
    tournament = await require_live_tournament(session)
    _editable(tournament)
    validate_limits(min_participants, max_participants, swiss_rounds)
    seated = [
        item
        for item in await list_participants(session, tournament.id)
        if item.status == ParticipantStatus.REGISTERED.value
    ]
    if len(seated) > max_participants:
        raise InvalidConfig("Maksimal son ro‘yxatdagi ishtirokchilardan kam.")
    row = await get_settings_row(session, tournament.id)
    row.min_participants = min_participants
    row.max_participants = max_participants
    row.swiss_rounds = swiss_rounds
    await audit(
        session,
        actor=actor_telegram_id,
        action="tournament.limits",
        entity_type="tournament",
        entity_id=tournament.id,
        details={
            "min": min_participants,
            "max": max_participants,
            "rounds": swiss_rounds,
        },
    )
    await session.commit()
    return row


async def update_prizes(
    session: AsyncSession,
    *,
    actor_telegram_id: int,
    settings: Settings,
    first: int,
    second: int,
    third: int,
) -> TournamentSettings:
    await ensure_database(session)
    require_admin(actor_telegram_id, settings)
    tournament = await require_live_tournament(session)
    _editable(tournament)
    validate_prizes(first, second, third)
    row = await get_settings_row(session, tournament.id)
    row.prize_first = first
    row.prize_second = second
    row.prize_third = third
    await audit(
        session,
        actor=actor_telegram_id,
        action="tournament.prizes",
        entity_type="tournament",
        entity_id=tournament.id,
        details={"first": first, "second": second, "third": third},
    )
    await session.commit()
    return row


async def choose_group(
    session: AsyncSession,
    *,
    actor_telegram_id: int,
    settings: Settings,
    group_id: int,
) -> Tournament:
    await ensure_database(session)
    require_admin(actor_telegram_id, settings)
    tournament = await require_live_tournament(session)
    _editable(tournament)
    group = await session.get(ConnectedGroup, group_id)
    if group is None or not group.is_active:
        raise NotFound("Ulangan guruh topilmadi.")
    tournament.group_id = group.id
    tournament.updated_at = datetime.now(UTC)
    await audit(
        session,
        actor=actor_telegram_id,
        action="tournament.group",
        entity_type="tournament",
        entity_id=tournament.id,
        details={"group_id": group.id},
    )
    await session.commit()
    return tournament


async def _require_ready(session: AsyncSession, tournament: Tournament) -> TournamentSettings:
    if not tournament.name:
        raise InvalidConfig("Turnir nomini kiriting.")
    group = await get_group(session, tournament.group_id)
    if group is None or not group.is_active:
        raise InvalidConfig("Avval Telegram guruhini ulang va tanlang.")
    row = await get_settings_row(session, tournament.id)
    validate_limits(row.min_participants, row.max_participants, row.swiss_rounds)
    validate_prizes(row.prize_first, row.prize_second, row.prize_third)
    return row


async def open_registration(
    session: AsyncSession,
    *,
    actor_telegram_id: int,
    settings: Settings,
) -> Tournament:
    await ensure_database(session)
    require_admin(actor_telegram_id, settings)
    tournament = await require_live_tournament(session)
    if tournament.status == TournamentStatus.REGISTRATION_CLOSED.value:
        return await reopen_registration(
            session,
            actor_telegram_id=actor_telegram_id,
            settings=settings,
        )
    if tournament.status != TournamentStatus.DRAFT.value:
        raise InvalidTransition("Ro‘yxatni hozir ochib bo‘lmaydi.")
    await _require_ready(session, tournament)
    transition(tournament, TournamentStatus.REGISTRATION_OPEN)
    await audit(
        session,
        actor=actor_telegram_id,
        action="registration.open",
        entity_type="tournament",
        entity_id=tournament.id,
        details={},
    )
    await session.commit()
    return tournament


async def close_registration(
    session: AsyncSession,
    *,
    actor_telegram_id: int,
    settings: Settings,
) -> Tournament:
    await ensure_database(session)
    require_admin(actor_telegram_id, settings)
    tournament = await require_live_tournament(session)
    if tournament.status != TournamentStatus.REGISTRATION_OPEN.value:
        raise InvalidTransition("Ro‘yxat ochiq emas.")
    transition(tournament, TournamentStatus.REGISTRATION_CLOSED)
    tournament.registration_closed_by = CloseReason.ADMIN.value
    await audit(
        session,
        actor=actor_telegram_id,
        action="registration.close",
        entity_type="tournament",
        entity_id=tournament.id,
        details={"reason": CloseReason.ADMIN.value},
    )
    await session.commit()
    return tournament


async def reopen_registration(
    session: AsyncSession,
    *,
    actor_telegram_id: int,
    settings: Settings,
) -> Tournament:
    await ensure_database(session)
    require_admin(actor_telegram_id, settings)
    tournament = await require_live_tournament(session)
    if tournament.status != TournamentStatus.REGISTRATION_CLOSED.value:
        raise InvalidTransition("Ro‘yxat yopiq emas.")
    if tournament.current_round_number != 0:
        raise InvalidTransition("Turnir boshlangan.")
    row = await get_settings_row(session, tournament.id)
    seated = sum(
        1
        for item in await list_participants(session, tournament.id)
        if item.status == ParticipantStatus.REGISTERED.value
    )
    if seated >= row.max_participants:
        raise InvalidConfig("Bo‘sh joy yo‘q. Avval kimnidir o‘chiring.")
    transition(tournament, TournamentStatus.REGISTRATION_OPEN)
    tournament.registration_closed_by = None
    await audit(
        session,
        actor=actor_telegram_id,
        action="registration.reopen",
        entity_type="tournament",
        entity_id=tournament.id,
        details={},
    )
    await session.commit()
    return tournament


async def override_minimum(
    session: AsyncSession,
    *,
    actor_telegram_id: int,
    settings: Settings,
) -> Tournament:
    await ensure_database(session)
    require_admin(actor_telegram_id, settings)
    tournament = await require_live_tournament(session)
    if tournament.status != TournamentStatus.REGISTRATION_CLOSED.value:
        raise InvalidTransition("Minimumdan past boshlash uchun avval ro‘yxatni yoping.")
    row = await get_settings_row(session, tournament.id)
    seated = [
        item
        for item in await list_participants(session, tournament.id)
        if item.status == ParticipantStatus.REGISTERED.value
    ]
    assert_field_can_play(len(seated), row.swiss_rounds)
    tournament.min_override = True
    await audit(
        session,
        actor=actor_telegram_id,
        action="tournament.override_minimum",
        entity_type="tournament",
        entity_id=tournament.id,
        details={"count": len(seated), "minimum": row.min_participants},
    )
    await session.commit()
    return tournament


async def pause_tournament(
    session: AsyncSession,
    *,
    actor_telegram_id: int,
    settings: Settings,
) -> Tournament:
    await ensure_database(session)
    require_admin(actor_telegram_id, settings)
    tournament = await require_live_tournament(session)
    pause_from(tournament)
    await audit(
        session,
        actor=actor_telegram_id,
        action="tournament.pause",
        entity_type="tournament",
        entity_id=tournament.id,
        details={"previous": tournament.previous_status},
    )
    await session.commit()
    return tournament


async def resume_tournament(
    session: AsyncSession,
    *,
    actor_telegram_id: int,
    settings: Settings,
) -> Tournament:
    await ensure_database(session)
    require_admin(actor_telegram_id, settings)
    tournament = await require_live_tournament(session)
    resume_from(tournament)
    await audit(
        session,
        actor=actor_telegram_id,
        action="tournament.resume",
        entity_type="tournament",
        entity_id=tournament.id,
        details={"status": tournament.status},
    )
    await session.commit()
    return tournament


async def cancel_tournament(
    session: AsyncSession,
    *,
    actor_telegram_id: int,
    settings: Settings,
) -> Tournament:
    await ensure_database(session)
    require_admin(actor_telegram_id, settings)
    tournament = await require_live_tournament(session)
    if TournamentStatus(tournament.status) in TERMINAL_STATUSES:
        raise AlreadyFinalized("Yakuniy turnirni bekor qilib bo‘lmaydi.")
    assert_transition(
        tournament.status, TournamentStatus.CANCELLED, previous=tournament.previous_status
    )
    tournament.status = TournamentStatus.CANCELLED.value
    tournament.is_live = None
    tournament.cancelled_at = datetime.now(UTC)
    tournament.updated_at = datetime.now(UTC)
    await audit(
        session,
        actor=actor_telegram_id,
        action="tournament.cancel",
        entity_type="tournament",
        entity_id=tournament.id,
        details={},
    )
    await session.commit()
    return tournament


async def _history(session: AsyncSession, tournament_id: int) -> set[tuple[int, int]]:
    matches = (
        (await session.execute(select(Match).where(Match.tournament_id == tournament_id)))
        .scalars()
        .all()
    )
    pairs: set[tuple[int, int]] = set()
    for match in matches:
        left, right = sorted((match.player1_id, match.player2_id))
        pairs.add((left, right))
    return pairs


async def _current_round(session: AsyncSession, tournament: Tournament) -> Round | None:
    if tournament.current_round_number <= 0:
        return None
    statement = select(Round).where(
        Round.tournament_id == tournament.id,
        Round.number == tournament.current_round_number,
    )
    return (await session.execute(statement)).scalar_one_or_none()


async def _variants(session: AsyncSession, round_id: int) -> list[PairingVariant]:
    statement = (
        select(PairingVariant)
        .where(PairingVariant.round_id == round_id)
        .order_by(PairingVariant.variant_index)
    )
    return list((await session.execute(statement)).scalars().all())


async def _save_variant(
    session: AsyncSession,
    round_id: int,
    index: int,
    outcome: PairingOutcome,
) -> PairingVariant:
    variant = PairingVariant(
        round_id=round_id,
        variant_index=index,
        seed=outcome.seed,
        objective_diff=outcome.objective.diff_sum,
        bye_score=outcome.objective.bye_score,
        bye_participant_id=outcome.bye,
        is_confirmed=False,
    )
    session.add(variant)
    await session.flush()
    for order, pair in enumerate(outcome.pairs, start=1):
        session.add(
            PairingVariantMatch(
                variant_id=variant.id,
                player1_id=pair[0],
                player2_id=pair[1],
                table_order=order,
            )
        )
    return variant


def _pairing_players(participants: list[TournamentParticipant]) -> list[PairingPlayer]:
    return [
        PairingPlayer(
            participant_id=item.id,
            score=item.score,
            bye_count=item.bye_count,
        )
        for item in participants
        if item.status == ParticipantStatus.ACTIVE.value
    ]


async def _open_preview(
    session: AsyncSession,
    tournament: Tournament,
    participants: list[TournamentParticipant],
    seed: int,
) -> Round:
    players = _pairing_players(participants)
    history = await _history(session, tournament.id)
    outcome = generate_pairing(players, history, seed)
    swiss_round = Round(
        tournament_id=tournament.id,
        number=tournament.current_round_number,
        status=RoundStatus.PREVIEW.value,
        reroll_count=0,
    )
    session.add(swiss_round)
    await session.flush()
    if outcome is None:
        return swiss_round
    await _save_variant(session, swiss_round.id, 1, outcome)
    return swiss_round


async def _regenerate_preview(session: AsyncSession, tournament: Tournament) -> None:
    swiss_round = await _current_round(session, tournament)
    if swiss_round is None or swiss_round.confirmed_variant_id is not None:
        return
    variants = await _variants(session, swiss_round.id)
    if variants:
        variant_ids = [item.id for item in variants]
        await session.execute(
            delete(PairingVariantMatch).where(PairingVariantMatch.variant_id.in_(variant_ids))
        )
        await session.execute(delete(PairingVariant).where(PairingVariant.id.in_(variant_ids)))
    participants = await list_participants(session, tournament.id)
    outcome = generate_pairing(
        _pairing_players(participants),
        await _history(session, tournament.id),
        seed=secrets.randbelow(2_000_000_000) + 1,
    )
    swiss_round.reroll_count = 0
    if outcome is not None:
        await _save_variant(session, swiss_round.id, 1, outcome)


async def start_tournament(
    session: AsyncSession,
    *,
    actor_telegram_id: int,
    settings: Settings,
    seed: int | None = None,
) -> Tournament:
    await ensure_database(session)
    require_admin(actor_telegram_id, settings)
    tournament = await require_live_tournament(session)
    if tournament.status != TournamentStatus.REGISTRATION_CLOSED.value:
        raise InvalidTransition("Boshlash uchun ro‘yxat yopiq bo‘lishi kerak.")
    row = await _require_ready(session, tournament)
    participants = [
        item
        for item in await list_participants(session, tournament.id)
        if item.status == ParticipantStatus.REGISTERED.value
    ]
    if len(participants) < row.min_participants and not tournament.min_override:
        raise MinimumNotMet(
            f"Kamida {row.min_participants} ishtirokchi kerak. Hozir {len(participants)}. "
            "Davom etish uchun minimumni alohida tasdiqlang."
        )
    assert_field_can_play(len(participants), row.swiss_rounds)
    number_seed = seed if seed is not None else secrets.randbelow(2_000_000_000) + 1
    for participant in participants:
        participant.tournament_number = None
    await session.flush()
    numbers = assign_tournament_numbers([item.id for item in participants], number_seed)
    for participant in participants:
        participant.status = ParticipantStatus.ACTIVE.value
        participant.tournament_number = numbers[participant.id]
        participant.updated_at = datetime.now(UTC)
    tournament.number_seed = number_seed
    tournament.current_round_number = 1
    tournament.started_at = datetime.now(UTC)
    transition(tournament, TournamentStatus.PAIRING_PREVIEW)
    pairing_seed = number_seed + 1
    preview = await _open_preview(session, tournament, participants, pairing_seed)
    await audit(
        session,
        actor=actor_telegram_id,
        action="tournament.start",
        entity_type="tournament",
        entity_id=tournament.id,
        details={"number_seed": number_seed, "round_id": preview.id},
    )
    await session.commit()
    variants = await _variants(session, preview.id)
    if not variants:
        raise InfeasiblePairing()
    return tournament


async def reroll_pairing(
    session: AsyncSession,
    *,
    actor_telegram_id: int,
    settings: Settings,
) -> PairingVariant:
    await ensure_database(session)
    require_admin(actor_telegram_id, settings)
    tournament = await require_live_tournament(session)
    if tournament.status != TournamentStatus.PAIRING_PREVIEW.value:
        raise InvalidTransition("Juftliklar hozir ko‘rib chiqilmayapti.")
    swiss_round = await _current_round(session, tournament)
    if swiss_round is None or swiss_round.status != RoundStatus.PREVIEW.value:
        raise InvalidTransition("Faol juftlash ko‘rigi yo‘q.")
    variants = await _variants(session, swiss_round.id)
    if any(item.is_confirmed for item in variants):
        raise PairingLocked()
    if not variants:
        raise InfeasiblePairing()
    if swiss_round.reroll_count >= 3:
        raise RerollLimitReached()
    participants = await list_participants(session, tournament.id)
    objective = PairingObjective(
        bye_score=variants[0].bye_score,
        diff_sum=variants[0].objective_diff,
    )
    existing = set()
    for variant in variants:
        pairs = await _variant_pairs(session, variant.id)
        existing.add(canonical_pairs(pairs, variant.bye_participant_id))
    found = find_distinct_optimal(
        _pairing_players(participants),
        await _history(session, tournament.id),
        base_seed=variants[-1].seed,
        existing=existing,
        objective=objective,
    )
    swiss_round.reroll_count += 1
    if found is None:
        await audit(
            session,
            actor=actor_telegram_id,
            action="pairing.reroll_empty",
            entity_type="round",
            entity_id=swiss_round.id,
            details={"reroll_count": swiss_round.reroll_count},
        )
        await session.commit()
        raise NoAlternativePairing()
    saved = await _save_variant(session, swiss_round.id, len(variants) + 1, found)
    await audit(
        session,
        actor=actor_telegram_id,
        action="pairing.reroll",
        entity_type="pairing_variant",
        entity_id=saved.id,
        details={"seed": found.seed, "objective": found.objective.as_dict()},
    )
    await session.commit()
    return saved


async def _variant_pairs(session: AsyncSession, variant_id: int) -> list[tuple[int, int]]:
    statement = (
        select(PairingVariantMatch)
        .where(PairingVariantMatch.variant_id == variant_id)
        .order_by(PairingVariantMatch.table_order)
    )
    rows = (await session.execute(statement)).scalars().all()
    return [(row.player1_id, row.player2_id) for row in rows]


async def confirm_pairing(
    session: AsyncSession,
    *,
    actor_telegram_id: int,
    settings: Settings,
    variant_id: int,
) -> Round:
    await ensure_database(session)
    require_admin(actor_telegram_id, settings)
    tournament = await require_live_tournament(session)
    if tournament.status != TournamentStatus.PAIRING_PREVIEW.value:
        raise InvalidTransition("Tasdiqlash uchun juftliklar ko‘rigi kerak.")
    variant = await session.get(PairingVariant, variant_id)
    if variant is None:
        raise NotFound("Juftlash varianti topilmadi.")
    swiss_round = await session.get(Round, variant.round_id)
    if swiss_round is None or swiss_round.tournament_id != tournament.id:
        raise NotFound("Raund topilmadi.")
    if swiss_round.number != tournament.current_round_number:
        raise PairingLocked()
    siblings = await _variants(session, swiss_round.id)
    if any(item.is_confirmed for item in siblings):
        raise PairingLocked()
    best_diff = min(item.objective_diff for item in siblings)
    best_bye = min((item.bye_score if item.bye_score is not None else -1) for item in siblings)
    bye_key = -1 if variant.bye_score is None else variant.bye_score
    if variant.objective_diff != best_diff or bye_key != best_bye:
        raise InvalidConfig("Faqat optimal variant tasdiqlanadi.")
    pairs = await _variant_pairs(session, variant.id)
    for player1_id, player2_id in pairs:
        session.add(
            Match(
                round_id=swiss_round.id,
                tournament_id=tournament.id,
                player1_id=player1_id,
                player2_id=player2_id,
                status=MatchStatus.PENDING.value,
                locked=False,
            )
        )
    if variant.bye_participant_id is not None:
        session.add(
            ByeAssignment(
                round_id=swiss_round.id,
                tournament_id=tournament.id,
                participant_id=variant.bye_participant_id,
                points_awarded=BYE_POINTS,
            )
        )
    variant.is_confirmed = True
    swiss_round.confirmed_variant_id = variant.id
    swiss_round.status = RoundStatus.ACTIVE.value
    await _lock_rounds_before(session, tournament.id, swiss_round.number)
    transition(tournament, TournamentStatus.ROUND_ACTIVE)
    await session.flush()
    await recompute_stored_scores(session, tournament.id)
    await _enqueue_pairing_notices(session, tournament, swiss_round, variant)
    await _save_snapshot(session, tournament, label="ROUND", provisional=True)
    await audit(
        session,
        actor=actor_telegram_id,
        action="pairing.confirm",
        entity_type="pairing_variant",
        entity_id=variant.id,
        details={"round": swiss_round.number, "seed": variant.seed},
    )
    await session.commit()
    return swiss_round


async def _lock_rounds_before(session: AsyncSession, tournament_id: int, round_number: int) -> None:
    rounds = (
        (
            await session.execute(
                select(Round).where(
                    Round.tournament_id == tournament_id,
                    Round.number < round_number,
                )
            )
        )
        .scalars()
        .all()
    )
    if not rounds:
        return
    round_ids = [item.id for item in rounds]
    matches = (
        (await session.execute(select(Match).where(Match.round_id.in_(round_ids)))).scalars().all()
    )
    for match in matches:
        match.locked = True
        match.status = MatchStatus.LOCKED.value
    for swiss_round in rounds:
        if swiss_round.status != RoundStatus.CLOSED.value:
            swiss_round.status = RoundStatus.CLOSED.value


async def _enqueue_pairing_notices(
    session: AsyncSession,
    tournament: Tournament,
    swiss_round: Round,
    variant: PairingVariant,
) -> None:
    participants = {item.id: item for item in await list_participants(session, tournament.id)}
    users = await _users_for(session, list(participants.values()))
    pairs = await _variant_pairs(session, variant.id)
    notified: set[int] = set()
    for player1_id, player2_id in pairs:
        for self_id, other_id in ((player1_id, player2_id), (player2_id, player1_id)):
            participant = participants[self_id]
            opponent = participants[other_id]
            user = users.get(participant.user_id)
            if user is None:
                continue
            await enqueue(
                session,
                event_key=f"pairing:{tournament.id}:{swiss_round.id}:{participant.id}",
                tournament_id=tournament.id,
                recipient_telegram_id=user.telegram_id,
                message_type=NotificationType.PAIRING.value,
                text=opponent_private_text(
                    tournament_name=tournament.name,
                    round_number=swiss_round.number,
                    opponent_number=opponent.tournament_number or 0,
                    opponent_name=opponent.display_name,
                ),
            )
            notified.add(self_id)
    if variant.bye_participant_id is not None:
        participant = participants[variant.bye_participant_id]
        user = users.get(participant.user_id)
        if user is not None:
            await enqueue(
                session,
                event_key=f"pairing:{tournament.id}:{swiss_round.id}:{participant.id}",
                tournament_id=tournament.id,
                recipient_telegram_id=user.telegram_id,
                message_type=NotificationType.PAIRING.value,
                text=bye_private_text(
                    tournament_name=tournament.name,
                    round_number=swiss_round.number,
                ),
            )


async def _users_for(
    session: AsyncSession,
    participants: list[TournamentParticipant],
) -> dict[int, User]:
    user_ids = [item.user_id for item in participants]
    if not user_ids:
        return {}
    rows = (await session.execute(select(User).where(User.id.in_(user_ids)))).scalars().all()
    return {user.id: user for user in rows}


async def _current_result(session: AsyncSession, match_id: int) -> MatchResult | None:
    statement = select(MatchResult).where(
        MatchResult.match_id == match_id,
        MatchResult.is_current.is_(True),
    )
    return (await session.execute(statement)).scalar_one_or_none()


async def _round_is_complete(session: AsyncSession, round_id: int) -> bool:
    matches = (
        (await session.execute(select(Match).where(Match.round_id == round_id))).scalars().all()
    )
    for match in matches:
        if await _current_result(session, match.id) is None:
            return False
    return True


async def confirm_result(
    session: AsyncSession,
    *,
    actor_telegram_id: int,
    settings: Settings,
    match_id: int,
    outcome: str,
) -> MatchResult:
    await ensure_database(session)
    require_admin(actor_telegram_id, settings)
    match = (
        await session.execute(select(Match).where(Match.id == match_id).with_for_update())
    ).scalar_one_or_none()
    if match is None:
        raise NotFound("O‘yin topilmadi.")
    if match.locked:
        raise ResultLocked()
    tournament = await session.get(Tournament, match.tournament_id)
    if tournament is None or tournament.is_live is not True:
        raise NotFound("Faol turnir yo‘q.")
    if tournament.status == TournamentStatus.ROUND_PAUSED.value:
        raise InvalidTransition("Turnir to‘xtatilgan.")
    if tournament.status not in {
        TournamentStatus.ROUND_ACTIVE.value,
        TournamentStatus.ROUND_REVIEW.value,
    }:
        raise ResultLocked()
    p1_points, p2_points, kind = _points(outcome)
    current = await _current_result(session, match.id)
    if current is not None and current.outcome == outcome:
        await session.commit()
        return current
    if current is not None:
        current.is_current = None
        await session.flush()
    saved = MatchResult(
        match_id=match.id,
        outcome=outcome,
        kind=kind,
        p1_points=p1_points,
        p2_points=p2_points,
        is_current=True,
        recorded_by=actor_telegram_id,
        supersedes_id=None if current is None else current.id,
    )
    session.add(saved)
    match.status = MatchStatus.CONFIRMED.value
    await session.flush()
    await recompute_stored_scores(session, tournament.id)
    swiss_round = await session.get(Round, match.round_id)
    if (
        swiss_round is not None
        and tournament.status == TournamentStatus.ROUND_ACTIVE.value
        and await _round_is_complete(session, swiss_round.id)
    ):
        transition(tournament, TournamentStatus.ROUND_REVIEW)
        swiss_round.status = RoundStatus.REVIEW.value
    provisional = tournament.status == TournamentStatus.ROUND_ACTIVE.value
    await _save_snapshot(
        session,
        tournament,
        label="PROVISIONAL" if provisional else "ROUND",
        provisional=provisional,
    )
    await audit(
        session,
        actor=actor_telegram_id,
        action="result.confirm",
        entity_type="match",
        entity_id=match.id,
        details={"outcome": outcome, "replaced": current.id if current else None},
    )
    await session.commit()
    return saved


async def withdraw_participant(
    session: AsyncSession,
    *,
    actor_telegram_id: int,
    settings: Settings,
    participant_id: int,
    reason: str = "",
) -> TournamentParticipant:
    await ensure_database(session)
    require_admin(actor_telegram_id, settings)
    tournament = await require_live_tournament(session)
    if tournament.status not in {
        TournamentStatus.ROUND_ACTIVE.value,
        TournamentStatus.ROUND_REVIEW.value,
        TournamentStatus.PAIRING_PREVIEW.value,
        TournamentStatus.TIEBREAK_PENDING.value,
        TournamentStatus.TIEBREAK_ACTIVE.value,
    }:
        raise InvalidTransition("Hozir ishtirokchini chiqarib bo‘lmaydi.")
    participant = await session.get(TournamentParticipant, participant_id)
    if participant is None or participant.tournament_id != tournament.id:
        raise NotFound("Ishtirokchi topilmadi.")
    if participant.status == ParticipantStatus.WITHDRAWN.value:
        return participant
    if participant.status != ParticipantStatus.ACTIVE.value:
        raise InvalidTransition("Faqat faol ishtirokchini chiqarish mumkin.")
    await _apply_open_technical(session, tournament, participant, actor_telegram_id)
    participant.status = ParticipantStatus.WITHDRAWN.value
    participant.prize_excluded = True
    participant.updated_at = datetime.now(UTC)
    swiss_round = await _current_round(session, tournament)
    session.add(
        Withdrawal(
            tournament_id=tournament.id,
            participant_id=participant.id,
            round_id=None if swiss_round is None else swiss_round.id,
            reason=reason[:255],
            admin_telegram_id=actor_telegram_id,
        )
    )
    await recompute_stored_scores(session, tournament.id)
    if tournament.status == TournamentStatus.PAIRING_PREVIEW.value:
        await _regenerate_preview(session, tournament)
    user = await session.get(User, participant.user_id)
    if user is not None:
        await enqueue(
            session,
            event_key=f"withdrawn:{tournament.id}:{participant.id}",
            tournament_id=tournament.id,
            recipient_telegram_id=user.telegram_id,
            message_type=NotificationType.WITHDRAWN_NOTICE.value,
            text=(
                f"{participant.display_name}, siz “{tournament.name}” turniridan chiqarildingiz. "
                "Oldingi ballaringiz saqlanadi, mukofot esa berilmaydi."
            ),
        )
    if swiss_round is not None and tournament.status == TournamentStatus.ROUND_ACTIVE.value:
        if await _round_is_complete(session, swiss_round.id):
            transition(tournament, TournamentStatus.ROUND_REVIEW)
            swiss_round.status = RoundStatus.REVIEW.value
    await audit(
        session,
        actor=actor_telegram_id,
        action="participant.withdraw",
        entity_type="tournament_participant",
        entity_id=participant.id,
        details={"reason": reason},
    )
    await session.commit()
    return participant


async def _apply_open_technical(
    session: AsyncSession,
    tournament: Tournament,
    participant: TournamentParticipant,
    actor: int,
) -> None:
    matches = (
        (
            await session.execute(
                select(Match).where(
                    Match.tournament_id == tournament.id,
                    Match.locked.is_(False),
                )
            )
        )
        .scalars()
        .all()
    )
    for match in matches:
        if participant.id not in {match.player1_id, match.player2_id}:
            continue
        if await _current_result(session, match.id) is not None:
            continue
        outcome = (
            Outcome.TECHNICAL_P2.value
            if match.player1_id == participant.id
            else Outcome.TECHNICAL_P1.value
        )
        p1_points, p2_points, kind = _points(outcome)
        session.add(
            MatchResult(
                match_id=match.id,
                outcome=outcome,
                kind=kind,
                p1_points=p1_points,
                p2_points=p2_points,
                is_current=True,
                recorded_by=actor,
            )
        )
        match.status = MatchStatus.CONFIRMED.value
    await session.flush()


async def advance_tournament(
    session: AsyncSession,
    *,
    actor_telegram_id: int,
    settings: Settings,
    seed: int | None = None,
) -> Tournament:
    await ensure_database(session)
    require_admin(actor_telegram_id, settings)
    tournament = await require_live_tournament(session)
    if tournament.status != TournamentStatus.ROUND_REVIEW.value:
        raise InvalidTransition("Keyingi bosqich uchun raund tekshiruvi kerak.")
    swiss_round = await _current_round(session, tournament)
    if swiss_round is None or not await _round_is_complete(session, swiss_round.id):
        raise ResultsIncomplete()
    row = await get_settings_row(session, tournament.id)
    participants = await list_participants(session, tournament.id)
    if tournament.current_round_number < row.swiss_rounds:
        players = _pairing_players(participants)
        history = await _history(session, tournament.id)
        probe_seed = seed if seed is not None else secrets.randbelow(2_000_000_000) + 1
        outcome = generate_pairing(players, history, probe_seed)
        if outcome is None:
            raise InfeasiblePairing()
        await _close_round(session, swiss_round)
        tournament.current_round_number += 1
        transition(tournament, TournamentStatus.PAIRING_PREVIEW)
        await recompute_stored_scores(session, tournament.id)
        participants = await list_participants(session, tournament.id)
        preview = await _open_preview(session, tournament, participants, probe_seed)
        await _save_snapshot(session, tournament, label="ROUND", provisional=False)
        await audit(
            session,
            actor=actor_telegram_id,
            action="round.advance",
            entity_type="round",
            entity_id=preview.id,
            details={"number": preview.number},
        )
        await session.commit()
        return tournament
    await _close_round(session, swiss_round)
    await recompute_stored_scores(session, tournament.id)
    await _enter_prize_phase(session, tournament, early=False)
    await audit(
        session,
        actor=actor_telegram_id,
        action="swiss.complete",
        entity_type="tournament",
        entity_id=tournament.id,
        details={"horizon": tournament.horizon_rounds},
    )
    await session.commit()
    return tournament


async def _close_round(session: AsyncSession, swiss_round: Round) -> None:
    swiss_round.status = RoundStatus.CLOSED.value
    matches = (
        (await session.execute(select(Match).where(Match.round_id == swiss_round.id)))
        .scalars()
        .all()
    )
    for match in matches:
        match.locked = True
        match.status = MatchStatus.LOCKED.value


async def approve_early_finish(
    session: AsyncSession,
    *,
    actor_telegram_id: int,
    settings: Settings,
) -> Tournament:
    await ensure_database(session)
    require_admin(actor_telegram_id, settings)
    tournament = await require_live_tournament(session)
    if tournament.status == TournamentStatus.PAIRING_PREVIEW.value:
        swiss_round = await _current_round(session, tournament)
        if swiss_round is not None:
            variants = await _variants(session, swiss_round.id)
            if variants:
                raise InvalidConfig("Juftlash mavjud. Variantni tasdiqlang.")
            await session.delete(swiss_round)
            tournament.current_round_number = max(0, tournament.current_round_number - 1)
    elif tournament.status == TournamentStatus.ROUND_REVIEW.value:
        swiss_round = await _current_round(session, tournament)
        if swiss_round is None or not await _round_is_complete(session, swiss_round.id):
            raise ResultsIncomplete()
        row = await get_settings_row(session, tournament.id)
        if tournament.current_round_number >= row.swiss_rounds:
            raise InvalidTransition("So‘nggi raund oddiy yakunlash orqali yopiladi.")
        participants = await list_participants(session, tournament.id)
        outcome = generate_pairing(
            _pairing_players(participants),
            await _history(session, tournament.id),
            seed=1,
        )
        if outcome is not None:
            raise InvalidConfig("Juftlash mumkin. Erta yakunlash shart emas.")
        await _close_round(session, swiss_round)
    else:
        raise InvalidTransition("Erta yakunlash hozir mumkin emas.")
    await recompute_stored_scores(session, tournament.id)
    await _enter_prize_phase(session, tournament, early=True)
    await audit(
        session,
        actor=actor_telegram_id,
        action="tournament.early_finish",
        entity_type="tournament",
        entity_id=tournament.id,
        details={"horizon": tournament.horizon_rounds},
    )
    await session.commit()
    return tournament


async def _closed_round_count(session: AsyncSession, tournament_id: int) -> int:
    rows = (
        (
            await session.execute(
                select(Round).where(
                    Round.tournament_id == tournament_id,
                    Round.status == RoundStatus.CLOSED.value,
                )
            )
        )
        .scalars()
        .all()
    )
    return len(list(rows))


async def _enter_prize_phase(session: AsyncSession, tournament: Tournament, *, early: bool) -> None:
    tournament.early_terminated = early or tournament.early_terminated
    tournament.horizon_rounds = await _closed_round_count(session, tournament.id)
    participants = await list_participants(session, tournament.id)
    eligible = [
        item
        for item in participants
        if item.status == ParticipantStatus.ACTIVE.value and not item.prize_excluded
    ]
    tournament.prize_clusters = build_clusters([(item.id, item.score) for item in eligible])
    await _continue_prizes(session, tournament)


async def _eligible_count(session: AsyncSession, tournament_id: int) -> int:
    participants = await list_participants(session, tournament_id)
    return sum(
        1
        for item in participants
        if item.status == ParticipantStatus.ACTIVE.value and not item.prize_excluded
    )


async def _continue_prizes(session: AsyncSession, tournament: Tournament) -> None:
    clusters = tournament.prize_clusters or []
    slots = prize_slot_count(await _eligible_count(session, tournament.id))
    index = first_unresolved_prize_cluster(clusters, slots)
    if index is None:
        if tournament.status != TournamentStatus.FINAL_REVIEW.value:
            transition(tournament, TournamentStatus.FINAL_REVIEW)
        await _save_snapshot(session, tournament, label="FINAL_REVIEW", provisional=False)
        return
    existing = (
        (
            await session.execute(
                select(TiebreakStage).where(
                    TiebreakStage.tournament_id == tournament.id,
                    TiebreakStage.status.in_(
                        [
                            TiebreakStageStatus.PENDING_APPROVAL.value,
                            TiebreakStageStatus.ACTIVE.value,
                        ]
                    ),
                )
            )
        )
        .scalars()
        .first()
    )
    if existing is None:
        stage_index = await _next_stage_index(session, tournament.id)
        cluster = list(clusters[index])
        session.add(
            TiebreakStage(
                tournament_id=tournament.id,
                stage_index=stage_index,
                format=(
                    TiebreakFormat.HEAD_TO_HEAD.value
                    if len(cluster) == 2
                    else TiebreakFormat.ROUND_ROBIN.value
                ),
                status=TiebreakStageStatus.PENDING_APPROVAL.value,
                player_ids=cluster,
                cluster_index=index,
            )
        )
    if tournament.status != TournamentStatus.TIEBREAK_PENDING.value:
        transition(tournament, TournamentStatus.TIEBREAK_PENDING)
    await _save_snapshot(session, tournament, label="TIEBREAK", provisional=False)


async def _next_stage_index(session: AsyncSession, tournament_id: int) -> int:
    rows = (
        (
            await session.execute(
                select(TiebreakStage.stage_index).where(
                    TiebreakStage.tournament_id == tournament_id
                )
            )
        )
        .scalars()
        .all()
    )
    return (max(rows) if rows else 0) + 1


async def _open_stage(session: AsyncSession, tournament: Tournament) -> TiebreakStage | None:
    statement = (
        select(TiebreakStage)
        .where(TiebreakStage.tournament_id == tournament.id)
        .order_by(TiebreakStage.stage_index.desc())
    )
    return (await session.execute(statement)).scalars().first()


async def approve_tiebreak_stage(
    session: AsyncSession,
    *,
    actor_telegram_id: int,
    settings: Settings,
) -> TiebreakStage:
    await ensure_database(session)
    require_admin(actor_telegram_id, settings)
    tournament = await require_live_tournament(session)
    if tournament.status != TournamentStatus.TIEBREAK_PENDING.value:
        raise InvalidTransition("Pley-off boshlash kutilmayapti.")
    stage = await _open_stage(session, tournament)
    if stage is None or stage.status != TiebreakStageStatus.PENDING_APPROVAL.value:
        raise NotFound("Tasdiq kutilayotgan pley-off yo‘q.")
    players = [int(item) for item in stage.player_ids]
    if len(players) == 2:
        session.add(
            TiebreakMatch(
                stage_id=stage.id,
                tournament_id=tournament.id,
                player1_id=players[0],
                player2_id=players[1],
                game_number=1,
                status=MatchStatus.PENDING.value,
            )
        )
    else:
        ordered = sorted(players)
        for left_index, left in enumerate(ordered):
            for right in ordered[left_index + 1 :]:
                session.add(
                    TiebreakMatch(
                        stage_id=stage.id,
                        tournament_id=tournament.id,
                        player1_id=left,
                        player2_id=right,
                        game_number=1,
                        status=MatchStatus.PENDING.value,
                    )
                )
    stage.status = TiebreakStageStatus.ACTIVE.value
    transition(tournament, TournamentStatus.TIEBREAK_ACTIVE)
    await _notify_tiebreak_players(session, tournament, stage)
    await audit(
        session,
        actor=actor_telegram_id,
        action="tiebreak.start",
        entity_type="tiebreak_stage",
        entity_id=stage.id,
        details={"players": players, "format": stage.format},
    )
    await session.commit()
    return stage


async def _notify_tiebreak_players(
    session: AsyncSession,
    tournament: Tournament,
    stage: TiebreakStage,
) -> None:
    participants = {item.id: item for item in await list_participants(session, tournament.id)}
    users = await _users_for(session, list(participants.values()))
    matches = (
        (await session.execute(select(TiebreakMatch).where(TiebreakMatch.stage_id == stage.id)))
        .scalars()
        .all()
    )
    opponents: dict[int, list[str]] = {int(pid): [] for pid in stage.player_ids}
    for match in matches:
        left = participants[match.player1_id]
        right = participants[match.player2_id]
        opponents[match.player1_id].append(f"{right.tournament_number}-raqam {right.display_name}")
        opponents[match.player2_id].append(f"{left.tournament_number}-raqam {left.display_name}")
    for participant_id, names in opponents.items():
        participant = participants.get(participant_id)
        if participant is None:
            continue
        user = users.get(participant.user_id)
        if user is None:
            continue
        await enqueue(
            session,
            event_key=f"tiebreak:{tournament.id}:{stage.id}:{participant.id}",
            tournament_id=tournament.id,
            recipient_telegram_id=user.telegram_id,
            message_type=NotificationType.TIEBREAK_OPPONENT.value,
            text=(
                f"“{tournament.name}” mukofot pley-offi.\n"
                f"Raqiblaringiz: {', '.join(names)}.\n"
                "Shveytsariya ballari o‘zgarmaydi."
            ),
        )


async def confirm_tiebreak_result(
    session: AsyncSession,
    *,
    actor_telegram_id: int,
    settings: Settings,
    tiebreak_match_id: int,
    outcome: str,
) -> TiebreakMatch:
    await ensure_database(session)
    require_admin(actor_telegram_id, settings)
    if outcome in _TECHNICAL:
        raise InvalidConfig("Kelmagan ishtirokchi uchun alohida tugmani bosing.")
    _points(outcome)
    tournament = await require_live_tournament(session)
    if tournament.status != TournamentStatus.TIEBREAK_ACTIVE.value:
        raise InvalidTransition("Pley-off faol emas.")
    match = await session.get(TiebreakMatch, tiebreak_match_id)
    if match is None or match.tournament_id != tournament.id:
        raise NotFound("Pley-off o‘yini topilmadi.")
    if match.status == "CANCELLED":
        raise InvalidTransition("Bu o‘yin bekor qilingan.")
    if match.confirmed and match.outcome == outcome:
        return match
    if match.confirmed:
        raise ResultLocked("Pley-off natijasi allaqachon tasdiqlangan.")
    stage = await session.get(TiebreakStage, match.stage_id)
    if stage is None or stage.status != TiebreakStageStatus.ACTIVE.value:
        raise InvalidTransition("Pley-off bosqichi faol emas.")
    match.outcome = outcome
    match.confirmed = True
    match.is_technical = False
    match.status = MatchStatus.CONFIRMED.value
    if stage.format == TiebreakFormat.HEAD_TO_HEAD.value and outcome == Outcome.DRAW.value:
        session.add(
            TiebreakMatch(
                stage_id=stage.id,
                tournament_id=tournament.id,
                player1_id=match.player1_id,
                player2_id=match.player2_id,
                game_number=match.game_number + 1,
                status=MatchStatus.PENDING.value,
            )
        )
    else:
        await _finish_stage_if_ready(session, tournament, stage)
    await audit(
        session,
        actor=actor_telegram_id,
        action="tiebreak.result",
        entity_type="tiebreak_match",
        entity_id=match.id,
        details={"outcome": outcome},
    )
    await session.commit()
    return match


async def exclude_tiebreak_contender(
    session: AsyncSession,
    *,
    actor_telegram_id: int,
    settings: Settings,
    tiebreak_match_id: int,
    absent_participant_id: int,
) -> Tournament:
    await ensure_database(session)
    require_admin(actor_telegram_id, settings)
    tournament = await require_live_tournament(session)
    if tournament.status != TournamentStatus.TIEBREAK_ACTIVE.value:
        raise InvalidTransition("Pley-off faol emas.")
    match = await session.get(TiebreakMatch, tiebreak_match_id)
    if match is None or match.tournament_id != tournament.id:
        raise NotFound("Pley-off o‘yini topilmadi.")
    if absent_participant_id not in {match.player1_id, match.player2_id}:
        raise InvalidConfig("Bu ishtirokchi shu o‘yinda yo‘q.")
    participant = await session.get(TournamentParticipant, absent_participant_id)
    if participant is None:
        raise NotFound("Ishtirokchi topilmadi.")
    outcome = (
        Outcome.TECHNICAL_P2.value
        if match.player1_id == absent_participant_id
        else Outcome.TECHNICAL_P1.value
    )
    if not match.confirmed:
        match.outcome = outcome
        match.confirmed = True
        match.is_technical = True
        match.status = MatchStatus.CONFIRMED.value
    participant.prize_excluded = True
    participant.updated_at = datetime.now(UTC)
    tournament.prize_clusters = remove_contender(tournament.prize_clusters or [], participant.id)
    stage = await session.get(TiebreakStage, match.stage_id)
    if stage is None:
        raise NotFound("Pley-off bosqichi topilmadi.")
    others = (
        (await session.execute(select(TiebreakMatch).where(TiebreakMatch.stage_id == stage.id)))
        .scalars()
        .all()
    )
    for item in others:
        involves = absent_participant_id in {item.player1_id, item.player2_id}
        if involves and not item.confirmed:
            item.status = "CANCELLED"
    await _finish_stage_if_ready(session, tournament, stage)
    await audit(
        session,
        actor=actor_telegram_id,
        action="tiebreak.exclude",
        entity_type="tournament_participant",
        entity_id=participant.id,
        details={"match_id": match.id},
    )
    await session.commit()
    return tournament


def _cluster_index(clusters: list[list[int]], player_ids: list[int]) -> int:
    wanted = set(player_ids)
    for index, cluster in enumerate(clusters):
        if set(cluster) == wanted:
            return index
    raise InvalidConfig("Pley-off guruhi reytingda topilmadi.")


def _still_in(clusters: list[list[int]], player_ids: list[int]) -> list[int]:
    present = {player_id for cluster in clusters for player_id in cluster}
    return [player_id for player_id in player_ids if player_id in present]


async def _finish_stage_if_ready(
    session: AsyncSession,
    tournament: Tournament,
    stage: TiebreakStage,
) -> None:
    clusters = [list(cluster) for cluster in (tournament.prize_clusters or [])]
    original = [int(item) for item in stage.player_ids]
    remaining = _still_in(clusters, original)
    matches = list(
        (await session.execute(select(TiebreakMatch).where(TiebreakMatch.stage_id == stage.id)))
        .scalars()
        .all()
    )
    if len(remaining) <= 1:
        stage.status = TiebreakStageStatus.COMPLETED.value
        tournament.prize_clusters = clusters
        await _continue_prizes(session, tournament)
        return
    relevant = [
        item
        for item in matches
        if item.status != "CANCELLED"
        and item.player1_id in remaining
        and item.player2_id in remaining
    ]
    if any(not item.confirmed for item in relevant):
        return
    if stage.format == TiebreakFormat.HEAD_TO_HEAD.value:
        ordered = sorted(relevant, key=lambda item: item.game_number)
        if not ordered:
            return
        last = ordered[-1]
        if last.outcome == Outcome.DRAW.value:
            return
        split = head_to_head_split(last.player1_id, last.player2_id, str(last.outcome))
        if split is None:
            return
        index = _cluster_index(clusters, remaining)
        tournament.prize_clusters = replace_cluster(clusters, index, split)
    else:
        games = [
            TiebreakGame(item.player1_id, item.player2_id, str(item.outcome))
            for item in relevant
            if item.outcome and not item.is_technical
        ]
        subgroups = rank_mini_tournament(remaining, games)
        index = _cluster_index(clusters, remaining)
        tournament.prize_clusters = replace_cluster(clusters, index, subgroups)
    stage.status = TiebreakStageStatus.COMPLETED.value
    await _continue_prizes(session, tournament)


async def approve_final(
    session: AsyncSession,
    *,
    actor_telegram_id: int,
    settings: Settings,
) -> Tournament:
    await ensure_database(session)
    require_admin(actor_telegram_id, settings)
    tournament = await get_live_tournament(session)
    if tournament is None:
        latest = (
            (await session.execute(select(Tournament).order_by(Tournament.id.desc())))
            .scalars()
            .first()
        )
        if latest is not None and latest.status == TournamentStatus.COMPLETED.value:
            raise AlreadyFinalized()
        if latest is not None and latest.status == TournamentStatus.CANCELLED.value:
            raise AlreadyFinalized("Bekor qilingan turnirni yakunlab bo‘lmaydi.")
        raise NotFound("Faol turnir yo‘q.")
    if tournament.status != TournamentStatus.FINAL_REVIEW.value:
        raise InvalidTransition("Yakuniy tasdiq uchun tekshiruv holati kerak.")
    participants = await list_participants(session, tournament.id)
    eligible = [
        item
        for item in participants
        if item.status == ParticipantStatus.ACTIVE.value and not item.prize_excluded
    ]
    clusters = tournament.prize_clusters or []
    slots = prize_slot_count(len(eligible))
    if not prizes_resolved(clusters, slots):
        raise UnresolvedPrizes("Majburiy pley-off hali tugamagan.")
    prize_places = assigned_prize_places(clusters, slots)
    prize_map = {participant_id: place for place, participant_id in prize_places}
    standings = await _official_standings(session, tournament, [item.id for item in eligible])
    for participant_id, place in prize_map.items():
        _participant(participants, participant_id).final_place = place
    _assign_remaining_places(participants, standings, prize_map, slots)
    for participant in participants:
        if participant.status == ParticipantStatus.WITHDRAWN.value:
            participant.final_place = None
    tournament.status = TournamentStatus.COMPLETED.value
    tournament.is_live = None
    tournament.completed_at = datetime.now(UTC)
    tournament.updated_at = datetime.now(UTC)
    await _enqueue_final_messages(session, tournament, participants, standings)
    await _save_snapshot(session, tournament, label="FINAL", provisional=False)
    await audit(
        session,
        actor=actor_telegram_id,
        action="tournament.complete",
        entity_type="tournament",
        entity_id=tournament.id,
        details={
            "winners": prize_map,
            "unawarded": unawarded_prize_places(len(eligible)),
            "note": unawarded_note(len(eligible)),
        },
    )
    await session.commit()
    return tournament


def _participant(
    participants: list[TournamentParticipant],
    participant_id: int,
) -> TournamentParticipant:
    for participant in participants:
        if participant.id == participant_id:
            return participant
    raise NotFound("Ishtirokchi topilmadi.")


def _assign_remaining_places(
    participants: list[TournamentParticipant],
    standings: list[StandingRow],
    prize_map: dict[int, int],
    slots: int,
) -> None:
    remaining = [row for row in standings if row.participant_id not in prize_map]
    next_place = slots + 1
    index = 0
    while index < len(remaining):
        group = [remaining[index]]
        while index + len(group) < len(remaining) and strictly_tied(
            group[0], remaining[index + len(group)]
        ):
            group.append(remaining[index + len(group)])
        for row in group:
            _participant(participants, row.participant_id).final_place = next_place
        next_place += len(group)
        index += len(group)


async def _official_standings(
    session: AsyncSession,
    tournament: Tournament,
    participant_ids: list[int],
) -> list[StandingRow]:
    games = await load_swiss_games(session, tournament.id)
    horizon = tournament.horizon_rounds or 0
    return calculate_standings(participant_ids, games, horizon=horizon)


async def _enqueue_final_messages(
    session: AsyncSession,
    tournament: Tournament,
    participants: list[TournamentParticipant],
    standings: list[StandingRow],
) -> None:
    row = await get_settings_row(session, tournament.id)
    amounts = {1: row.prize_first, 2: row.prize_second, 3: row.prize_third}
    by_id = {item.participant_id: item for item in standings}
    users = await _users_for(session, participants)
    for participant in participants:
        user = users.get(participant.user_id)
        if user is None:
            continue
        if participant.status not in {
            ParticipantStatus.ACTIVE.value,
            ParticipantStatus.WITHDRAWN.value,
        }:
            continue
        stats = by_id.get(participant.id)
        wins = 0 if stats is None else stats.real_wins
        draws = 0 if stats is None else stats.real_draws
        losses = 0 if stats is None else stats.real_losses + stats.technical_losses
        if (
            participant.final_place in amounts
            and participant.status == ParticipantStatus.ACTIVE.value
            and not participant.prize_excluded
        ):
            await enqueue(
                session,
                event_key=f"prize:{tournament.id}:{participant.id}",
                tournament_id=tournament.id,
                recipient_telegram_id=user.telegram_id,
                message_type=NotificationType.PRIZE.value,
                text=prize_congratulations(
                    player_name=participant.display_name,
                    tournament_name=tournament.name,
                    place=int(participant.final_place),
                    amount=amounts[int(participant.final_place)],
                ),
            )
            continue
        await enqueue(
            session,
            event_key=f"stats:{tournament.id}:{participant.id}",
            tournament_id=tournament.id,
            recipient_telegram_id=user.telegram_id,
            message_type=NotificationType.FINAL_STATS.value,
            text=final_stats_text(
                player_name=participant.display_name,
                tournament_name=tournament.name,
                place=participant.final_place,
                points=participant.score,
                wins=wins,
                draws=draws,
                losses=losses,
                withdrawn=participant.status == ParticipantStatus.WITHDRAWN.value
                or participant.prize_excluded,
            ),
        )


async def _save_snapshot(
    session: AsyncSession,
    tournament: Tournament,
    *,
    label: str,
    provisional: bool,
) -> None:
    participants = await list_participants(session, tournament.id)
    ranked_ids = [item.id for item in active_ids(participants)]
    if tournament.horizon_rounds is not None:
        horizon = tournament.horizon_rounds
    else:
        horizon = max(tournament.current_round_number, 0)
    rows = calculate_standings(
        ranked_ids,
        await load_swiss_games(session, tournament.id),
        horizon=horizon,
    )
    payload = [
        {
            "participant_id": row.participant_id,
            "rank": row.rank,
            "points": row.points,
            "buchholz": row.buchholz,
            "sonneborn_berger": row.sonneborn_berger,
            "real_wins": row.real_wins,
        }
        for row in rows
    ]
    swiss_round = await _current_round(session, tournament)
    session.add(
        StandingsSnapshot(
            tournament_id=tournament.id,
            round_id=None if swiss_round is None else swiss_round.id,
            label=label,
            provisional=provisional,
            payload=payload,
        )
    )


async def list_completed(session: AsyncSession) -> list[Tournament]:
    statement = (
        select(Tournament)
        .where(
            Tournament.status.in_(
                [TournamentStatus.COMPLETED.value, TournamentStatus.CANCELLED.value]
            )
        )
        .order_by(Tournament.id.desc())
    )
    return list((await session.execute(statement)).scalars().all())
