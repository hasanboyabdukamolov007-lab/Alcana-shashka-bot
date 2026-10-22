"""Registration, authorization, results, tiebreaks, recovery, and backups."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.config import Settings
from app.db.models import (
    Match,
    MatchResult,
    NotificationOutbox,
    PairingVariant,
    Round,
    Tournament,
    TournamentParticipant,
    User,
)
from app.domain.enums import (
    NotificationStatus,
    NotificationType,
    Outcome,
    ParticipantStatus,
    TournamentStatus,
)
from app.domain.exceptions import (
    ActiveTournamentExists,
    AlreadyFinalized,
    DatabaseUnavailable,
    DuplicateRegistration,
    GroupPermissionError,
    InfeasiblePairing,
    InvalidConfig,
    MessageNotFoundError,
    MinimumNotMet,
    NoAlternativePairing,
    PermissionDenied,
    RerollLimitReached,
    ResultLocked,
)
from app.services.backup import create_backup, restore_logical_backup
from app.services.flow import (
    advance_tournament,
    approve_early_finish,
    approve_final,
    approve_tiebreak_stage,
    cancel_tournament,
    confirm_pairing,
    confirm_result,
    confirm_tiebreak_result,
    copy_previous_settings,
    create_draft,
    exclude_tiebreak_contender,
    override_minimum,
    pause_tournament,
    reroll_pairing,
    resume_tournament,
    start_tournament,
    withdraw_participant,
)
from app.services.groups import BotMembership, connect_group
from app.services.notifications import (
    DeliveryError,
    DeliveryResult,
    apply_delivery,
    enqueue,
    process_outbox_once,
    retry_notification,
)
from app.services.recovery import recover_after_restart
from app.services.registration import (
    register_participant,
    remove_registration,
    update_participant_name,
)
from app.services.support import ensure_database, get_live_tournament, list_participants
from app.telegram.middlewares import AdminOnlyMiddleware
from app.telegram.publisher import GroupPublisher
from sqlalchemy import func, select
from tests.conftest import open_cup


async def _register_many(session, settings, count: int) -> list[TournamentParticipant]:
    rows = []
    for index in range(count):
        rows.append(
            await register_participant(
                session,
                telegram_id=1000 + index,
                first_name=f"Ism{index}",
                last_name=f"Fam{index}",
                username=None,
                actor_telegram_id=1000 + index,
                settings=settings,
            )
        )
    return rows


async def _close_and_start(session, settings, seed: int = 99):
    from app.services.flow import close_registration

    live = await get_live_tournament(session)
    if live is not None and live.status == TournamentStatus.REGISTRATION_OPEN.value:
        await close_registration(
            session,
            actor_telegram_id=settings.admin_telegram_id,
            settings=settings,
        )
    return await start_tournament(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
        seed=seed,
    )


class _Sender:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.fail = False

    async def send_message(self, chat_id: int, text: str, reply_markup=None) -> int:
        if self.fail:
            raise DeliveryError(DeliveryResult.BLOCKED)
        self.sent.append((chat_id, text))
        return 1


class _GroupSender:
    def __init__(self) -> None:
        self.messages: dict[tuple[int, int], str] = {}
        self.next_id = 1
        self.deleted: set[int] = set()
        self.pinned: list[tuple[int, int]] = []
        self.unpinned: list[tuple[int, int]] = []

    async def send_message(self, chat_id: int, text: str, reply_markup=None) -> int:
        message_id = self.next_id
        self.next_id += 1
        self.messages[(chat_id, message_id)] = text
        return message_id

    async def edit_message(
        self, chat_id: int, message_id: int, text: str, reply_markup=None
    ) -> None:
        if message_id in self.deleted or (chat_id, message_id) not in self.messages:
            raise MessageNotFoundError()
        self.messages[(chat_id, message_id)] = text

    async def pin_message(self, chat_id: int, message_id: int) -> None:
        self.pinned.append((chat_id, message_id))

    async def unpin_message(self, chat_id: int, message_id: int) -> None:
        self.unpinned.append((chat_id, message_id))


async def test_registration_duplicate_capacity_and_promotion(session, settings) -> None:
    await open_cup(session, settings, max_participants=2, min_participants=2, swiss_rounds=1)
    first, second = await _register_many(session, settings, 2)
    tournament = await get_live_tournament(session)
    assert tournament is not None
    assert tournament.status == TournamentStatus.REGISTRATION_CLOSED.value
    third = await register_participant(
        session,
        telegram_id=1002,
        first_name="Navbat",
        last_name="Kutuvchi",
        username=None,
        actor_telegram_id=1002,
        settings=settings,
    )
    assert third.status == ParticipantStatus.WAITLIST.value
    with pytest.raises(DuplicateRegistration):
        await register_participant(
            session,
            telegram_id=1000,
            first_name="Ism0",
            last_name="Fam0",
            username=None,
            actor_telegram_id=1000,
            settings=settings,
        )
    await remove_registration(
        session,
        participant_id=first.id,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
    )
    await session.refresh(third)
    assert third.status == ParticipantStatus.REGISTERED.value
    notices = (
        (
            await session.execute(
                select(NotificationOutbox).where(
                    NotificationOutbox.message_type == NotificationType.WAITLIST_PROMOTED.value
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(notices) == 1
    with pytest.raises(PermissionDenied):
        await remove_registration(
            session,
            participant_id=second.id,
            actor_telegram_id=999,
            settings=settings,
        )


async def test_admin_authorization_and_single_live_tournament(session, settings) -> None:
    await open_cup(session, settings)
    with pytest.raises(PermissionDenied):
        await create_draft(
            session,
            actor_telegram_id=5,
            settings=settings,
            name="Boshqa",
        )
    with pytest.raises(ActiveTournamentExists):
        await create_draft(
            session,
            actor_telegram_id=settings.admin_telegram_id,
            settings=settings,
            name="Ikkinchi",
        )
    member_group = await connect_group(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
        telegram_group_id=-55,
        title="Oddiy",
        membership=BotMembership(status="member", can_pin_messages=False),
    )
    assert member_group.can_pin is False
    with pytest.raises(GroupPermissionError):
        await connect_group(
            session,
            actor_telegram_id=settings.admin_telegram_id,
            settings=settings,
            telegram_group_id=-56,
            title="Yopiq",
            membership=BotMembership(
                status="left",
                can_pin_messages=False,
                can_send_messages=False,
            ),
        )


async def test_admin_middleware_blocks_callback() -> None:
    called = False

    async def handler(event, data):  # noqa: ANN001
        nonlocal called
        called = True

    event_answers: list[str] = []

    class Event:
        data = "a:home"

        def __init__(self) -> None:
            self.from_user = type("U", (), {"id": 5})()

        async def answer(self, text: str, show_alert: bool = False) -> None:
            event_answers.append(text)

    middleware = AdminOnlyMiddleware(100)
    await middleware(handler, Event(), {})
    assert called is False
    assert event_answers == ["Bu amal faqat administrator uchun."]


async def test_numbers_pairing_results_and_lock(session, settings) -> None:
    await open_cup(session, settings, min_participants=4, max_participants=4, swiss_rounds=2)
    people = await _register_many(session, settings, 4)
    tournament = await _close_and_start(session, settings, seed=21)
    active = [
        item
        for item in await list_participants(session, tournament.id)
        if item.status == ParticipantStatus.ACTIVE.value
    ]
    numbers = [item.tournament_number for item in active]
    assert sorted(numbers) == [1, 2, 3, 4]
    variants = list((await session.execute(select(PairingVariant))).scalars().all())
    assert len(variants) == 1
    original_id = variants[0].id
    try:
        await reroll_pairing(
            session,
            actor_telegram_id=settings.admin_telegram_id,
            settings=settings,
        )
    except NoAlternativePairing:
        pass
    swiss_round = await confirm_pairing(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
        variant_id=original_id,
    )
    assert swiss_round.status == "ACTIVE"
    matches = (
        (await session.execute(select(Match).where(Match.round_id == swiss_round.id)))
        .scalars()
        .all()
    )
    assert len(matches) == 2
    saved = await confirm_result(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
        match_id=matches[0].id,
        outcome=Outcome.P1_WIN.value,
    )
    again = await confirm_result(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
        match_id=matches[0].id,
        outcome=Outcome.P1_WIN.value,
    )
    assert again.id == saved.id
    edited = await confirm_result(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
        match_id=matches[0].id,
        outcome=Outcome.DRAW.value,
    )
    assert edited.id != saved.id
    history = (
        await session.execute(
            select(func.count())
            .select_from(MatchResult)
            .where(MatchResult.match_id == matches[0].id)
        )
    ).scalar_one()
    assert history == 2
    await confirm_result(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
        match_id=matches[1].id,
        outcome=Outcome.TECHNICAL_P1.value,
    )
    technical = (
        await session.execute(
            select(MatchResult).where(
                MatchResult.match_id == matches[1].id,
                MatchResult.is_current.is_(True),
            )
        )
    ).scalar_one()
    assert technical.kind == "technical"
    assert technical.p1_points == 2 and technical.p2_points == 0
    await withdraw_participant(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
        participant_id=matches[1].player2_id,
    )
    with pytest.raises(PermissionDenied):
        await withdraw_participant(
            session,
            actor_telegram_id=people[0].id,
            settings=settings,
            participant_id=matches[0].player1_id,
        )
    await advance_tournament(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
        seed=22,
    )
    with pytest.raises(ResultLocked):
        await confirm_result(
            session,
            actor_telegram_id=settings.admin_telegram_id,
            settings=settings,
            match_id=matches[0].id,
            outcome=Outcome.P2_WIN.value,
        )


async def test_minimum_override_and_pause_cancel(session, settings) -> None:
    await open_cup(session, settings, min_participants=4, max_participants=8, swiss_rounds=1)
    await _register_many(session, settings, 2)
    from app.services.flow import close_registration

    await close_registration(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
    )
    with pytest.raises(MinimumNotMet):
        await start_tournament(
            session,
            actor_telegram_id=settings.admin_telegram_id,
            settings=settings,
            seed=1,
        )
    await override_minimum(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
    )
    await start_tournament(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
        seed=3,
    )
    await pause_tournament(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
    )
    paused = await get_live_tournament(session)
    assert paused is not None and paused.status == TournamentStatus.ROUND_PAUSED.value
    await resume_tournament(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
    )
    await cancel_tournament(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
    )
    cancelled = (
        (await session.execute(select(Tournament).order_by(Tournament.id.desc()))).scalars().first()
    )
    assert cancelled is not None
    assert cancelled.status == TournamentStatus.CANCELLED.value
    assert cancelled.is_live is None
    assert all(item.final_place is None for item in await list_participants(session, cancelled.id))
    with pytest.raises(AlreadyFinalized):
        await approve_final(
            session,
            actor_telegram_id=settings.admin_telegram_id,
            settings=settings,
        )


async def test_early_finish_and_final_prizes(session, settings) -> None:
    await open_cup(session, settings, min_participants=4, max_participants=4, swiss_rounds=2)
    await _register_many(session, settings, 4)
    await _close_and_start(session, settings, seed=8)
    variant = (await session.execute(select(PairingVariant))).scalar_one()
    await confirm_pairing(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
        variant_id=variant.id,
    )
    matches = list((await session.execute(select(Match))).scalars().all())
    await confirm_result(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
        match_id=matches[0].id,
        outcome=Outcome.P1_WIN.value,
    )
    await confirm_result(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
        match_id=matches[1].id,
        outcome=Outcome.DRAW.value,
    )
    # The drawn pair leaves the tournament. The remaining pair already played.
    await withdraw_participant(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
        participant_id=matches[1].player1_id,
    )
    await withdraw_participant(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
        participant_id=matches[1].player2_id,
    )
    with pytest.raises(InfeasiblePairing):
        await advance_tournament(
            session,
            actor_telegram_id=settings.admin_telegram_id,
            settings=settings,
            seed=9,
        )
    await approve_early_finish(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
    )
    live = await get_live_tournament(session)
    assert live is not None
    assert live.early_terminated is True
    assert live.horizon_rounds == 1
    assert live.status == TournamentStatus.FINAL_REVIEW.value
    await approve_final(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
    )
    with pytest.raises(AlreadyFinalized):
        await approve_final(
            session,
            actor_telegram_id=settings.admin_telegram_id,
            settings=settings,
        )
    prizes = (
        (
            await session.execute(
                select(NotificationOutbox).where(
                    NotificationOutbox.message_type == NotificationType.PRIZE.value
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(prizes) == 2
    assert all("so‘m" in item.payload["text"] for item in prizes)
    withdrawn = (
        (
            await session.execute(
                select(TournamentParticipant).where(
                    TournamentParticipant.status == ParticipantStatus.WITHDRAWN.value
                )
            )
        )
        .scalars()
        .all()
    )
    assert withdrawn
    assert all(item.final_place is None for item in withdrawn)
    finished = (
        await session.execute(select(Tournament).where(Tournament.status == "COMPLETED"))
    ).scalar_one()
    copied = await copy_previous_settings(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
    )
    assert copied.id != finished.id
    assert copied.status == TournamentStatus.DRAFT.value
    assert await list_participants(session, copied.id) == []


async def test_tiebreak_rounds_and_absence(session, settings) -> None:
    await open_cup(session, settings, min_participants=4, max_participants=4, swiss_rounds=1)
    await _register_many(session, settings, 4)
    await _close_and_start(session, settings, seed=15)
    variant = (await session.execute(select(PairingVariant))).scalar_one()
    await confirm_pairing(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
        variant_id=variant.id,
    )
    matches = list((await session.execute(select(Match))).scalars().all())
    for match in matches:
        await confirm_result(
            session,
            actor_telegram_id=settings.admin_telegram_id,
            settings=settings,
            match_id=match.id,
            outcome=Outcome.DRAW.value,
        )
    await advance_tournament(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
    )
    live = await get_live_tournament(session)
    assert live is not None and live.status == TournamentStatus.TIEBREAK_PENDING.value
    before = {item.id: item.score for item in await list_participants(session, live.id)}
    stage = await approve_tiebreak_stage(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
    )
    assert stage.format == "ROUND_ROBIN"
    from app.db.models import TiebreakMatch

    tie_matches = list(
        (await session.execute(select(TiebreakMatch).where(TiebreakMatch.stage_id == stage.id)))
        .scalars()
        .all()
    )
    assert len(tie_matches) == 6
    for match in tie_matches:
        await confirm_tiebreak_result(
            session,
            actor_telegram_id=settings.admin_telegram_id,
            settings=settings,
            tiebreak_match_id=match.id,
            outcome=Outcome.DRAW.value,
        )
    live = await get_live_tournament(session)
    assert live is not None
    assert live.status == TournamentStatus.TIEBREAK_PENDING.value
    after = {item.id: item.score for item in await list_participants(session, live.id)}
    assert after == before
    second = await approve_tiebreak_stage(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
    )
    assert second.stage_index == 2


async def test_head_to_head_and_replacement(session, settings) -> None:
    await open_cup(session, settings, min_participants=4, max_participants=4, swiss_rounds=1)
    await _register_many(session, settings, 4)
    await _close_and_start(session, settings, seed=4)
    variant = (await session.execute(select(PairingVariant))).scalar_one()
    await confirm_pairing(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
        variant_id=variant.id,
    )
    matches = list((await session.execute(select(Match))).scalars().all())
    for match in matches:
        await confirm_result(
            session,
            actor_telegram_id=settings.admin_telegram_id,
            settings=settings,
            match_id=match.id,
            outcome=Outcome.P1_WIN.value,
        )
    await advance_tournament(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
    )
    stage = await approve_tiebreak_stage(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
    )
    assert stage.format == "HEAD_TO_HEAD"
    from app.db.models import TiebreakMatch, TiebreakStage

    match = (
        await session.execute(select(TiebreakMatch).where(TiebreakMatch.stage_id == stage.id))
    ).scalar_one()
    await confirm_tiebreak_result(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
        tiebreak_match_id=match.id,
        outcome=Outcome.DRAW.value,
    )
    replay = (
        await session.execute(
            select(TiebreakMatch).where(
                TiebreakMatch.stage_id == stage.id,
                TiebreakMatch.confirmed.is_(False),
            )
        )
    ).scalar_one()
    await exclude_tiebreak_contender(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
        tiebreak_match_id=replay.id,
        absent_participant_id=replay.player1_id,
    )
    live = await get_live_tournament(session)
    assert live is not None
    assert live.status == TournamentStatus.TIEBREAK_PENDING.value
    next_stage = (
        await session.execute(
            select(TiebreakStage).where(TiebreakStage.status == "PENDING_APPROVAL")
        )
    ).scalar_one()
    assert replay.player1_id not in next_stage.player_ids
    await approve_tiebreak_stage(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
    )
    final_match = (
        await session.execute(
            select(TiebreakMatch).where(
                TiebreakMatch.stage_id == next_stage.id,
                TiebreakMatch.confirmed.is_(False),
            )
        )
    ).scalar_one()
    await confirm_tiebreak_result(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
        tiebreak_match_id=final_match.id,
        outcome=Outcome.P1_WIN.value,
    )
    live = await get_live_tournament(session)
    assert live is not None and live.status == TournamentStatus.FINAL_REVIEW.value
    scores = {item.id: item.score for item in await list_participants(session, live.id)}
    await approve_final(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
    )
    finished_scores = {
        item.id: item.score
        for item in (await session.execute(select(TournamentParticipant))).scalars().all()
        if item.tournament_id == live.id
    }
    assert finished_scores == scores


async def test_notifications_backup_and_recovery(session, settings, tmp_path: Path) -> None:
    user = User(telegram_id=50, first_name="Ali", last_name="Vali")
    session.add(user)
    await session.commit()
    row = await enqueue(
        session,
        event_key="pairing:1:1:1",
        tournament_id=None,
        recipient_telegram_id=50,
        message_type=NotificationType.PAIRING.value,
        text="Raqib",
    )
    duplicate = await enqueue(
        session,
        event_key="pairing:1:1:1",
        tournament_id=None,
        recipient_telegram_id=50,
        message_type=NotificationType.PAIRING.value,
        text="Raqib",
    )
    await session.commit()
    assert duplicate.id == row.id
    sender = _Sender()
    sender.fail = True
    await process_outbox_once(session, sender)
    await session.refresh(row)
    assert row.status == NotificationStatus.FAILED.value
    row.status = NotificationStatus.SENDING.value
    await session.commit()
    report = await recover_after_restart(session)
    await session.refresh(row)
    assert row.status == NotificationStatus.UNCERTAIN.value
    assert report.uncertain_notifications == 1
    sender.fail = False
    processed = await process_outbox_once(session, sender)
    await session.refresh(row)
    assert row.status == NotificationStatus.UNCERTAIN.value
    assert processed == 1
    assert len(sender.sent) == 1
    prize = await enqueue(
        session,
        event_key="prize:1:9",
        tournament_id=None,
        recipient_telegram_id=50,
        message_type=NotificationType.PRIZE.value,
        text="Tabriklaymiz",
    )
    await session.commit()
    prize.status = NotificationStatus.FAILED.value
    await session.commit()
    await retry_notification(
        session,
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
        notification_id=prize.id,
    )
    await session.refresh(prize)
    assert prize.status == NotificationStatus.PENDING.value
    with pytest.raises(InvalidConfig):
        await retry_notification(
            session,
            actor_telegram_id=settings.admin_telegram_id,
            settings=settings,
            notification_id=row.id,
        )

    now = datetime.now(UTC)
    apply_delivery(row, DeliveryResult.RATE_LIMITED, now, retry_after=3)
    assert row.status == NotificationStatus.PENDING.value
    old = tmp_path / "backups" / "shashka-backup-old.json"
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_text("{}", encoding="utf-8")
    dated = now - timedelta(days=8)
    import os

    os.utime(old, (dated.timestamp(), dated.timestamp()))
    job = await create_backup(session, settings, now=now)
    assert job.status == "SUCCEEDED"
    assert Path(job.path).exists()
    assert not old.exists()
    payload = Path(job.path).read_text(encoding="utf-8")
    assert "users" in payload
    user.first_name = "Boshqa"
    await session.commit()
    import json

    await restore_logical_backup(session, json.loads(payload), force=True)
    restored = (await session.execute(select(User).where(User.telegram_id == 50))).scalar_one()
    assert restored.first_name == "Ali"


async def test_database_failure_does_not_claim_success(session, monkeypatch) -> None:
    async def boom(_session):  # noqa: ANN001
        raise DatabaseUnavailable()

    monkeypatch.setattr("app.services.registration.ensure_database", boom)
    with pytest.raises(DatabaseUnavailable):
        await register_participant(
            session,
            telegram_id=1,
            first_name="Ali",
            last_name="Vali",
            username=None,
            actor_telegram_id=1,
            settings=Settings(
                bot_token="1:abc",
                admin_telegram_id=100,
                database_url="sqlite+aiosqlite:///:memory:",
            ),
        )
    count = (
        await session.execute(select(func.count()).select_from(TournamentParticipant))
    ).scalar_one()
    assert count == 0

    class Broken:
        async def execute(self, *_args, **_kwargs):  # noqa: ANN002
            raise RuntimeError("database offline")

    with pytest.raises(DatabaseUnavailable):
        await ensure_database(Broken())  # type: ignore[arg-type]


async def test_message_is_recreated_when_deleted(session, settings) -> None:
    await open_cup(session, settings)
    tournament = await get_live_tournament(session)
    assert tournament is not None
    sender = _GroupSender()
    publisher = GroupPublisher(sender, "shashka_bot")
    await publisher.sync(session, tournament)
    assert sender.pinned
    stored_id = sender.pinned[-1][1]
    sender.deleted.add(stored_id)
    del sender.messages[(sender.pinned[-1][0], stored_id)]
    await publisher.sync(session, tournament)
    assert any(message_id != stored_id for _chat, message_id in sender.messages)


async def test_name_edit_rules(session, settings) -> None:
    await open_cup(session, settings, min_participants=2, max_participants=4, swiss_rounds=1)
    person = (await _register_many(session, settings, 2))[0]
    renamed = await update_participant_name(
        session,
        participant_id=person.id,
        first_name="Yangi",
        last_name="Ism",
        actor_telegram_id=1000,
        settings=settings,
    )
    assert renamed.display_name == "Yangi Ism"
    await _close_and_start(session, settings, seed=2)
    with pytest.raises(PermissionDenied):
        await update_participant_name(
            session,
            participant_id=person.id,
            first_name="Taqiqlangan",
            last_name="Ism",
            actor_telegram_id=1000,
            settings=settings,
        )
    await update_participant_name(
        session,
        participant_id=person.id,
        first_name="Admin",
        last_name="Tahrir",
        actor_telegram_id=settings.admin_telegram_id,
        settings=settings,
    )


async def test_reroll_limit(session, settings) -> None:
    await open_cup(session, settings, min_participants=2, max_participants=2, swiss_rounds=1)
    await _register_many(session, settings, 2)
    await _close_and_start(session, settings, seed=6)
    for _ in range(3):
        with pytest.raises(NoAlternativePairing):
            await reroll_pairing(
                session,
                actor_telegram_id=settings.admin_telegram_id,
                settings=settings,
            )
    with pytest.raises(RerollLimitReached):
        await reroll_pairing(
            session,
            actor_telegram_id=settings.admin_telegram_id,
            settings=settings,
        )
    variants = (await session.execute(select(PairingVariant))).scalars().all()
    assert len(list(variants)) == 1
    round_row = (await session.execute(select(Round))).scalar_one()
    assert round_row.reroll_count == 3
