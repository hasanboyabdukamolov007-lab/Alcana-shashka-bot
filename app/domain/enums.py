"""Persistent tournament enumerations."""

from __future__ import annotations

from enum import StrEnum


class TournamentStatus(StrEnum):
    DRAFT = "DRAFT"
    REGISTRATION_OPEN = "REGISTRATION_OPEN"
    REGISTRATION_CLOSED = "REGISTRATION_CLOSED"
    PAIRING_PREVIEW = "PAIRING_PREVIEW"
    ROUND_ACTIVE = "ROUND_ACTIVE"
    ROUND_REVIEW = "ROUND_REVIEW"
    ROUND_PAUSED = "ROUND_PAUSED"
    TIEBREAK_PENDING = "TIEBREAK_PENDING"
    TIEBREAK_ACTIVE = "TIEBREAK_ACTIVE"
    FINAL_REVIEW = "FINAL_REVIEW"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


TERMINAL_STATUSES = frozenset({TournamentStatus.COMPLETED, TournamentStatus.CANCELLED})

PRE_START_STATUSES = frozenset(
    {
        TournamentStatus.DRAFT,
        TournamentStatus.REGISTRATION_OPEN,
        TournamentStatus.REGISTRATION_CLOSED,
    }
)


class ParticipantStatus(StrEnum):
    REGISTERED = "REGISTERED"
    WAITLIST = "WAITLIST"
    ACTIVE = "ACTIVE"
    WITHDRAWN = "WITHDRAWN"


class RoundStatus(StrEnum):
    PREVIEW = "PREVIEW"
    ACTIVE = "ACTIVE"
    REVIEW = "REVIEW"
    CLOSED = "CLOSED"


class MatchStatus(StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    LOCKED = "LOCKED"


class Outcome(StrEnum):
    P1_WIN = "P1_WIN"
    DRAW = "DRAW"
    P2_WIN = "P2_WIN"
    TECHNICAL_P1 = "TECHNICAL_P1"
    TECHNICAL_P2 = "TECHNICAL_P2"


class ResultKind(StrEnum):
    REAL = "real"
    TECHNICAL = "technical"
    BYE = "bye"


class NotificationStatus(StrEnum):
    PENDING = "PENDING"
    SENDING = "SENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    UNCERTAIN = "UNCERTAIN"


class NotificationType(StrEnum):
    PAIRING = "PAIRING"
    TIEBREAK_OPPONENT = "TIEBREAK_OPPONENT"
    PRIZE = "PRIZE"
    FINAL_STATS = "FINAL_STATS"
    WAITLIST_PROMOTED = "WAITLIST_PROMOTED"
    ADMIN_ALERT = "ADMIN_ALERT"
    WITHDRAWN_NOTICE = "WITHDRAWN_NOTICE"


class TiebreakFormat(StrEnum):
    HEAD_TO_HEAD = "HEAD_TO_HEAD"
    ROUND_ROBIN = "ROUND_ROBIN"


class TiebreakStageStatus(StrEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"


class GroupMessageType(StrEnum):
    REGISTRATION = "registration"
    ROUND = "round"
    STANDINGS = "standings"
    TIEBREAK = "tiebreak"
    FINAL = "final"
    CANCELLED = "cancelled"


class CloseReason(StrEnum):
    ADMIN = "admin"
    CAPACITY = "capacity"


class BackupStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


DEFAULT_PRIZE_FIRST = 200_000
DEFAULT_PRIZE_SECOND = 100_000
DEFAULT_PRIZE_THIRD = 50_000
DEFAULT_MIN_PARTICIPANTS = 8
DEFAULT_MAX_PARTICIPANTS = 16
DEFAULT_SWISS_ROUNDS = 5
MAX_PARTICIPANTS_CAP = 64
MAX_REROLLS = 3
MAX_VARIANTS = 4
BYE_POINTS = 2
WIN_POINTS = 2
DRAW_POINTS = 1
LOSS_POINTS = 0
PRIZE_SLOTS = 3
