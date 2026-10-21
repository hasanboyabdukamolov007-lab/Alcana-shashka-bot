"""Normalized tournament schema.

`is_live` is TRUE for the single non-terminal tournament and NULL afterwards.
A unique constraint therefore allows many finished tournaments and only one
live tournament on PostgreSQL and SQLite. The same NULL/TRUE pattern prevents
two current results for one match.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.domain.enums import (
    DEFAULT_MAX_PARTICIPANTS,
    DEFAULT_MIN_PARTICIPANTS,
    DEFAULT_PRIZE_FIRST,
    DEFAULT_PRIZE_SECOND,
    DEFAULT_PRIZE_THIRD,
    DEFAULT_SWISS_ROUNDS,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str] = mapped_column(String(64), nullable=False)
    last_name: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (UniqueConstraint("telegram_id", name="uq_users_telegram_id"),)


class ConnectedGroup(Base):
    __tablename__ = "connected_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_group_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    connected_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    can_pin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (UniqueConstraint("telegram_group_id", name="uq_connected_groups_telegram"),)


class Tournament(Base):
    __tablename__ = "tournaments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    previous_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("connected_groups.id"), nullable=True)
    created_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_live: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=True)
    current_round_number: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    early_terminated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    horizon_rounds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    number_seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    registration_closed_by: Mapped[str | None] = mapped_column(String(16), nullable=True)
    min_override: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    prize_clusters: Mapped[list | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("is_live", name="uq_tournaments_single_live"),
        Index("ix_tournaments_status", "status"),
    )


class TournamentSettings(Base):
    __tablename__ = "tournament_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id"), nullable=False)
    min_participants: Mapped[int] = mapped_column(Integer, default=DEFAULT_MIN_PARTICIPANTS)
    max_participants: Mapped[int] = mapped_column(Integer, default=DEFAULT_MAX_PARTICIPANTS)
    swiss_rounds: Mapped[int] = mapped_column(Integer, default=DEFAULT_SWISS_ROUNDS)
    prize_first: Mapped[int] = mapped_column(Integer, default=DEFAULT_PRIZE_FIRST)
    prize_second: Mapped[int] = mapped_column(Integer, default=DEFAULT_PRIZE_SECOND)
    prize_third: Mapped[int] = mapped_column(Integer, default=DEFAULT_PRIZE_THIRD)

    __table_args__ = (
        UniqueConstraint("tournament_id", name="uq_settings_tournament"),
        CheckConstraint("min_participants >= 2", name="ck_settings_min"),
        CheckConstraint("max_participants >= min_participants", name="ck_settings_max"),
        CheckConstraint("swiss_rounds >= 1", name="ck_settings_rounds"),
        CheckConstraint("prize_first >= 0", name="ck_prize_first"),
        CheckConstraint("prize_second >= 0", name="ck_prize_second"),
        CheckConstraint("prize_third >= 0", name="ck_prize_third"),
    )


class TournamentParticipant(Base):
    __tablename__ = "tournament_participants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    display_name: Mapped[str] = mapped_column(String(140), nullable=False)
    tournament_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    bye_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    prize_excluded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    final_place: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("tournament_id", "user_id", name="uq_participant_user"),
        UniqueConstraint(
            "tournament_id",
            "tournament_number",
            name="uq_participant_number",
        ),
        Index("ix_participants_tournament_status", "tournament_id", "status"),
        CheckConstraint("score >= 0", name="ck_participant_score"),
        CheckConstraint("bye_count >= 0", name="ck_participant_byes"),
    )


class RegistrationWaitlist(Base):
    __tablename__ = "registration_waitlist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    participant_id: Mapped[int] = mapped_column(
        ForeignKey("tournament_participants.id"), nullable=False
    )
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("tournament_id", "user_id", name="uq_waitlist_user"),
        Index("ix_waitlist_fifo", "tournament_id", "joined_at", "id"),
    )


class Round(Base):
    __tablename__ = "rounds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id"), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    confirmed_variant_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reroll_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("tournament_id", "number", name="uq_round_number"),
        CheckConstraint("reroll_count >= 0 AND reroll_count <= 3", name="ck_reroll_count"),
    )


class PairingVariant(Base):
    __tablename__ = "pairing_variants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id"), nullable=False)
    variant_index: Mapped[int] = mapped_column(Integer, nullable=False)
    seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    objective_diff: Mapped[int] = mapped_column(Integer, nullable=False)
    bye_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bye_participant_id: Mapped[int | None] = mapped_column(
        ForeignKey("tournament_participants.id"), nullable=True
    )
    is_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("round_id", "variant_index", name="uq_variant_index"),
        CheckConstraint(
            "variant_index >= 1 AND variant_index <= 4",
            name="ck_variant_index",
        ),
    )


class PairingVariantMatch(Base):
    __tablename__ = "pairing_variant_matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("pairing_variants.id"), nullable=False)
    player1_id: Mapped[int] = mapped_column(
        ForeignKey("tournament_participants.id"), nullable=False
    )
    player2_id: Mapped[int] = mapped_column(
        ForeignKey("tournament_participants.id"), nullable=False
    )
    table_order: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (UniqueConstraint("variant_id", "table_order", name="uq_variant_table"),)


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id"), nullable=False)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id"), nullable=False)
    player1_id: Mapped[int] = mapped_column(
        ForeignKey("tournament_participants.id"), nullable=False
    )
    player2_id: Mapped[int] = mapped_column(
        ForeignKey("tournament_participants.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_matches_round", "round_id"),)


class MatchResult(Base):
    __tablename__ = "match_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"), nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    p1_points: Mapped[int] = mapped_column(Integer, nullable=False)
    p2_points: Mapped[int] = mapped_column(Integer, nullable=False)
    is_current: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    recorded_by: Mapped[int] = mapped_column(BigInteger, nullable=False)
    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey("match_results.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("match_id", "is_current", name="uq_match_current_result"),
        CheckConstraint("p1_points >= 0 AND p2_points >= 0", name="ck_result_points"),
    )


class ByeAssignment(Base):
    __tablename__ = "bye_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id"), nullable=False)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id"), nullable=False)
    participant_id: Mapped[int] = mapped_column(
        ForeignKey("tournament_participants.id"), nullable=False
    )
    points_awarded: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("round_id", name="uq_one_bye_per_round"),
        CheckConstraint("points_awarded = 2", name="ck_bye_points"),
    )


class Withdrawal(Base):
    __tablename__ = "withdrawals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id"), nullable=False)
    participant_id: Mapped[int] = mapped_column(
        ForeignKey("tournament_participants.id"), nullable=False
    )
    round_id: Mapped[int | None] = mapped_column(ForeignKey("rounds.id"), nullable=True)
    reason: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    admin_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    withdrew_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("tournament_id", "participant_id", name="uq_withdrawal_once"),
    )


class TiebreakStage(Base):
    __tablename__ = "tiebreak_stages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id"), nullable=False)
    stage_index: Mapped[int] = mapped_column(Integer, nullable=False)
    format: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    player_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    cluster_index: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("tournament_id", "stage_index", name="uq_tiebreak_stage_index"),
    )


class TiebreakMatch(Base):
    __tablename__ = "tiebreak_matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stage_id: Mapped[int] = mapped_column(ForeignKey("tiebreak_stages.id"), nullable=False)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id"), nullable=False)
    player1_id: Mapped[int] = mapped_column(
        ForeignKey("tournament_participants.id"), nullable=False
    )
    player2_id: Mapped[int] = mapped_column(
        ForeignKey("tournament_participants.id"), nullable=False
    )
    game_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    outcome: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_technical: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_tiebreak_matches_stage", "stage_id"),)


class StandingsSnapshot(Base):
    __tablename__ = "standings_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id"), nullable=False)
    round_id: Mapped[int | None] = mapped_column(ForeignKey("rounds.id"), nullable=True)
    label: Mapped[str] = mapped_column(String(32), nullable=False)
    provisional: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    payload: Mapped[list] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_snapshots_tournament", "tournament_id", "created_at"),)


class TelegramMessage(Base):
    __tablename__ = "telegram_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id"), nullable=False)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("connected_groups.id"), nullable=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_type: Mapped[str] = mapped_column(String(32), nullable=False)
    part: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    telegram_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "tournament_id",
            "message_type",
            "part",
            name="uq_telegram_message_part",
        ),
    )


class NotificationOutbox(Base):
    __tablename__ = "notification_outbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_key: Mapped[str] = mapped_column(String(200), nullable=False)
    tournament_id: Mapped[int | None] = mapped_column(ForeignKey("tournaments.id"), nullable=True)
    recipient_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_type: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("event_key", name="uq_notification_event_key"),
        Index("ix_outbox_status_next", "status", "next_attempt_at"),
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_audit_created", "created_at"),)


class BackupJob(Base):
    __tablename__ = "backup_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    path: Mapped[str] = mapped_column(Text, default="", nullable=False)
    method: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
