"""Configuration checks that keep Swiss rounds pairable."""

from __future__ import annotations

from app.domain.enums import MAX_PARTICIPANTS_CAP
from app.domain.exceptions import InvalidConfig


def max_swiss_rounds(participant_count: int) -> int:
    """Maximum rounds with no repeated opponents.

    Even N has a 1-factorization of K_N into N-1 perfect matchings.
    Odd N decomposes into N near-perfect matchings (one bye each round).
    """

    if participant_count < 2:
        return 0
    if participant_count % 2 == 0:
        return participant_count - 1
    return participant_count


def clean_tournament_name(value: str) -> str:
    cleaned = " ".join(value.strip().split())
    if len(cleaned) < 2 or len(cleaned) > 120:
        raise InvalidConfig("Turnir nomi 2 tadan 120 tagacha belgidan iborat bo‘lishi kerak.")
    return cleaned


def clean_person_name(value: str, *, field: str) -> str:
    cleaned = " ".join(value.strip().split())
    if len(cleaned) < 2 or len(cleaned) > 64:
        raise InvalidConfig(f"{field} 2 tadan 64 tagacha belgidan iborat bo‘lishi kerak.")
    return cleaned


def validate_limits(min_participants: int, max_participants: int, swiss_rounds: int) -> None:
    if min_participants < 2:
        raise InvalidConfig("Ishtirokchilar soni kamida 2 bo‘lishi kerak.")
    if max_participants < min_participants:
        raise InvalidConfig("Maksimal son minimal sondan kichik bo‘lishi mumkin emas.")
    if max_participants > MAX_PARTICIPANTS_CAP:
        raise InvalidConfig(f"Maksimal ishtirokchi soni {MAX_PARTICIPANTS_CAP} dan oshmaydi.")
    if swiss_rounds < 1:
        raise InvalidConfig("Kamida 1 ta shveytsariya raundi kerak.")
    possible = max_swiss_rounds(min_participants)
    if swiss_rounds > possible:
        raise InvalidConfig(
            f"{min_participants} ishtirokchi bilan takroriy raqibsiz "
            f"eng ko‘pi {possible} raund o‘tkazish mumkin."
        )


def validate_prizes(first: int, second: int, third: int) -> None:
    for label, amount in (("1-o‘rin", first), ("2-o‘rin", second), ("3-o‘rin", third)):
        if amount < 0:
            raise InvalidConfig(f"{label} mukofoti manfiy bo‘lishi mumkin emas.")


def assert_field_can_play(participant_count: int, swiss_rounds: int) -> None:
    """Reject a start or minimum override that cannot finish the configured rounds."""

    if participant_count < 2:
        raise InvalidConfig("Juftlash uchun kamida 2 nafar faol ishtirokchi kerak.")
    possible = max_swiss_rounds(participant_count)
    if swiss_rounds > possible:
        raise InvalidConfig(
            f"{participant_count} ishtirokchi bilan {swiss_rounds} raund "
            f"takroriy raqibsiz o‘tkazib bo‘lmaydi. Eng ko‘pi {possible} raund."
        )
