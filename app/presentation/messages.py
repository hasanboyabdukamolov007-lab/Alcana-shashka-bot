"""Participant and admin copy. The bot UI is Uzbek Latin."""

from __future__ import annotations

from app.domain.enums import PRIZE_SLOTS

MENU_PROFILE = "Mening profilim"
MENU_OPPONENT = "Mening raqibim"
MENU_GAMES = "Mening o‘yinlarim"
MENU_STANDINGS = "Jonli reyting"
MENU_STATUS = "Turnir holati"
MENU_HISTORY = "Oldingi turnirlar"
MENU_PRIZES = "Mukofotlar"

PARTICIPANT_MENU = (
    MENU_PROFILE,
    MENU_OPPONENT,
    MENU_GAMES,
    MENU_STANDINGS,
    MENU_STATUS,
    MENU_HISTORY,
    MENU_PRIZES,
)

TELEGRAM_TEXT_LIMIT = 3900


def money(amount: int) -> str:
    grouped = f"{amount:,}".replace(",", " ")
    return f"{grouped} so‘m"


def split_text(text: str, limit: int = TELEGRAM_TEXT_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current = ""
    for line in text.split("\n"):
        candidate = line if not current else f"{current}\n{line}"
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            parts.append(current)
        while len(line) > limit:
            parts.append(line[:limit])
            line = line[limit:]
        current = line
    if current:
        parts.append(current)
    return parts


def registration_intro(*, registration_open: bool) -> str:
    if registration_open:
        action = "📋 Ro‘yxatdan o‘tish uchun pastdagi tugmani bosing."
    else:
        action = "📋 Ro‘yxatdan o‘tish hozircha yopiq."
    return (
        "🎉 Assalomu alaykum, hurmatli “Alcana Print” jamoasi!\n\n"
        "♟ Oylik shashka musobaqamizga rasman start berildi!\n\n"
        "Barcha ishtirokchilarga munosib raqobat, aniq yurishlar va yorqin g‘alabalar tilaymiz.\n\n"
        f"{action}\n\n"
        "Har bir bahsda mahoratingizni namoyon eting va chempionlik sari dadil harakat qiling!\n\n"
        "🏆 Barcha ishtirokchilarga omad yor bo‘lsin!"
    )


def registration_text(
    *,
    name: str,
    status_label: str,
    names: list[str],
    registered: int,
    maximum: int,
    prizes: tuple[int, int, int],
    waiting: int,
) -> str:
    remaining = max(maximum - registered, 0)
    listed = "\n".join(f"{index}. {item}" for index, item in enumerate(names, start=1))
    if not listed:
        listed = "Hali ishtirokchi yo‘q."
    intro = registration_intro(registration_open=status_label == "OCHIQ")
    body = (
        f"{intro}\n\n"
        f"🏆 {name}\n\n"
        f"Ro‘yxatdan o‘tish: {status_label}\n\n"
        f"Ishtirokchilar ({registered}/{maximum}):\n{listed}\n\n"
        f"Bo‘sh joylar: {remaining}\n"
        f"Kutish ro‘yxati: {waiting}\n\n"
        "Mukofotlar:\n"
        f"1-o‘rin: {money(prizes[0])}\n"
        f"2-o‘rin: {money(prizes[1])}\n"
        f"3-o‘rin: {money(prizes[2])}\n\n"
        "Ishtirok bepul."
    )
    if len(body) <= TELEGRAM_TEXT_LIMIT:
        return body
    short_names = "\n".join(
        f"{index}. {item[:40]}" for index, item in enumerate(names[:30], start=1)
    )
    extra = max(len(names) - 30, 0)
    tail = f"\n… va yana {extra} nafar." if extra else ""
    return (
        f"{intro}\n\n"
        f"🏆 {name}\n\n"
        f"Ro‘yxatdan o‘tish: {status_label}\n\n"
        f"Ishtirokchilar ({registered}/{maximum}):\n{short_names}{tail}\n\n"
        f"Bo‘sh joylar: {remaining}\n"
        f"Mukofotlar: {money(prizes[0])}, {money(prizes[1])}, {money(prizes[2])}\n"
        "Ishtirok bepul."
    )


def pairing_notification(*, tournament_name: str, round_number: int, text: str) -> str:
    return f"{tournament_name}\n{round_number}-raund\n\n{text}"


def bye_private_text(*, tournament_name: str, round_number: int) -> str:
    return (
        f"{tournament_name}\n{round_number}-raund\n\n"
        "Sizga BYE berildi. Bu raundda 2 ball olasiz.\n"
        "BYE haqiqiy g‘alaba hisoblanmaydi."
    )


def opponent_private_text(
    *,
    tournament_name: str,
    round_number: int,
    opponent_number: int,
    opponent_name: str,
) -> str:
    return (
        f"{tournament_name}\n{round_number}-raund\n\n"
        f"Raqibingiz: {opponent_number}-raqam {opponent_name}\n"
        "Natijani administrator kiritadi."
    )


_PLACE_MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


def final_results_announcement(
    *,
    tournament_name: str,
    winners: list[tuple[int, str]],
    missing_note: str = "",
) -> str:
    by_place = {place: name for place, name in winners}
    lines = [
        f"🏆 “{tournament_name}” o‘z yakuniga yetdi!",
        "",
        "Turnir g‘oliblari:",
        "",
    ]
    for place in (1, 2, 3):
        name = by_place.get(place)
        if name is None:
            continue
        medal = _PLACE_MEDALS[place]
        lines.append(f"{medal} {place}-o‘rin: {name}")
    if not any(place in by_place for place in (1, 2, 3)):
        lines.append("G‘oliblar aniqlanmadi.")
    if missing_note:
        lines.extend(["", missing_note])
    lines.extend(
        [
            "",
            "G‘oliblarni munosib natijalari bilan samimiy tabriklaymiz!",
            "",
            "👏 Musobaqada faol ishtirok etgan barcha xodimlarga minnatdorchilik bildiramiz. "
            "Sizlarning mahoratingiz, faolligingiz va sog‘lom raqobatingiz "
            "turnirga o‘zgacha ruh bag‘ishladi.",
            "",
            "♟ Keyingi turnirlarda barchangizga yangi g‘alabalar tilaymiz!",
        ]
    )
    return "\n".join(lines)


def prize_congratulations(
    *,
    player_name: str,
    tournament_name: str,
    place: int,
    amount: int,
) -> str:
    return (
        f"Tabriklaymiz, {player_name}!\n\n"
        f"Siz “{tournament_name}” turnirida {place}-o‘rinni egalladingiz.\n"
        f"Mukofot: {money(amount)}.\n\n"
        "G‘alaba muborak! Ishtirokingiz uchun rahmat."
    )


def final_stats_text(
    *,
    player_name: str,
    tournament_name: str,
    place: int | None,
    points: int,
    wins: int,
    draws: int,
    losses: int,
    withdrawn: bool,
) -> str:
    if withdrawn:
        return (
            f"{player_name}, “{tournament_name}” turniridagi ishtirokingiz yakunlandi.\n\n"
            "Holat: turnirdan chiqarilgan\n"
            f"To‘plangan ball: {points}\n"
            f"Haqiqiy g‘alabalar: {wins}\n"
            f"Duranglar: {draws}\n"
            f"Mag‘lubiyatlar: {losses}\n\n"
            "Chiqarilgan ishtirokchilar mukofot olmaydi."
        )
    place_line = f"{place}-o‘rin" if place is not None else "o‘rin belgilanmagan"
    return (
        f"{player_name}, “{tournament_name}” turniri yakunlandi.\n\n"
        f"Yakuniy o‘rin: {place_line}\n"
        f"Shveytsariya ballari: {points}\n"
        f"Haqiqiy g‘alabalar: {wins}\n"
        f"Duranglar: {draws}\n"
        f"Mag‘lubiyatlar: {losses}"
    )


def promoted_text(*, tournament_name: str) -> str:
    return (
        f"“{tournament_name}” turnirida sizga joy bo‘shadi.\n"
        "Kutish ro‘yxatidan ishtirokchilar safiga o‘tdingiz."
    )


def blocked_admin_text(*, telegram_id: int, message_type: str) -> str:
    return (
        f"Bildirishnoma yuborilmadi. Foydalanuvchi botni bloklagan.\n"
        f"Telegram ID: {telegram_id}\n"
        f"Tur: {message_type}"
    )


def unawarded_note(eligible_count: int) -> str:
    missing = [
        place for place in range(1, PRIZE_SLOTS + 1) if place > min(PRIZE_SLOTS, eligible_count)
    ]
    if not missing:
        return ""
    labels = ", ".join(f"{place}-o‘rin" for place in missing)
    return f"Yakunlagan ishtirokchilar: {eligible_count}. Bo‘sh mukofotlar berilmaydi: {labels}."


def status_label(status: str) -> str:
    labels = {
        "DRAFT": "Qoralama",
        "REGISTRATION_OPEN": "Ro‘yxat ochiq",
        "REGISTRATION_CLOSED": "Ro‘yxat yopiq",
        "PAIRING_PREVIEW": "Juftliklar ko‘rib chiqilmoqda",
        "ROUND_ACTIVE": "Raund davom etmoqda",
        "ROUND_REVIEW": "Raund tekshiruvi",
        "ROUND_PAUSED": "To‘xtatilgan",
        "TIEBREAK_PENDING": "Pley-off kutilmoqda",
        "TIEBREAK_ACTIVE": "Pley-off davom etmoqda",
        "FINAL_REVIEW": "Yakuniy tekshiruv",
        "COMPLETED": "Yakunlangan",
        "CANCELLED": "Bekor qilingan",
    }
    return labels.get(status, status)
