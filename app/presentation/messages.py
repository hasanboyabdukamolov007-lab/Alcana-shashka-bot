"""Participant and admin copy. The bot UI is Uzbek Latin."""

from __future__ import annotations

import html
import re

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
    listed = "\n".join(
        f"{bold(f'{index}.')} {html.escape(item, quote=False)}"
        for index, item in enumerate(names, start=1)
    )
    if not listed:
        listed = "Hali ishtirokchi yo‘q."
    intro = registration_intro(registration_open=status_label == "OCHIQ")
    roster_title = bold(f"Ishtirokchilar ({registered}/{maximum})")
    free_line = bold("Ishtirok bepul.")
    body = (
        f"{intro}\n\n"
        f"🏆 {bold(name)}\n\n"
        f"Ro‘yxatdan o‘tish: {bold(status_label)}\n\n"
        f"{roster_title}\n{listed}\n\n"
        f"Bo‘sh joylar: {bold(str(remaining))}\n"
        f"Kutish ro‘yxati: {bold(str(waiting))}\n\n"
        f"{bold('Mukofotlar')}\n"
        f"1-o‘rin: {bold(money(prizes[0]))}\n"
        f"2-o‘rin: {bold(money(prizes[1]))}\n"
        f"3-o‘rin: {bold(money(prizes[2]))}\n\n"
        f"{free_line}"
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
        f"🏆 {bold(name)}\n\n"
        f"Ro‘yxatdan o‘tish: {bold(status_label)}\n\n"
        f"{roster_title}\n"
        f"{html.escape(short_names, quote=False)}{html.escape(tail, quote=False)}\n\n"
        f"Bo‘sh joylar: {bold(str(remaining))}\n"
        f"Mukofotlar: {bold(money(prizes[0]))}, {bold(money(prizes[1]))}, "
        f"{bold(money(prizes[2]))}\n"
        f"{free_line}"
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


def bold(text: str) -> str:
    return f"<b>{html.escape(text, quote=False)}</b>"


def html_parse_mode(text: str) -> str | None:
    if "<b>" in text or "<pre>" in text or "tg://user?id=" in text:
        return "HTML"
    return None


_MONTHS = {
    "sentyabr": "SENTYABR",
    "sentabr": "SENTABR",
    "yanvar": "YANVAR",
    "fevral": "FEVRAL",
    "mart": "MART",
    "aprel": "APREL",
    "may": "MAY",
    "iyun": "IYUN",
    "iyul": "IYUL",
    "avgust": "AVGUST",
    "oktyabr": "OKTABR",
    "noyabr": "NOYABR",
    "dekabr": "DEKABR",
}
_NAME_COLUMN_LIMIT = 32
_STAT_GAPS = (3, 2, 1, 2)
_STAT_HEADERS = ("B", "BH", "SB", "G")


def standings_banner(tournament_name: str) -> str:
    lowered = tournament_name.casefold()
    year = re.search(r"20\d{2}", tournament_name)
    month = next((label for name, label in _MONTHS.items() if name in lowered), None)
    if month is not None and year is not None:
        return bold(f"SHASHKA | {month} {year.group()}")
    return bold(tournament_name)


def _fit(text: str, width: int, *, align: str) -> str:
    if len(text) > width:
        text = text[: width - 1] + "…" if width > 1 else text[:width]
    pad = width - len(text)
    if align == "right":
        return f"{' ' * pad}{text}"
    return f"{text}{' ' * pad}"


def _clip_name(name: str) -> str:
    if len(name) <= _NAME_COLUMN_LIMIT:
        return name
    return f"{name[: _NAME_COLUMN_LIMIT - 1]}…"


def standings_table(
    rows: list[tuple[int | None, str, int, int, str, int]],
) -> str:
    names = [_clip_name(name) for _, name, *_rest in rows]
    numbers = ["--" if number is None else str(number) for number, *_rest in rows]
    stats = [
        (str(points), str(buchholz), sonneborn, str(wins))
        for _, _, points, buchholz, sonneborn, wins in rows
    ]
    number_width = max(2, *(len(item) for item in numbers))
    name_width = max(len("Ishtirokchi"), *(len(item) for item in names))
    stat_widths = [
        max(len(header), *(len(item[index]) for item in stats))
        for index, header in enumerate(_STAT_HEADERS)
    ]
    shown_numbers = [
        "--".rjust(number_width) if number is None else f"{number:0{number_width}d}"
        for number, *_rest in rows
    ]

    def assemble(number: str, name: str, values: tuple[str, str, str, str]) -> str:
        pieces = [
            _fit(number, number_width, align="left"),
            "  ",
            _fit(name, name_width, align="left"),
        ]
        for gap, value, width in zip(_STAT_GAPS, values, stat_widths, strict=True):
            pieces.append(" " * gap)
            pieces.append(_fit(value, width, align="right"))
        return "".join(pieces)

    header = assemble("№", "Ishtirokchi", _STAT_HEADERS)
    body = [
        assemble(number, name, values)
        for number, name, values in zip(shown_numbers, names, stats, strict=True)
    ]
    rule = "─" * len(header)
    return "\n".join([header, rule, *body])


def standings_message(
    *,
    tournament_name: str,
    provisional: bool,
    rows: list[tuple[int | None, str, int, int, str, int]],
    withdrawn: list[tuple[str, int]] | None = None,
) -> str:
    subtitle = "Jonli reyting (vaqtinchalik)" if provisional else "Jonli reyting"
    lines = [f"🏆 {standings_banner(tournament_name)}", "", f"📊 {subtitle}", ""]
    if not rows:
        lines.append("Hali reyting yo‘q.")
    else:
        table = html.escape(standings_table(rows), quote=False)
        lines.extend(
            [
                f"<pre>{table}</pre>",
                "",
                bold("Belgilar:"),
                "B — Ball",
                "BH — Buchholz",
                "SB — Sonneborn–Berger",
                "G — G‘alabalar soni",
                "",
                f"👥 Ishtirokchilar: {len(rows)} nafar",
                "🔄 Natijalar avtomatik yangilanadi.",
            ]
        )
    people = withdrawn or []
    if people:
        lines.extend(["", bold("Chiqarilgan ishtirokchilar"), "Mukofot yo‘q."])
        for name, score in people:
            safe = html.escape(name, quote=False)
            lines.append(f"{safe} · {bold(str(score))} ball")
    return "\n".join(lines).strip()


def mention(name: str, telegram_id: int | None) -> str:
    """Tag a participant in a group message. Without an id, return the plain name."""

    safe = html.escape(name, quote=False)
    if telegram_id is None:
        return safe
    return f'<a href="tg://user?id={telegram_id}">{safe}</a>'


def final_results_announcement(
    *,
    tournament_name: str,
    winners: list[tuple[int, str, int | None]],
    missing_note: str = "",
) -> str:
    by_place = {place: (name, telegram_id) for place, name, telegram_id in winners}
    lines = [
        f"🏆 “{bold(tournament_name)}” o‘z yakuniga yetdi!",
        "",
        bold("Turnir g‘oliblari:"),
        "",
    ]
    for place in (1, 2, 3):
        winner = by_place.get(place)
        if winner is None:
            continue
        name, telegram_id = winner
        medal = _PLACE_MEDALS[place]
        lines.append(f"{medal} {bold(f'{place}-o‘rin')}: {mention(name, telegram_id)}")
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
