from __future__ import annotations

import logging
import re
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Message

from app.bot.context import BotContext
from app.bot.formatter import split_plain_text
from app.bot.keyboards import vacancy_keyboard

logger = logging.getLogger(__name__)
CALLBACK_PATTERN = re.compile(r"^(?P<action>[scak]):(?P<id>\d+)$")


def build_callbacks_router(context: BotContext) -> Router:
    router = Router(name="callbacks")

    @router.callback_query(F.data.regexp(CALLBACK_PATTERN.pattern))
    async def vacancy_callback(callback: CallbackQuery) -> None:
        if callback.from_user.id != context.settings.telegram_user_id:
            await callback.answer("Этот бот является приватным.", show_alert=True)
            return
        match = CALLBACK_PATTERN.fullmatch(callback.data or "")
        if match is None:
            await callback.answer("Некорректная кнопка.", show_alert=True)
            return
        action = match.group("action")
        vacancy_id = int(match.group("id"))
        vacancy = await context.vacancy_repository.get_by_id(vacancy_id)
        if vacancy is None:
            await callback.answer("Вакансия больше недоступна.", show_alert=True)
            return
        logger.info("Telegram callback %s for vacancy %s", action, vacancy_id)

        if action in {"s", "a", "k"}:
            status = {"s": "saved", "a": "applied", "k": "skipped"}[action]
            await context.application_repository.set_status(vacancy_id, status)  # type: ignore[arg-type]
            if isinstance(callback.message, Message):
                try:
                    await callback.message.edit_reply_markup(
                        reply_markup=vacancy_keyboard(vacancy_id, vacancy.url, status)
                    )
                except TelegramAPIError:
                    logger.warning("Could not update keyboard for vacancy %s", vacancy_id)
            labels = {
                "saved": "Вакансия сохранена.",
                "applied": "Отклик отмечен.",
                "skipped": "Вакансия пропущена.",
            }
            await callback.answer(labels[status])
            return

        application = await context.application_repository.get(vacancy_id)
        letter = application.cover_letter if application else None
        if not letter:
            await callback.answer("Генерирую письмо…")
            letter = await context.cover_letter_service.generate(vacancy)
            if not letter:
                if isinstance(callback.message, Message):
                    await callback.message.answer(
                        "Не удалось создать письмо: сервис временно недоступен."
                    )
                return
            await context.application_repository.save_cover_letter(vacancy_id, letter)
        else:
            await callback.answer("Использую сохраненное письмо.")
        if isinstance(callback.message, Message):
            for chunk in split_plain_text(letter):
                await callback.message.answer(
                    f"<b>Сопроводительное письмо</b>\n<pre>{escape(chunk)}</pre>"
                )

    @router.callback_query()
    async def invalid_callback(callback: CallbackQuery) -> None:
        if callback.from_user.id == context.settings.telegram_user_id:
            await callback.answer("Эта кнопка устарела.", show_alert=True)

    return router
