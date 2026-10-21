"""aiogram adapter around the outbox and group publisher."""

from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)

from app.domain.exceptions import MessageNotFoundError
from app.services.notifications import DeliveryError, DeliveryResult


class AiogramSender:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def send_message(self, chat_id: int, text: str, reply_markup=None) -> int:
        try:
            message = await self.bot.send_message(chat_id, text, reply_markup=reply_markup)
        except TelegramRetryAfter as exc:
            raise DeliveryError(
                DeliveryResult.RATE_LIMITED, retry_after=int(exc.retry_after)
            ) from exc
        except TelegramForbiddenError as exc:
            raise DeliveryError(DeliveryResult.BLOCKED) from exc
        except TelegramNetworkError as exc:
            raise DeliveryError(DeliveryResult.UNCERTAIN) from exc
        except TelegramBadRequest as exc:
            raise DeliveryError(DeliveryResult.FAILED) from exc
        return message.message_id

    async def edit_message(
        self, chat_id: int, message_id: int, text: str, reply_markup=None
    ) -> None:
        try:
            await self.bot.edit_message_text(
                text,
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=reply_markup,
            )
        except TelegramBadRequest as exc:
            lowered = str(exc).lower()
            if (
                "not found" in lowered
                or "message to edit" in lowered
                or "can't be edited" in lowered
            ):
                raise MessageNotFoundError(str(exc)) from exc
            if "message is not modified" in lowered:
                return
            raise

    async def pin_message(self, chat_id: int, message_id: int) -> None:
        await self.bot.pin_chat_message(chat_id, message_id, disable_notification=True)

    async def unpin_message(self, chat_id: int, message_id: int) -> None:
        await self.bot.unpin_chat_message(chat_id, message_id)
