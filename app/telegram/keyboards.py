"""Inline and reply keyboards."""

from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from app.db.models import (
    ConnectedGroup,
    Match,
    PairingVariant,
    TiebreakMatch,
    TournamentParticipant,
)
from app.domain.enums import TournamentStatus
from app.presentation.messages import PARTICIPANT_MENU


def participant_reply_keyboard() -> ReplyKeyboardMarkup:
    labels = list(PARTICIPANT_MENU)
    rows = [labels[index : index + 2] for index in range(0, len(labels), 2)]
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=label) for label in row] for row in rows],
        resize_keyboard=True,
    )


def registration_keyboard(bot_username: str, tournament_id: int) -> InlineKeyboardMarkup:
    url = f"https://t.me/{bot_username}?start=reg_{tournament_id}"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Ro‘yxatdan o‘tish", url=url)]]
    )


def _button(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def admin_keyboard(status: str | None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if status is None:
        rows.extend(
            [
                [_button("Turnir yaratish", "a:create")],
                [_button("Oldingi sozlamalarni nusxalash", "a:copy")],
                [_button("Guruhlar", "a:groups")],
                [_button("Oldingi turnirlar", "a:history")],
                [_button("Bildirishnomalar", "a:notif")],
            ]
        )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    rows.append([_button("Ko‘rib chiqish", "a:review")])
    if status == TournamentStatus.DRAFT.value:
        rows.extend(
            [
                [_button("Nom", "a:name"), _button("Guruh", "a:groups")],
                [_button("Limitlar", "a:limits"), _button("Mukofotlar", "a:prizes")],
                [_button("Ro‘yxatni ochish", "a:open")],
            ]
        )
    elif status == TournamentStatus.REGISTRATION_OPEN.value:
        rows.extend(
            [
                [_button("Ro‘yxatni yopish", "a:close")],
                [_button("Ishtirokchilar", "a:roster")],
                [_button("Ishtirokchi qo‘shish", "a:add")],
            ]
        )
    elif status == TournamentStatus.REGISTRATION_CLOSED.value:
        rows.extend(
            [
                [_button("Ro‘yxatni ochish", "a:open")],
                [_button("Turnirni boshlash", "a:start")],
                [_button("Minimumni tasdiqlash", "a:override")],
                [_button("Ishtirokchilar", "a:roster")],
                [_button("Ishtirokchi qo‘shish", "a:add")],
            ]
        )
    elif status == TournamentStatus.PAIRING_PREVIEW.value:
        rows.extend(
            [
                [_button("Juftlik variantlari", "a:variants")],
                [_button("Qayta juftlash", "a:reroll")],
                [_button("Erta yakunlash", "a:early")],
            ]
        )
    elif status in {TournamentStatus.ROUND_ACTIVE.value, TournamentStatus.ROUND_REVIEW.value}:
        rows.append([_button("O‘yinlar", "a:matches")])
        if status == TournamentStatus.ROUND_REVIEW.value:
            rows.append([_button("Keyingi bosqich", "a:advance")])
            rows.append([_button("Erta yakunlash", "a:early")])
    elif status == TournamentStatus.TIEBREAK_PENDING.value:
        rows.append([_button("Pley-offni boshlash", "a:tbstart")])
    elif status == TournamentStatus.TIEBREAK_ACTIVE.value:
        rows.append([_button("Pley-off o‘yinlari", "a:tbmatches")])
    elif status == TournamentStatus.FINAL_REVIEW.value:
        rows.append([_button("Yakuniy natija", "a:final")])
        rows.append([_button("Yakunlashni tasdiqlash", "a:finalize")])
    elif status == TournamentStatus.ROUND_PAUSED.value:
        rows.append([_button("Davom ettirish", "a:resume")])

    if status not in {TournamentStatus.ROUND_PAUSED.value}:
        rows.append([_button("To‘xtatish", "a:pause")])
    rows.append([_button("Bekor qilish", "a:cancel")])
    rows.append([_button("Bildirishnomalar", "a:notif")])
    rows.append([_button("Bosh menyu", "a:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def group_choice_keyboard(groups: list[ConnectedGroup]) -> InlineKeyboardMarkup:
    rows = [[_button(group.title[:40], f"a:group:{group.id}")] for group in groups]
    rows.append([_button("Orqaga", "a:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def roster_keyboard(participants: list[TournamentParticipant]) -> InlineKeyboardMarkup:
    rows = []
    for participant in participants:
        rows.append(
            [
                _button(participant.display_name[:24], f"a:pname:{participant.id}"),
                _button("O‘chirish", f"a:premove:{participant.id}"),
            ]
        )
    rows.append([_button("Orqaga", "a:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def variant_keyboard(variants: list[PairingVariant]) -> InlineKeyboardMarkup:
    rows = []
    for variant in variants:
        rows.append(
            [
                _button(f"Variant {variant.variant_index}", f"a:vshow:{variant.id}"),
                _button("Tasdiqlash", f"a:vok:{variant.id}"),
            ]
        )
    rows.append([_button("Qayta juftlash", "a:reroll")])
    rows.append([_button("Orqaga", "a:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def match_keyboard(matches: list[Match], names: dict[int, str]) -> InlineKeyboardMarkup:
    rows = []
    for match in matches:
        label = f"{names.get(match.player1_id, '?')} — {names.get(match.player2_id, '?')}"
        rows.append([_button(label[:40], f"a:match:{match.id}")])
    rows.append([_button("Orqaga", "a:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def result_keyboard(match_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _button("1-o‘yinchi g‘alaba", f"a:pick:{match_id}:P1_WIN"),
                _button("Durang", f"a:pick:{match_id}:DRAW"),
            ],
            [_button("2-o‘yinchi g‘alaba", f"a:pick:{match_id}:P2_WIN")],
            [
                _button("1 texnik", f"a:pick:{match_id}:TECHNICAL_P1"),
                _button("2 texnik", f"a:pick:{match_id}:TECHNICAL_P2"),
            ],
            [_button("Orqaga", "a:matches")],
        ]
    )


def confirm_result_keyboard(match_id: int, outcome: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button("Tasdiqlash", f"a:rok:{match_id}:{outcome}")],
            [_button("Bekor qilish", f"a:match:{match_id}")],
        ]
    )


def withdraw_keyboard(participant_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button("Turnirdan chiqarish", f"a:out:{participant_id}")],
            [_button("Keyingi raundlarda qoladi", "a:matches")],
        ]
    )


def tiebreak_keyboard(matches: list[TiebreakMatch], names: dict[int, str]) -> InlineKeyboardMarkup:
    rows = []
    for match in matches:
        if match.status == "CANCELLED" or match.confirmed:
            continue
        label = f"{names.get(match.player1_id, '?')} — {names.get(match.player2_id, '?')}"
        rows.append([_button(label[:40], f"a:tb:{match.id}")])
    rows.append([_button("Orqaga", "a:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tiebreak_result_keyboard(match_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _button("1 g‘alaba", f"a:tbpick:{match_id}:P1_WIN"),
                _button("Durang", f"a:tbpick:{match_id}:DRAW"),
                _button("2 g‘alaba", f"a:tbpick:{match_id}:P2_WIN"),
            ],
            [
                _button("1 kelmadi", f"a:tbabs:{match_id}:1"),
                _button("2 kelmadi", f"a:tbabs:{match_id}:2"),
            ],
            [_button("Orqaga", "a:tbmatches")],
        ]
    )


def tiebreak_confirm_keyboard(match_id: int, outcome: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button("Tasdiqlash", f"a:tbok:{match_id}:{outcome}")],
            [_button("Bekor qilish", f"a:tb:{match_id}")],
        ]
    )


def yes_no_keyboard(yes: str, no: str = "a:home") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button("Tasdiqlash", yes), _button("Bekor qilish", no)],
        ]
    )


def name_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button("Ismni tasdiqlash", "u:regok")],
            [_button("Ismni o‘zgartirish", "u:regedit")],
        ]
    )


def prize_default_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button("Standart mukofotlar", "a:prizes:default")],
            [_button("Orqaga", "a:home")],
        ]
    )


def notification_keyboard(notification_id: int, *, uncertain: bool) -> InlineKeyboardMarkup:
    data = f"a:nforce:{notification_id}" if uncertain else f"a:nretry:{notification_id}"
    label = "Aniq qayta yuborish" if uncertain else "Qayta yuborish"
    return InlineKeyboardMarkup(
        inline_keyboard=[[_button(label, data)], [_button("Orqaga", "a:home")]]
    )
