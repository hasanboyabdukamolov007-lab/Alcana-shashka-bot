"""Routers for the administrator, participants, and connected groups."""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message
from sqlalchemy import select

from app.config import Settings
from app.db.models import (
    Match,
    NotificationOutbox,
    Round,
    TiebreakMatch,
)
from app.domain.enums import NotificationStatus, TournamentStatus
from app.domain.exceptions import AppError
from app.presentation.messages import PARTICIPANT_MENU, html_parse_mode
from app.services import flow
from app.services.groups import (
    BotMembership,
    connect_group,
    list_active_groups,
    mark_group_inactive,
)
from app.services.notifications import retry_notification
from app.services.registration import (
    register_participant,
    remove_registration,
    update_participant_name,
)
from app.services.support import get_live_tournament, list_participants
from app.services.views import (
    admin_home_text,
    failed_notifications_text,
    final_public_text,
    games_text,
    opponent_text,
    pairing_preview_text,
    previous_tournaments_text,
    prizes_text,
    profile_text,
    review_text,
    standings_text,
)
from app.telegram.keyboards import (
    admin_keyboard,
    confirm_result_keyboard,
    group_choice_keyboard,
    match_keyboard,
    name_confirm_keyboard,
    notification_keyboard,
    participant_reply_keyboard,
    prize_default_keyboard,
    result_keyboard,
    roster_keyboard,
    tiebreak_confirm_keyboard,
    tiebreak_keyboard,
    tiebreak_result_keyboard,
    variant_keyboard,
    withdraw_keyboard,
    yes_no_keyboard,
)
from app.telegram.middlewares import AdminOnlyMiddleware
from app.telegram.publisher import GroupPublisher
from app.telegram.states import AdminForm, RegistrationForm

logger = logging.getLogger(__name__)

admin_router = Router(name="admin")
participant_router = Router(name="participant")
group_router = Router(name="group")


def _publisher(bot, username: str) -> GroupPublisher:
    from app.telegram.sender import AiogramSender

    return GroupPublisher(AiogramSender(bot), username)


async def _safe_edit(message: Message | None, text: str, markup=None) -> None:
    if message is None:
        return
    try:
        await message.edit_text(text[:3900], reply_markup=markup)
    except Exception:
        await message.answer(text[:3900], reply_markup=markup)


async def _home(session, status: str | None):
    text = await admin_home_text(session)
    return text, admin_keyboard(status)


@admin_router.message(Command("admin"))
async def admin_menu(message: Message, session, settings: Settings) -> None:
    if message.chat.type != "private":
        await message.answer("Administrator paneli shaxsiy chatda ochiladi.")
        return
    tournament = await get_live_tournament(session)
    status = None if tournament is None else tournament.status
    text, markup = await _home(session, status)
    await message.answer(text, reply_markup=markup)


@admin_router.callback_query(F.data.startswith("a:"))
async def admin_callback(
    query: CallbackQuery,
    session,
    settings: Settings,
    state: FSMContext,
) -> None:
    data = query.data or ""
    actor = query.from_user.id
    try:
        await _dispatch_admin(query, session, settings, state, data, actor)
    except AppError as exc:
        await query.answer(str(exc)[:180], show_alert=True)
    except Exception:
        logger.exception("admin callback failed")
        await query.answer("Xatolik yuz berdi. Qayta urinib ko‘ring.", show_alert=True)


async def _dispatch_admin(query, session, settings, state, data: str, actor: int) -> None:
    bot = query.bot
    me = await bot.get_me()
    publisher = _publisher(bot, me.username or "")

    async def refresh() -> None:
        tournament = await get_live_tournament(session)
        status = None if tournament is None else tournament.status
        text, markup = await _home(session, status)
        await _safe_edit(query.message, text, markup)
        if tournament is not None:
            await publisher.sync(session, tournament)

    if data == "a:home":
        await state.clear()
        await refresh()
        await query.answer()
        return
    if data == "a:create":
        await state.set_state(AdminForm.tournament_name)
        await _safe_edit(query.message, "Turnir nomini yuboring.")
        await query.answer()
        return
    if data == "a:copy":
        await flow.copy_previous_settings(session, actor_telegram_id=actor, settings=settings)
        await refresh()
        await query.answer("Sozlamalar nusxalandi. Ishtirokchilar ko‘chirilmadi.")
        return
    if data == "a:groups":
        groups = await list_active_groups(session)
        text = "Guruhni tanlang." if groups else "Hali guruh ulanmagan. Guruhda /connect yuboring."
        await _safe_edit(query.message, text, group_choice_keyboard(groups))
        await query.answer()
        return
    if data.startswith("a:group:"):
        group_id = int(data.split(":")[2])
        await flow.choose_group(
            session, actor_telegram_id=actor, settings=settings, group_id=group_id
        )
        await refresh()
        await query.answer("Guruh tanlandi.")
        return
    if data == "a:name":
        await state.set_state(AdminForm.tournament_name)
        await _safe_edit(query.message, "Yangi turnir nomini yuboring.")
        await query.answer()
        return
    if data == "a:limits":
        await state.set_state(AdminForm.min_participants)
        await state.update_data(limits={})
        await _safe_edit(query.message, "Minimal ishtirokchi sonini yuboring (kamida 2).")
        await query.answer()
        return
    if data == "a:prizes":
        await _safe_edit(
            query.message,
            "Standart mukofot: 200 000 / 100 000 / 50 000 so‘m. Yoki 1-o‘rin summasini yuboring.",
            prize_default_keyboard(),
        )
        await state.set_state(AdminForm.prize_first)
        await query.answer()
        return
    if data == "a:prizes:default":
        await flow.update_prizes(
            session,
            actor_telegram_id=actor,
            settings=settings,
            first=200_000,
            second=100_000,
            third=50_000,
        )
        await state.clear()
        await refresh()
        await query.answer("Mukofotlar saqlandi.")
        return
    if data == "a:open":
        await flow.open_registration(session, actor_telegram_id=actor, settings=settings)
        await refresh()
        await query.answer("Ro‘yxat ochiq.")
        return
    if data == "a:close":
        await flow.close_registration(session, actor_telegram_id=actor, settings=settings)
        await refresh()
        await query.answer("Ro‘yxat yopildi.")
        return
    if data == "a:override":
        await flow.override_minimum(session, actor_telegram_id=actor, settings=settings)
        await refresh()
        await query.answer("Minimum alohida tasdiqlandi.")
        return
    if data == "a:start":
        try:
            await flow.start_tournament(session, actor_telegram_id=actor, settings=settings)
        except AppError:
            raise
        await refresh()
        await query.answer("Raqamlar berildi. Juftliklarni ko‘rib chiqing.")
        return
    if data == "a:review":
        tournament = await get_live_tournament(session)
        text = "Faol turnir yo‘q." if tournament is None else await review_text(session, tournament)
        status = None if tournament is None else tournament.status
        await _safe_edit(query.message, text, admin_keyboard(status))
        await query.answer()
        return
    if data == "a:roster":
        tournament = await get_live_tournament(session)
        if tournament is None:
            await query.answer("Faol turnir yo‘q.", show_alert=True)
            return
        people = await list_participants(session, tournament.id)
        lines = ["Ishtirokchilar"]
        for person in people:
            lines.append(f"{person.display_name} — {person.status}")
        await _safe_edit(query.message, "\n".join(lines), roster_keyboard(people))
        await query.answer()
        return
    if data.startswith("a:premove:"):
        participant_id = int(data.split(":")[2])
        await remove_registration(
            session,
            participant_id=participant_id,
            actor_telegram_id=actor,
            settings=settings,
        )
        await refresh()
        await query.answer("Ishtirokchi o‘chirildi.")
        return
    if data.startswith("a:pname:"):
        await state.set_state(AdminForm.edit_first)
        await state.update_data(participant_id=int(data.split(":")[2]))
        await _safe_edit(query.message, "Yangi ismni yuboring.")
        await query.answer()
        return
    if data == "a:add":
        await state.set_state(AdminForm.add_telegram_id)
        await _safe_edit(query.message, "Ishtirokchining Telegram ID raqamini yuboring.")
        await query.answer()
        return
    if data == "a:variants":
        tournament = await get_live_tournament(session)
        if tournament is None:
            await query.answer("Faol turnir yo‘q.", show_alert=True)
            return
        swiss_round = (
            await session.execute(
                select(Round).where(
                    Round.tournament_id == tournament.id,
                    Round.number == tournament.current_round_number,
                )
            )
        ).scalar_one_or_none()
        from app.services.views import variant_rows

        variants = [] if swiss_round is None else await variant_rows(session, swiss_round.id)
        if not variants:
            await _safe_edit(
                query.message,
                "Yaroqli juftlash yo‘q. Turnirni erta yakunlash mumkin.",
                yes_no_keyboard("a:early:yes"),
            )
            await query.answer()
            return
        text = await pairing_preview_text(session, tournament, variants[0].id)
        await _safe_edit(query.message, text, variant_keyboard(variants))
        await query.answer()
        return
    if data.startswith("a:vshow:"):
        tournament = await get_live_tournament(session)
        variant_id = int(data.split(":")[2])
        text = (
            "Turnir yo‘q."
            if tournament is None
            else await pairing_preview_text(session, tournament, variant_id)
        )
        await _safe_edit(query.message, text, None)
        await query.answer()
        return
    if data == "a:reroll":
        await flow.reroll_pairing(session, actor_telegram_id=actor, settings=settings)
        await refresh()
        await query.answer("Yangi variant saqlandi.")
        return
    if data.startswith("a:vok:"):
        variant_id = int(data.split(":")[2])
        await flow.confirm_pairing(
            session,
            actor_telegram_id=actor,
            settings=settings,
            variant_id=variant_id,
        )
        await refresh()
        await query.answer("Juftliklar tasdiqlandi.")
        return
    if data == "a:early":
        await _safe_edit(
            query.message,
            "Takroriy raqibsiz juftlash imkonsiz bo‘lsa, turnir erta yakunlanadi. Tasdiqlaysizmi?",
            yes_no_keyboard("a:early:yes"),
        )
        await query.answer()
        return
    if data == "a:early:yes":
        await flow.approve_early_finish(session, actor_telegram_id=actor, settings=settings)
        await refresh()
        await query.answer("Turnir erta yakunlandi. Mukofot tartibi ochildi.")
        return
    if data == "a:matches":
        await _show_matches(query, session)
        return
    if data.startswith("a:match:"):
        match_id = int(data.split(":")[2])
        await _safe_edit(
            query.message,
            "Natijani tanlang. Saqlashdan oldin alohida tasdiq so‘raladi.",
            result_keyboard(match_id),
        )
        await query.answer()
        return
    if data.startswith("a:pick:"):
        _, _, match_id, outcome = data.split(":")
        await _safe_edit(
            query.message,
            f"Natija: {outcome}. Tasdiqlaysizmi?",
            confirm_result_keyboard(int(match_id), outcome),
        )
        await query.answer()
        return
    if data.startswith("a:rok:"):
        _, _, match_id, outcome = data.split(":")
        await flow.confirm_result(
            session,
            actor_telegram_id=actor,
            settings=settings,
            match_id=int(match_id),
            outcome=outcome,
        )
        if outcome.startswith("TECHNICAL"):
            match = await session.get(Match, int(match_id))
            absent = match.player2_id if outcome.endswith("P1") else match.player1_id
            await _safe_edit(
                query.message,
                "Texnik natija saqlandi. Kelmagan ishtirokchi turnirda qolsinmi?",
                withdraw_keyboard(absent),
            )
            await query.answer("Natija saqlandi.")
            tournament = await get_live_tournament(session)
            if tournament is not None:
                await publisher.sync(session, tournament)
            return
        await refresh()
        await query.answer("Natija saqlandi.")
        return
    if data.startswith("a:out:"):
        participant_id = int(data.split(":")[2])
        await flow.withdraw_participant(
            session,
            actor_telegram_id=actor,
            settings=settings,
            participant_id=participant_id,
            reason="Administrator chiqardi",
        )
        await refresh()
        await query.answer("Ishtirokchi chiqarildi.")
        return
    if data == "a:advance":
        await flow.advance_tournament(session, actor_telegram_id=actor, settings=settings)
        await refresh()
        await query.answer("Keyingi bosqich ochildi.")
        return
    if data == "a:pause":
        await flow.pause_tournament(session, actor_telegram_id=actor, settings=settings)
        await refresh()
        await query.answer("Turnir to‘xtatildi.")
        return
    if data == "a:resume":
        await flow.resume_tournament(session, actor_telegram_id=actor, settings=settings)
        await refresh()
        await query.answer("Turnir davom etmoqda.")
        return
    if data == "a:cancel":
        await _safe_edit(
            query.message,
            "Turnir bekor qilinsa chempion e’lon qilinmaydi. Tasdiqlaysizmi?",
            yes_no_keyboard("a:cancel:yes"),
        )
        await query.answer()
        return
    if data == "a:cancel:yes":
        tournament = await get_live_tournament(session)
        await flow.cancel_tournament(session, actor_telegram_id=actor, settings=settings)
        if tournament is not None:
            await session.refresh(tournament)
            await publisher.sync(session, tournament)
        await _safe_edit(query.message, "Turnir bekor qilindi.", admin_keyboard(None))
        await query.answer()
        return
    if data == "a:tbstart":
        await flow.approve_tiebreak_stage(session, actor_telegram_id=actor, settings=settings)
        await refresh()
        await query.answer("Pley-off boshlandi.")
        return
    if data == "a:tbmatches":
        await _show_tiebreak(query, session)
        return
    if data.startswith("a:tbabs:"):
        _, _, match_id, side = data.split(":")
        match = await session.get(TiebreakMatch, int(match_id))
        absent = match.player1_id if side == "1" else match.player2_id
        await flow.exclude_tiebreak_contender(
            session,
            actor_telegram_id=actor,
            settings=settings,
            tiebreak_match_id=int(match_id),
            absent_participant_id=absent,
        )
        await refresh()
        await query.answer("Ishtirokchi mukofotdan chiqarildi.")
        return
    if data.startswith("a:tbpick:"):
        _, _, match_id, outcome = data.split(":")
        await _safe_edit(
            query.message,
            f"Pley-off natijasi: {outcome}. Tasdiqlaysizmi?",
            tiebreak_confirm_keyboard(int(match_id), outcome),
        )
        await query.answer()
        return
    if data.startswith("a:tbok:"):
        _, _, match_id, outcome = data.split(":")
        await flow.confirm_tiebreak_result(
            session,
            actor_telegram_id=actor,
            settings=settings,
            tiebreak_match_id=int(match_id),
            outcome=outcome,
        )
        await refresh()
        await query.answer("Pley-off natijasi saqlandi.")
        return
    if data.startswith("a:tb:"):
        match_id = int(data.split(":")[2])
        await _safe_edit(
            query.message, "Pley-off natijasini tanlang.", tiebreak_result_keyboard(match_id)
        )
        await query.answer()
        return
    if data == "a:final":
        tournament = await get_live_tournament(session)
        text = (
            "Turnir yo‘q." if tournament is None else await final_public_text(session, tournament)
        )
        await _safe_edit(
            query.message, text, admin_keyboard(None if tournament is None else tournament.status)
        )
        await query.answer()
        return
    if data == "a:finalize":
        await _safe_edit(
            query.message,
            "Yakuniy natijani tasdiqlaysizmi? Bu amalni qaytarib bo‘lmaydi.",
            yes_no_keyboard("a:finalize:yes"),
        )
        await query.answer()
        return
    if data == "a:finalize:yes":
        tournament = await flow.approve_final(session, actor_telegram_id=actor, settings=settings)
        await publisher.sync(session, tournament)
        await _safe_edit(
            query.message, "Turnir yakunlandi. Tabriklar navbatga qo‘yildi.", admin_keyboard(None)
        )
        await query.answer()
        return
    if data == "a:history":
        rows = await flow.list_completed(session)
        if not rows:
            text = "Yakunlangan turnir yo‘q."
        else:
            text = "\n".join(f"{item.name} — {item.status}" for item in rows)
        await _safe_edit(query.message, text, admin_keyboard(None))
        await query.answer()
        return
    if data == "a:notif":
        text = await failed_notifications_text(session)
        failed = (
            (
                await session.execute(
                    select(NotificationOutbox)
                    .where(
                        NotificationOutbox.status.in_(
                            [NotificationStatus.FAILED.value, NotificationStatus.UNCERTAIN.value]
                        )
                    )
                    .order_by(NotificationOutbox.id.desc())
                )
            )
            .scalars()
            .first()
        )
        markup = admin_keyboard(None)
        if failed is not None and failed.message_type == "PRIZE":
            markup = notification_keyboard(
                failed.id,
                uncertain=failed.status == NotificationStatus.UNCERTAIN.value,
            )
        await _safe_edit(query.message, text, markup)
        await query.answer()
        return
    if data.startswith("a:nretry:") or data.startswith("a:nforce:"):
        notification_id = int(data.split(":")[2])
        explicit = data.startswith("a:nforce:")
        await retry_notification(
            session,
            actor_telegram_id=actor,
            settings=settings,
            notification_id=notification_id,
            explicit_uncertain=explicit,
        )
        await query.answer("Qayta yuborish navbatga qo‘yildi.")
        return
    await query.answer("Noma’lum buyruq.", show_alert=True)


async def _show_matches(query, session) -> None:
    tournament = await get_live_tournament(session)
    if tournament is None:
        await query.answer("Faol turnir yo‘q.", show_alert=True)
        return
    swiss_round = (
        await session.execute(
            select(Round).where(
                Round.tournament_id == tournament.id,
                Round.number == tournament.current_round_number,
            )
        )
    ).scalar_one_or_none()
    matches = []
    if swiss_round is not None:
        matches = list(
            (await session.execute(select(Match).where(Match.round_id == swiss_round.id)))
            .scalars()
            .all()
        )
    people = {
        item.id: item.display_name for item in await list_participants(session, tournament.id)
    }
    await _safe_edit(query.message, "O‘yinni tanlang.", match_keyboard(matches, people))
    await query.answer()


async def _show_tiebreak(query, session) -> None:
    from app.db.models import TiebreakStage

    tournament = await get_live_tournament(session)
    if tournament is None:
        await query.answer("Faol turnir yo‘q.", show_alert=True)
        return
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
    matches = []
    if stage is not None:
        matches = list(
            (await session.execute(select(TiebreakMatch).where(TiebreakMatch.stage_id == stage.id)))
            .scalars()
            .all()
        )
    people = {
        item.id: item.display_name for item in await list_participants(session, tournament.id)
    }
    await _safe_edit(
        query.message, "Pley-off o‘yinini tanlang.", tiebreak_keyboard(matches, people)
    )
    await query.answer()


@admin_router.message(AdminForm.tournament_name)
async def set_name(message: Message, session, settings: Settings, state: FSMContext) -> None:
    tournament = await get_live_tournament(session)
    if tournament is None:
        await flow.create_draft(
            session,
            actor_telegram_id=message.from_user.id,
            settings=settings,
            name=message.text or "",
        )
    else:
        await flow.rename_tournament(
            session,
            actor_telegram_id=message.from_user.id,
            settings=settings,
            name=message.text or "",
        )
    await state.clear()
    text, markup = await _home(session, (await get_live_tournament(session)).status)
    await message.answer(text, reply_markup=markup)


@admin_router.message(AdminForm.min_participants)
async def set_min(message: Message, state: FSMContext) -> None:
    await state.update_data(min_participants=int((message.text or "").strip()))
    await state.set_state(AdminForm.max_participants)
    await message.answer("Maksimal ishtirokchi sonini yuboring.")


@admin_router.message(AdminForm.max_participants)
async def set_max(message: Message, state: FSMContext) -> None:
    await state.update_data(max_participants=int((message.text or "").strip()))
    await state.set_state(AdminForm.rounds)
    await message.answer("Shveytsariya raundlari sonini yuboring.")


@admin_router.message(AdminForm.rounds)
async def set_rounds(message: Message, session, settings: Settings, state: FSMContext) -> None:
    data = await state.get_data()
    await flow.update_limits(
        session,
        actor_telegram_id=message.from_user.id,
        settings=settings,
        min_participants=int(data["min_participants"]),
        max_participants=int(data["max_participants"]),
        swiss_rounds=int((message.text or "").strip()),
    )
    await state.clear()
    text, markup = await _home(session, (await get_live_tournament(session)).status)
    await message.answer("Limitlar saqlandi.\n\n" + text, reply_markup=markup)


@admin_router.message(AdminForm.prize_first)
async def set_prize1(message: Message, state: FSMContext) -> None:
    await state.update_data(prize_first=int((message.text or "").strip()))
    await state.set_state(AdminForm.prize_second)
    await message.answer("2-o‘rin mukofotini so‘mda yuboring.")


@admin_router.message(AdminForm.prize_second)
async def set_prize2(message: Message, state: FSMContext) -> None:
    await state.update_data(prize_second=int((message.text or "").strip()))
    await state.set_state(AdminForm.prize_third)
    await message.answer("3-o‘rin mukofotini so‘mda yuboring.")


@admin_router.message(AdminForm.prize_third)
async def set_prize3(message: Message, session, settings: Settings, state: FSMContext) -> None:
    data = await state.get_data()
    await flow.update_prizes(
        session,
        actor_telegram_id=message.from_user.id,
        settings=settings,
        first=int(data["prize_first"]),
        second=int(data["prize_second"]),
        third=int((message.text or "").strip()),
    )
    await state.clear()
    text, markup = await _home(session, (await get_live_tournament(session)).status)
    await message.answer("Mukofotlar saqlandi.\n\n" + text, reply_markup=markup)


@admin_router.message(AdminForm.add_telegram_id)
async def add_tid(message: Message, state: FSMContext) -> None:
    await state.update_data(telegram_id=int((message.text or "").strip()))
    await state.set_state(AdminForm.add_first)
    await message.answer("Ismni yuboring.")


@admin_router.message(AdminForm.add_first)
async def add_first(message: Message, state: FSMContext) -> None:
    await state.update_data(first_name=message.text or "")
    await state.set_state(AdminForm.add_last)
    await message.answer("Familiyani yuboring.")


@admin_router.message(AdminForm.add_last)
async def add_last(message: Message, session, settings: Settings, state: FSMContext) -> None:
    data = await state.get_data()
    await register_participant(
        session,
        telegram_id=int(data["telegram_id"]),
        first_name=data["first_name"],
        last_name=message.text or "",
        username=None,
        actor_telegram_id=message.from_user.id,
        settings=settings,
        by_admin=True,
    )
    await state.clear()
    tournament = await get_live_tournament(session)
    publisher = _publisher(message.bot, (await message.bot.get_me()).username or "")
    if tournament is not None:
        await publisher.sync(session, tournament)
    text, markup = await _home(session, None if tournament is None else tournament.status)
    await message.answer("Ishtirokchi qo‘shildi.\n\n" + text, reply_markup=markup)


@admin_router.message(AdminForm.edit_first)
async def edit_first(message: Message, state: FSMContext) -> None:
    await state.update_data(first_name=message.text or "")
    await state.set_state(AdminForm.edit_last)
    await message.answer("Familiyani yuboring.")


@admin_router.message(AdminForm.edit_last)
async def edit_last(message: Message, session, settings: Settings, state: FSMContext) -> None:
    data = await state.get_data()
    await update_participant_name(
        session,
        participant_id=int(data["participant_id"]),
        first_name=data["first_name"],
        last_name=message.text or "",
        actor_telegram_id=message.from_user.id,
        settings=settings,
    )
    await state.clear()
    tournament = await get_live_tournament(session)
    text, markup = await _home(session, None if tournament is None else tournament.status)
    await message.answer("Ism yangilandi.\n\n" + text, reply_markup=markup)


@participant_router.message(CommandStart())
async def start_private(
    message: Message, session, command, state: FSMContext, settings: Settings
) -> None:
    if message.chat.type != "private":
        await message.answer("Ro‘yxatdan o‘tish uchun botga shaxsiy chatda yozing.")
        return
    payload = (command.args or "").strip()
    if payload.startswith("reg_"):
        await state.update_data(tournament_hint=payload.removeprefix("reg_"))
        from app.services.registration import get_user_by_telegram

        user = await get_user_by_telegram(session, message.from_user.id)
        if user is not None:
            await message.answer(
                "Saqlangan ism: "
                f"{user.first_name} {user.last_name}\n"
                "Tasdiqlaysizmi yoki o‘zgartirasizmi?",
                reply_markup=name_confirm_keyboard(),
            )
            return
        await state.set_state(RegistrationForm.first_name)
        await message.answer("Ismingizni yuboring.")
        return
    hint = (
        "\nAdministrator uchun: /admin"
        if message.from_user.id == settings.admin_telegram_id
        else ""
    )
    await message.answer(
        "Shashka turniri botiga xush kelibsiz." + hint,
        reply_markup=participant_reply_keyboard(),
    )


@participant_router.callback_query(F.data == "u:regok")
async def confirm_saved_name(query: CallbackQuery, session, settings: Settings) -> None:
    from app.services.registration import get_user_by_telegram

    user = await get_user_by_telegram(session, query.from_user.id)
    if user is None:
        await query.answer("Avval ism kiriting.", show_alert=True)
        return
    try:
        await register_participant(
            session,
            telegram_id=user.telegram_id,
            first_name=user.first_name,
            last_name=user.last_name,
            username=query.from_user.username,
            actor_telegram_id=query.from_user.id,
            settings=settings,
        )
    except AppError as exc:
        await query.answer(str(exc)[:180], show_alert=True)
        return
    tournament = await get_live_tournament(session)
    if tournament is not None:
        publisher = _publisher(query.bot, (await query.bot.get_me()).username or "")
        await publisher.sync(session, tournament)
    await query.message.answer("Ro‘yxatdan o‘tdingiz.", reply_markup=participant_reply_keyboard())
    await query.answer()


@participant_router.callback_query(F.data == "u:regedit")
async def edit_saved_name(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(RegistrationForm.first_name)
    await query.message.answer("Yangi ismingizni yuboring.")
    await query.answer()


@participant_router.message(RegistrationForm.first_name)
async def reg_first(message: Message, state: FSMContext) -> None:
    await state.update_data(first_name=message.text or "")
    await state.set_state(RegistrationForm.last_name)
    await message.answer("Familiyangizni yuboring.")


@participant_router.message(RegistrationForm.last_name)
async def reg_last(message: Message, session, settings: Settings, state: FSMContext) -> None:
    data = await state.get_data()
    try:
        await register_participant(
            session,
            telegram_id=message.from_user.id,
            first_name=data.get("first_name", ""),
            last_name=message.text or "",
            username=message.from_user.username,
            actor_telegram_id=message.from_user.id,
            settings=settings,
        )
    except AppError as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    tournament = await get_live_tournament(session)
    if tournament is not None:
        publisher = _publisher(message.bot, (await message.bot.get_me()).username or "")
        await publisher.sync(session, tournament)
    await message.answer("Ro‘yxatdan o‘tdingiz.", reply_markup=participant_reply_keyboard())


async def _screen(message: Message, session) -> str:
    tournament = await get_live_tournament(session)
    text = message.text or ""
    telegram_id = message.from_user.id
    if text == PARTICIPANT_MENU[0]:
        return await profile_text(session, tournament, telegram_id)
    if text == PARTICIPANT_MENU[1]:
        return await opponent_text(session, tournament, telegram_id)
    if text == PARTICIPANT_MENU[2]:
        return await games_text(session, tournament, telegram_id)
    if text == PARTICIPANT_MENU[3]:
        if tournament is None:
            return "Faol turnir yo‘q."
        body = await standings_text(session, tournament)
        if tournament.status == TournamentStatus.ROUND_ACTIVE.value and "vaqtinchalik" not in body:
            body = "Vaqtinchalik reyting\n" + body
        return body
    if text == PARTICIPANT_MENU[4]:
        if tournament is None:
            return "Faol turnir yo‘q."
        return await review_text(session, tournament)
    if text == PARTICIPANT_MENU[5]:
        return await previous_tournaments_text(session, telegram_id)
    if text == PARTICIPANT_MENU[6]:
        return await prizes_text(session, tournament)
    return ""


@participant_router.message(F.chat.type == "private", F.text.in_(PARTICIPANT_MENU))
async def participant_menu(message: Message, session) -> None:
    text = await _screen(message, session)
    shown = text or "Buyruq tushunilmadi."
    await message.answer(
        shown,
        reply_markup=participant_reply_keyboard(),
        parse_mode=html_parse_mode(shown),
    )


@participant_router.message(Command("menu"))
async def menu_command(message: Message) -> None:
    if message.chat.type != "private":
        return
    await message.answer("Menyu:", reply_markup=participant_reply_keyboard())


@group_router.message(Command("connect"))
async def connect(message: Message, session, settings: Settings, bot) -> None:
    if message.chat.type not in {"group", "supergroup"}:
        await message.answer("Bu buyruq guruh ichida yuboriladi.")
        return
    if message.from_user is None or message.from_user.id != settings.admin_telegram_id:
        await message.answer("Bu amal faqat administrator uchun.")
        return
    me = await bot.get_me()
    member = await bot.get_chat_member(message.chat.id, me.id)
    membership = BotMembership(
        status=member.status,
        can_pin_messages=bool(getattr(member, "can_pin_messages", False)),
        can_send_messages=bool(getattr(member, "can_send_messages", True)),
    )
    try:
        await connect_group(
            session,
            actor_telegram_id=message.from_user.id,
            settings=settings,
            telegram_group_id=message.chat.id,
            title=message.chat.title or "Guruh",
            membership=membership,
        )
    except AppError as exc:
        await message.answer(str(exc))
        return
    if membership.can_pin_messages or membership.status == "creator":
        await message.answer(f"Guruh ulandi: {message.chat.title}")
    else:
        await message.answer(
            f"Guruh ulandi: {message.chat.title}\n"
            "Bot administrator emas. Xabarlar yuboriladi, lekin mahkamlanmaydi."
        )


@group_router.my_chat_member()
async def bot_membership(event: ChatMemberUpdated, session) -> None:
    new_status = event.new_chat_member.status
    if new_status not in {"left", "kicked"}:
        return
    actor = event.from_user.id if event.from_user else 0
    await mark_group_inactive(session, telegram_group_id=event.chat.id, actor_telegram_id=actor)
    from app.domain.enums import NotificationType
    from app.services.notifications import enqueue

    tournament = await get_live_tournament(session)
    if tournament is None:
        return
    await enqueue(
        session,
        event_key=f"admin-alert:removed:{event.chat.id}:{tournament.id}",
        tournament_id=tournament.id,
        recipient_telegram_id=tournament.created_by,
        message_type=NotificationType.ADMIN_ALERT.value,
        text="Bot guruhdan chiqarildi. Turnir shaxsiy chat orqali davom etadi.",
    )
    await session.commit()


def setup_routers(settings: Settings) -> list[Router]:
    gate = AdminOnlyMiddleware(settings.admin_telegram_id)
    admin_router.message.middleware(gate)
    admin_router.callback_query.middleware(gate)
    return [group_router, admin_router, participant_router]
