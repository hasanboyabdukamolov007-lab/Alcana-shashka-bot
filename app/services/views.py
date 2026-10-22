"""Read models and build Uzbek Latin screens. No Telegram types here."""

from __future__ import annotations

import html

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ByeAssignment,
    ConnectedGroup,
    Match,
    MatchResult,
    NotificationOutbox,
    PairingVariant,
    PairingVariantMatch,
    Round,
    TiebreakMatch,
    TiebreakStage,
    Tournament,
    TournamentParticipant,
    User,
)
from app.domain.enums import (
    NotificationStatus,
    ParticipantStatus,
    TournamentStatus,
)
from app.domain.prizes import prize_slot_count
from app.domain.scoring import calculate_standings
from app.presentation.messages import (
    final_results_announcement,
    mention,
    money,
    registration_text,
    status_label,
    unawarded_note,
)
from app.services.support import (
    get_settings_row,
    list_participants,
    load_swiss_games,
)


async def _user_participant(
    session: AsyncSession,
    tournament: Tournament,
    telegram_id: int,
) -> tuple[User | None, TournamentParticipant | None]:
    user = (
        await session.execute(select(User).where(User.telegram_id == telegram_id))
    ).scalar_one_or_none()
    if user is None:
        return None, None
    participant = (
        await session.execute(
            select(TournamentParticipant).where(
                TournamentParticipant.tournament_id == tournament.id,
                TournamentParticipant.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    return user, participant


def _participant_map(participants: list[TournamentParticipant]) -> dict[int, TournamentParticipant]:
    return {item.id: item for item in participants}


async def _telegram_ids(
    session: AsyncSession, participants: list[TournamentParticipant]
) -> dict[int, int]:
    user_ids = {item.user_id for item in participants}
    if not user_ids:
        return {}
    rows = (
        await session.execute(
            select(User.id, User.telegram_id).where(User.id.in_(user_ids))
        )
    ).all()
    return {user_id: telegram_id for user_id, telegram_id in rows}


def _tagged(person: TournamentParticipant, telegram_ids: dict[int, int], *, html_mode: bool) -> str:
    number = person.tournament_number if person.tournament_number is not None else "—"
    if html_mode:
        name = mention(person.display_name, telegram_ids.get(person.user_id))
    else:
        name = person.display_name
    return f"{number}-raqam {name}"


async def registration_message(session: AsyncSession, tournament: Tournament) -> str:
    row = await get_settings_row(session, tournament.id)
    participants = await list_participants(session, tournament.id)
    names = [
        item.display_name
        for item in participants
        if item.status == ParticipantStatus.REGISTERED.value
        or item.status == ParticipantStatus.ACTIVE.value
    ]
    registered = len(names)
    waiting = sum(1 for item in participants if item.status == ParticipantStatus.WAITLIST.value)
    if tournament.status == TournamentStatus.REGISTRATION_OPEN.value:
        label = "OCHIQ"
    elif tournament.status == TournamentStatus.ROUND_PAUSED.value:
        label = "TO‘XTATILGAN"
    else:
        label = "YOPIQ"
    return registration_text(
        name=tournament.name,
        status_label=label,
        names=names,
        registered=registered,
        maximum=row.max_participants,
        prizes=(row.prize_first, row.prize_second, row.prize_third),
        waiting=waiting,
    )


async def review_text(session: AsyncSession, tournament: Tournament) -> str:
    row = await get_settings_row(session, tournament.id)
    group = None
    if tournament.group_id is not None:
        group = await session.get(ConnectedGroup, tournament.group_id)
    group_name = "tanlanmagan" if group is None else group.title
    participants = await list_participants(session, tournament.id)
    registered = sum(
        1
        for item in participants
        if item.status in {ParticipantStatus.REGISTERED.value, ParticipantStatus.ACTIVE.value}
    )
    return (
        f"Turnir: {tournament.name}\n"
        f"Holat: {status_label(tournament.status)}\n"
        f"Guruh: {group_name}\n"
        f"Ishtirokchilar: {registered} (min {row.min_participants}, max {row.max_participants})\n"
        f"Shveytsariya raundlari: {row.swiss_rounds}\n"
        f"1-o‘rin: {money(row.prize_first)}\n"
        f"2-o‘rin: {money(row.prize_second)}\n"
        f"3-o‘rin: {money(row.prize_third)}\n"
        "Ishtirok bepul.\n\n"
        "Boshlashdan oldin sozlamalarni tekshiring."
    )


async def pairing_preview_text(
    session: AsyncSession, tournament: Tournament, variant_id: int
) -> str:
    variant = await session.get(PairingVariant, variant_id)
    if variant is None:
        return "Variant topilmadi."
    participants = _participant_map(await list_participants(session, tournament.id))
    pair_rows = (
        (
            await session.execute(
                select(PairingVariantMatch)
                .where(PairingVariantMatch.variant_id == variant.id)
                .order_by(PairingVariantMatch.table_order)
            )
        )
        .scalars()
        .all()
    )
    pairs = [(item.player1_id, item.player2_id) for item in pair_rows]
    lines = [f"Variant {variant.variant_index}. Urug‘: {variant.seed}"]
    lines.append(f"Ball farqi yig‘indisi: {variant.objective_diff}")
    for index, (left_id, right_id) in enumerate(pairs, start=1):
        left = participants[left_id]
        right = participants[right_id]
        lines.append(
            f"{index}. {left.tournament_number}-raqam {left.display_name} — "
            f"{right.tournament_number}-raqam {right.display_name}"
        )
    if variant.bye_participant_id is not None:
        bye = participants[variant.bye_participant_id]
        lines.append(f"BYE: {bye.tournament_number}-raqam {bye.display_name} (2 ball)")
    lines.append("BYE haqiqiy g‘alaba emas. Tasdiqlangach juftliklar o‘zgarmaydi.")
    return "\n".join(lines)


async def round_public_text(
    session: AsyncSession, tournament: Tournament, round_number: int
) -> str:
    swiss_round = (
        await session.execute(
            select(Round).where(
                Round.tournament_id == tournament.id,
                Round.number == round_number,
            )
        )
    ).scalar_one_or_none()
    if swiss_round is None:
        return "Raund topilmadi."
    roster = await list_participants(session, tournament.id)
    participants = _participant_map(roster)
    telegram_ids = await _telegram_ids(session, roster)
    html_mode = bool(telegram_ids)
    title = html.escape(tournament.name, quote=False) if html_mode else tournament.name
    matches = (
        (await session.execute(select(Match).where(Match.round_id == swiss_round.id)))
        .scalars()
        .all()
    )
    lines = [title, f"{round_number}-raund juftliklari"]
    for index, match in enumerate(matches, start=1):
        left = participants[match.player1_id]
        right = participants[match.player2_id]
        result = (
            await session.execute(
                select(MatchResult).where(
                    MatchResult.match_id == match.id,
                    MatchResult.is_current.is_(True),
                )
            )
        ).scalar_one_or_none()
        score = _result_label(result.outcome) if result is not None else "kutilmoqda"
        lines.append(
            f"{index}. {_tagged(left, telegram_ids, html_mode=html_mode)} — "
            f"{_tagged(right, telegram_ids, html_mode=html_mode)}: {score}"
        )
    bye = (
        await session.execute(select(ByeAssignment).where(ByeAssignment.round_id == swiss_round.id))
    ).scalar_one_or_none()
    if bye is not None:
        person = participants[bye.participant_id]
        lines.append(f"BYE: {_tagged(person, telegram_ids, html_mode=html_mode)} (2 ball)")
    return "\n".join(lines)


def _result_label(outcome: str) -> str:
    labels = {
        "P1_WIN": "1-o‘yinchi g‘alaba",
        "P2_WIN": "2-o‘yinchi g‘alaba",
        "DRAW": "durang",
        "TECHNICAL_P1": "texnik g‘alaba (1)",
        "TECHNICAL_P2": "texnik g‘alaba (2)",
    }
    return labels.get(outcome, outcome)


async def standings_text(session: AsyncSession, tournament: Tournament) -> str:
    participants = await list_participants(session, tournament.id)
    active = [
        item
        for item in participants
        if item.status == ParticipantStatus.ACTIVE.value and not item.prize_excluded
    ]
    horizon = (
        tournament.horizon_rounds
        if tournament.horizon_rounds is not None
        else max(tournament.current_round_number, 0)
    )
    rows = calculate_standings(
        [item.id for item in active],
        await load_swiss_games(session, tournament.id),
        horizon=horizon,
    )
    by_id = _participant_map(participants)
    title = "Jonli reyting"
    if tournament.status == TournamentStatus.ROUND_ACTIVE.value:
        title += " (vaqtinchalik)"
    lines = [tournament.name, title]
    if not rows:
        lines.append("Hali reyting yo‘q.")
    for row in rows:
        person = by_id[row.participant_id]
        lines.append(
            f"{row.rank}. {person.tournament_number}-raqam {person.display_name} — "
            f"{row.points} ball, BH {row.buchholz}, SB {row.sonneborn_berger:g}, "
            f"g‘alaba {row.real_wins}"
        )
    withdrawn = [item for item in participants if item.status == ParticipantStatus.WITHDRAWN.value]
    if withdrawn:
        lines.append("")
        lines.append("Chiqarilgan ishtirokchilar (mukofot yo‘q):")
        for item in withdrawn:
            lines.append(f"- {item.display_name}: {item.score} ball")
    return "\n".join(lines)


async def tiebreak_public_text(session: AsyncSession, tournament: Tournament) -> str:
    stage = (
        (
            await session.execute(
                select(TiebreakStage)
                .where(TiebreakStage.tournament_id == tournament.id)
                .order_by(TiebreakStage.stage_index.desc())
            )
        )
        .scalars()
        .first()
    )
    if stage is None:
        return "\n".join(
            [
                tournament.name,
                "Mukofot pley-offi",
                "Shveytsariya ballari o‘zgarmaydi.",
                "Pley-off yo‘q.",
            ]
        )
    roster = await list_participants(session, tournament.id)
    telegram_ids = await _telegram_ids(session, roster)
    html_mode = bool(telegram_ids)
    title = html.escape(tournament.name, quote=False) if html_mode else tournament.name
    lines = [title, "Mukofot pley-offi", "Shveytsariya ballari o‘zgarmaydi."]
    participants = _participant_map(roster)
    matches = (
        (await session.execute(select(TiebreakMatch).where(TiebreakMatch.stage_id == stage.id)))
        .scalars()
        .all()
    )
    lines.append(f"{stage.stage_index}-bosqich. Holat: {stage.status}")
    for match in matches:
        left = participants[match.player1_id]
        right = participants[match.player2_id]
        label = _result_label(match.outcome) if match.outcome else "kutilmoqda"
        lines.append(
            f"{_tagged(left, telegram_ids, html_mode=html_mode)} — "
            f"{_tagged(right, telegram_ids, html_mode=html_mode)}: {label}"
        )
    return "\n".join(lines)


async def final_public_text(session: AsyncSession, tournament: Tournament) -> str:
    row = await get_settings_row(session, tournament.id)
    participants = await list_participants(session, tournament.id)
    eligible = [
        item
        for item in participants
        if item.final_place is not None and item.status == ParticipantStatus.ACTIVE.value
    ]
    eligible.sort(key=lambda item: (item.final_place or 999, item.id))
    prizes = {1: row.prize_first, 2: row.prize_second, 3: row.prize_third}
    telegram_ids = await _telegram_ids(session, participants)
    winners = [
        (item.final_place, item.display_name, telegram_ids.get(item.user_id))
        for item in eligible
        if item.final_place in prizes and item.final_place is not None
    ]
    note = unawarded_note(
        sum(
            1
            for item in participants
            if item.status == ParticipantStatus.ACTIVE.value and not item.prize_excluded
        )
    )
    lines = [
        final_results_announcement(
            tournament_name=tournament.name,
            winners=winners,
            missing_note=note,
        ),
        "",
        "To‘liq natijalar:",
    ]
    for item in eligible:
        prize = prizes.get(item.final_place or 0)
        prize_text = f", mukofot {money(prize)}" if prize is not None else ""
        lines.append(
            f"{item.final_place}-o‘rin. {item.tournament_number}-raqam {item.display_name} — "
            f"{item.score} ball{prize_text}"
        )
    withdrawn = [item for item in participants if item.status == ParticipantStatus.WITHDRAWN.value]
    if withdrawn:
        lines.append("")
        lines.append("Chiqarilgan ishtirokchilar:")
        for item in withdrawn:
            lines.append(f"- {item.display_name}: {item.score} ball. Mukofot yo‘q.")
    if tournament.status == TournamentStatus.CANCELLED.value:
        lines = [f"{tournament.name}", "Turnir bekor qilindi. Chempion e’lon qilinmaydi."]
    return "\n".join(lines)


async def profile_text(
    session: AsyncSession, tournament: Tournament | None, telegram_id: int
) -> str:
    if tournament is None:
        user = (
            await session.execute(select(User).where(User.telegram_id == telegram_id))
        ).scalar_one_or_none()
        if user is None:
            return "Profil hali saqlanmagan. Turnir ochilganda ro‘yxatdan o‘ting."
        return f"Ism: {user.first_name} {user.last_name}\nTelegram ID: {user.telegram_id}"
    user, participant = await _user_participant(session, tournament, telegram_id)
    if user is None:
        return "Profil hali saqlanmagan. Ro‘yxatdan o‘tish uchun ism va familiyangizni yuboring."
    if participant is None:
        return (
            f"Ism: {user.first_name} {user.last_name}\n"
            f"Telegram ID: {user.telegram_id}\n"
            "Shu turnirda ro‘yxatdan o‘tmagansiz."
        )
    number = participant.tournament_number or "—"
    return (
        f"Ism: {participant.display_name}\n"
        f"Telegram ID: {user.telegram_id}\n"
        f"Turnir raqami: {number}\n"
        f"Ball: {participant.score}\n"
        f"Holat: {participant.status}"
    )


async def opponent_text(
    session: AsyncSession, tournament: Tournament | None, telegram_id: int
) -> str:
    if tournament is None:
        return "Faol turnir yo‘q."
    _user, participant = await _user_participant(session, tournament, telegram_id)
    if participant is None or participant.status != ParticipantStatus.ACTIVE.value:
        return "Faol ishtirokchi emassiz."
    swiss_round = (
        await session.execute(
            select(Round).where(
                Round.tournament_id == tournament.id,
                Round.number == tournament.current_round_number,
            )
        )
    ).scalar_one_or_none()
    if swiss_round is None or swiss_round.status == "PREVIEW":
        return "Hali raqib belgilanmagan."
    bye = (
        await session.execute(
            select(ByeAssignment).where(
                ByeAssignment.round_id == swiss_round.id,
                ByeAssignment.participant_id == participant.id,
            )
        )
    ).scalar_one_or_none()
    if bye is not None:
        return (
            f"{tournament.current_round_number}-raund\n"
            "Sizga BYE berildi. Bu raundda 2 ball olasiz.\n"
            "BYE haqiqiy g‘alaba hisoblanmaydi."
        )
    matches = (
        (await session.execute(select(Match).where(Match.round_id == swiss_round.id)))
        .scalars()
        .all()
    )
    participants = _participant_map(await list_participants(session, tournament.id))
    for match in matches:
        if participant.id not in {match.player1_id, match.player2_id}:
            continue
        other_id = match.player2_id if match.player1_id == participant.id else match.player1_id
        other = participants[other_id]
        return (
            f"{swiss_round.number}-raund\n"
            f"Raqibingiz: {other.tournament_number}-raqam {other.display_name}\n"
            "Natijani administrator kiritadi."
        )
    return "Hali raqib belgilanmagan."


async def games_text(session: AsyncSession, tournament: Tournament | None, telegram_id: int) -> str:
    if tournament is None:
        return "Faol turnir yo‘q."
    _user, participant = await _user_participant(session, tournament, telegram_id)
    if participant is None:
        return "Shu turnirda o‘yinlaringiz yo‘q."
    participants = _participant_map(await list_participants(session, tournament.id))
    rounds = {
        item.id: item.number
        for item in (
            await session.execute(select(Round).where(Round.tournament_id == tournament.id))
        )
        .scalars()
        .all()
    }
    matches = (
        (await session.execute(select(Match).where(Match.tournament_id == tournament.id)))
        .scalars()
        .all()
    )
    lines = ["Mening o‘yinlarim"]
    found = False
    for match in matches:
        if participant.id not in {match.player1_id, match.player2_id}:
            continue
        found = True
        other_id = match.player2_id if match.player1_id == participant.id else match.player1_id
        other = participants[other_id]
        result = (
            await session.execute(
                select(MatchResult).where(
                    MatchResult.match_id == match.id,
                    MatchResult.is_current.is_(True),
                )
            )
        ).scalar_one_or_none()
        if result is None:
            label = "natija yo‘q"
        elif participant.id == match.player1_id:
            label = f"{result.p1_points} ball"
        else:
            label = f"{result.p2_points} ball"
        lines.append(
            f"{rounds.get(match.round_id, '?')}-raund: {other.tournament_number}-raqam "
            f"{other.display_name} — {label}"
        )
    byes = (
        (
            await session.execute(
                select(ByeAssignment).where(ByeAssignment.participant_id == participant.id)
            )
        )
        .scalars()
        .all()
    )
    for bye in byes:
        found = True
        lines.append(f"{rounds.get(bye.round_id, '?')}-raund: BYE, 2 ball")
    if not found:
        lines.append("Hali o‘yin yo‘q.")
    return "\n".join(lines)


async def previous_tournaments_text(session: AsyncSession, telegram_id: int) -> str:
    user = (
        await session.execute(select(User).where(User.telegram_id == telegram_id))
    ).scalar_one_or_none()
    if user is None:
        return "Oldingi turnirlar yo‘q."
    rows = (
        await session.execute(
            select(Tournament, TournamentParticipant)
            .join(
                TournamentParticipant,
                TournamentParticipant.tournament_id == Tournament.id,
            )
            .where(
                TournamentParticipant.user_id == user.id,
                Tournament.status.in_(
                    [TournamentStatus.COMPLETED.value, TournamentStatus.CANCELLED.value]
                ),
            )
            .order_by(Tournament.id.desc())
        )
    ).all()
    if not rows:
        return "Oldingi turnirlar yo‘q."
    lines = ["Oldingi turnirlar"]
    for tournament, participant in rows:
        place = participant.final_place or "—"
        lines.append(
            f"{tournament.name}: {status_label(tournament.status)}, "
            f"o‘rin {place}, ball {participant.score}"
        )
    return "\n".join(lines)


async def prizes_text(session: AsyncSession, tournament: Tournament | None) -> str:
    if tournament is None:
        return "Faol turnir yo‘q. Mukofotlar turnir e’lon qilinganda ko‘rinadi."
    row = await get_settings_row(session, tournament.id)
    participants = await list_participants(session, tournament.id)
    eligible = sum(
        1
        for item in participants
        if item.status == ParticipantStatus.ACTIVE.value and not item.prize_excluded
    )
    lines = [
        f"{tournament.name}",
        f"1-o‘rin: {money(row.prize_first)}",
        f"2-o‘rin: {money(row.prize_second)}",
        f"3-o‘rin: {money(row.prize_third)}",
        "Ishtirok bepul. Bot pul o‘tkazmaydi.",
    ]
    if tournament.status in {TournamentStatus.FINAL_REVIEW.value, TournamentStatus.COMPLETED.value}:
        note = unawarded_note(
            eligible if tournament.status != TournamentStatus.COMPLETED.value else eligible
        )
        slots = prize_slot_count(eligible)
        lines.append(f"Mukofotli o‘rinlar: {slots}")
        if note:
            lines.append(note)
    return "\n".join(lines)


async def failed_notifications_text(session: AsyncSession) -> str:
    rows = (
        (
            await session.execute(
                select(NotificationOutbox)
                .where(
                    NotificationOutbox.status.in_(
                        [NotificationStatus.FAILED.value, NotificationStatus.UNCERTAIN.value]
                    )
                )
                .order_by(NotificationOutbox.id)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return "Yuborilmagan bildirishnoma yo‘q."
    lines = ["Bildirishnomalar"]
    for row in rows:
        lines.append(
            f"#{row.id} {row.status} {row.message_type} → {row.recipient_telegram_id}\n"
            f"{row.last_error or ''}"
        )
    return "\n".join(lines)


async def admin_home_text(session: AsyncSession) -> str:
    tournament = (
        await session.execute(select(Tournament).where(Tournament.is_live.is_(True)))
    ).scalar_one_or_none()
    if tournament is None:
        return "Faol turnir yo‘q.\nYangi turnir yarating yoki eski sozlamalardan nusxa oling."
    return await review_text(session, tournament)


async def variant_rows(session: AsyncSession, round_id: int) -> list[PairingVariant]:
    statement = (
        select(PairingVariant)
        .where(PairingVariant.round_id == round_id)
        .order_by(PairingVariant.variant_index)
    )
    return list((await session.execute(statement)).scalars().all())
