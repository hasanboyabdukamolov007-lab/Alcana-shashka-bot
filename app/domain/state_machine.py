"""Validated tournament state transitions.

Pause stores the previous status and always returns to that exact status.
Completed and cancelled tournaments are terminal.
"""

from __future__ import annotations

from app.domain.enums import TERMINAL_STATUSES, TournamentStatus
from app.domain.exceptions import InvalidTransition

_TRANSITIONS: dict[TournamentStatus, frozenset[TournamentStatus]] = {
    TournamentStatus.DRAFT: frozenset(
        {TournamentStatus.REGISTRATION_OPEN, TournamentStatus.CANCELLED}
    ),
    TournamentStatus.REGISTRATION_OPEN: frozenset(
        {
            TournamentStatus.REGISTRATION_CLOSED,
            TournamentStatus.ROUND_PAUSED,
            TournamentStatus.CANCELLED,
        }
    ),
    TournamentStatus.REGISTRATION_CLOSED: frozenset(
        {
            TournamentStatus.REGISTRATION_OPEN,
            TournamentStatus.PAIRING_PREVIEW,
            TournamentStatus.ROUND_PAUSED,
            TournamentStatus.CANCELLED,
        }
    ),
    TournamentStatus.PAIRING_PREVIEW: frozenset(
        {
            TournamentStatus.ROUND_ACTIVE,
            TournamentStatus.FINAL_REVIEW,
            TournamentStatus.TIEBREAK_PENDING,
            TournamentStatus.ROUND_PAUSED,
            TournamentStatus.CANCELLED,
            TournamentStatus.REGISTRATION_CLOSED,
        }
    ),
    TournamentStatus.ROUND_ACTIVE: frozenset(
        {
            TournamentStatus.ROUND_REVIEW,
            TournamentStatus.ROUND_PAUSED,
            TournamentStatus.CANCELLED,
        }
    ),
    TournamentStatus.ROUND_REVIEW: frozenset(
        {
            TournamentStatus.PAIRING_PREVIEW,
            TournamentStatus.TIEBREAK_PENDING,
            TournamentStatus.FINAL_REVIEW,
            TournamentStatus.ROUND_PAUSED,
            TournamentStatus.CANCELLED,
        }
    ),
    TournamentStatus.ROUND_PAUSED: frozenset(),
    TournamentStatus.TIEBREAK_PENDING: frozenset(
        {
            TournamentStatus.TIEBREAK_ACTIVE,
            TournamentStatus.FINAL_REVIEW,
            TournamentStatus.ROUND_PAUSED,
            TournamentStatus.CANCELLED,
        }
    ),
    TournamentStatus.TIEBREAK_ACTIVE: frozenset(
        {
            TournamentStatus.TIEBREAK_PENDING,
            TournamentStatus.FINAL_REVIEW,
            TournamentStatus.ROUND_PAUSED,
            TournamentStatus.CANCELLED,
        }
    ),
    TournamentStatus.FINAL_REVIEW: frozenset(
        {TournamentStatus.COMPLETED, TournamentStatus.CANCELLED}
    ),
    TournamentStatus.COMPLETED: frozenset(),
    TournamentStatus.CANCELLED: frozenset(),
}


def assert_transition(
    current: TournamentStatus | str,
    new: TournamentStatus | str,
    *,
    previous: TournamentStatus | str | None = None,
) -> None:
    """Raise InvalidTransition when `current` cannot move to `new`."""

    current_status = TournamentStatus(current)
    new_status = TournamentStatus(new)
    if current_status in TERMINAL_STATUSES:
        raise InvalidTransition(f"Yakuniy holat o‘zgarmaydi: {current_status.value}.")
    if current_status == TournamentStatus.ROUND_PAUSED:
        if new_status == TournamentStatus.CANCELLED:
            return
        if previous is None:
            raise InvalidTransition("To‘xtatilgan turnirning oldingi holati saqlanmagan.")
        previous_status = TournamentStatus(previous)
        if (
            new_status != previous_status
            or previous_status in TERMINAL_STATUSES
            or previous_status == TournamentStatus.ROUND_PAUSED
        ):
            raise InvalidTransition("To‘xtatilgan turnir faqat oldingi holatga qaytadi.")
        return
    allowed = _TRANSITIONS[current_status]
    if new_status not in allowed:
        raise InvalidTransition(
            f"{current_status.value} holatidan {new_status.value} holatiga o‘tish mumkin emas."
        )


def can_pause(current: TournamentStatus | str) -> bool:
    current_status = TournamentStatus(current)
    return TournamentStatus.ROUND_PAUSED in _TRANSITIONS[current_status]
