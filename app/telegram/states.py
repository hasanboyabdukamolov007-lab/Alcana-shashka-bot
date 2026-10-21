"""Conversation states for text entry. Confirmed values are stored in PostgreSQL."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class AdminForm(StatesGroup):
    tournament_name = State()
    min_participants = State()
    max_participants = State()
    rounds = State()
    prize_first = State()
    prize_second = State()
    prize_third = State()
    add_telegram_id = State()
    add_first = State()
    add_last = State()
    edit_participant_id = State()
    edit_first = State()
    edit_last = State()


class RegistrationForm(StatesGroup):
    first_name = State()
    last_name = State()
