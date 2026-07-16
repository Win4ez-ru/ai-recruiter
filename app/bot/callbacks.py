from __future__ import annotations

import logging

from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Message

from app.bot.context import BotContext
from app.bot.hh_callback_data import VacancyCallback
from app.bot.keyboards import vacancy_keyboard

logger = logging.getLogger(__name__)


def build_callbacks_router(context: BotContext) -> Router:
    router = Router(name="callbacks")

    @router.callback_query(VacancyCallback.filter())
    async def vacancy_callback(
        callback: CallbackQuery, callback_data: VacancyCallback
    ) -> None:
        if callback.from_user.id != context.settings.telegram_user_id:
            await callback.answer("Этот бот является приватным.", show_alert=True)
            return
        action = callback_data.action
        vacancy_id = callback_data.vacancy_id
        if action == "cancel":
            await callback.answer("Отменено.")
            if isinstance(callback.message, Message):
                await callback.message.edit_reply_markup(reply_markup=None)
            return
        vacancy = await context.vacancy_repository.get_by_id(vacancy_id)
        if vacancy is None:
            await callback.answer("Вакансия больше недоступна.", show_alert=True)
            return
        logger.info("Telegram callback %s for vacancy %s", action, vacancy_id)

        if action in {"save", "skip"}:
            status = {"save": "saved", "skip": "skipped"}[action]
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
                "skipped": "Вакансия пропущена.",
            }
            await callback.answer(labels[status])
            return

        await callback.answer("Эта кнопка устарела.", show_alert=True)

    @router.callback_query()
    async def invalid_callback(callback: CallbackQuery) -> None:
        if callback.from_user.id == context.settings.telegram_user_id:
            await callback.answer("Эта кнопка устарела.", show_alert=True)
        else:
            await callback.answer("Этот бот является приватным.", show_alert=True)

    return router
