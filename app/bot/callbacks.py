from __future__ import annotations

import logging

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.context import BotContext
from app.bot.hh_callback_data import ScreenCallback, VacancyCallback
from app.bot.keyboards import (
    hidden_vacancy_keyboard,
    lifecycle_keyboard,
    manual_application_confirmation_keyboard,
    manual_application_registered_keyboard,
)
from app.bot.screens import (
    hidden_vacancy_text,
    lifecycle_text,
    manual_application_confirmation_text,
    manual_application_registered_text,
)
from app.bot.views import (
    run_search,
    show_collection_kind,
    show_current_collection,
    show_help,
    show_hh,
    show_main,
    show_profile,
    show_stats,
)
from app.vacancy_status import (
    VacancyStatus,
    VacancyStatusSource,
    VacancyStatusTransitionError,
    can_mark_applied_manual,
    has_registered_application,
)

logger = logging.getLogger(__name__)


def build_callbacks_router(context: BotContext) -> Router:
    router = Router(name="callbacks")

    async def authorized(callback: CallbackQuery) -> bool:
        if callback.from_user.id == context.settings.telegram_user_id:
            return True
        await callback.answer("Этот бот является приватным.", show_alert=True)
        return False

    async def restore_collection(chat_id: int, vacancy_id: int) -> None:
        session = await context.ui.restore(chat_id)
        if session.collection_ids or not vacancy_id:
            return
        vacancies = await context.vacancy_repository.list_digest_candidates(
            context.settings.min_score_to_send,
            100,
            only_unsent=False,
        )
        ids = [vacancy.id for vacancy in vacancies]
        if vacancy_id not in ids:
            ids.insert(0, vacancy_id)
        context.ui.set_collection(
            chat_id,
            ids,
            title="Вакансии",
            start_vacancy_id=vacancy_id,
        )

    @router.callback_query(ScreenCallback.filter())
    async def screen_callback(
        callback: CallbackQuery,
        callback_data: ScreenCallback,
        state: FSMContext,
    ) -> None:
        if not await authorized(callback):
            return
        if not isinstance(callback.message, Message):
            await callback.answer()
            return
        action = callback_data.action
        chat_id = callback.message.chat.id
        session = await context.ui.restore(chat_id)

        if action == "noop":
            await callback.answer()
            return
        context.ui.cancel_operation(chat_id)
        if action == "close":
            await callback.answer("Интерфейс закрыт")
            await state.clear()
            await context.ui.close(callback)
            return
        if action == "menu":
            await callback.answer()
            await state.clear()
            await show_main(context, callback)
            return
        if action == "pending_clear":
            await callback.answer("Отклик отменён")
            await state.clear()
            context.ui.set_pending_vacancy(chat_id, None)
            await show_main(context, callback)
            return
        if action == "back":
            await callback.answer()
            await state.clear()
            if (
                session.screen
                in {
                    "application",
                    "application_loading",
                    "application_edit",
                    "confirmation",
                    "application_result",
                    "resume",
                    "manual_apply_confirmation",
                    "manual_apply_result",
                    "hidden_result",
                    "lifecycle",
                }
                and session.collection_ids
            ):
                await show_current_collection(context, callback)
            else:
                await show_main(context, callback)
            return
        if action == "search":
            await callback.answer("Ищу…")
            await state.clear()
            await run_search(context, callback)
            return
        if action in {"new", "top", "saved", "applied"}:
            await callback.answer()
            await state.clear()
            await show_collection_kind(context, callback, action)
            return
        if action == "stats":
            await callback.answer()
            await state.clear()
            await show_stats(context, callback)
            return
        if action == "profile":
            await callback.answer()
            await state.clear()
            await show_profile(context, callback)
            return
        if action == "hh":
            await callback.answer()
            await state.clear()
            await show_hh(context, callback)
            return
        if action == "help":
            await callback.answer()
            await state.clear()
            await show_help(context, callback)
            return
        if action == "manual_apply_continue":
            await callback.answer()
            await show_current_collection(context, callback)
            return
        if action in {"prev", "next"}:
            await callback.answer()
            await restore_collection(chat_id, callback_data.value)
            context.ui.move(chat_id, -1 if action == "prev" else 1)
            await show_current_collection(context, callback)
            return
        if action in {"hide_undo", "hide_continue"}:
            vacancy_id = callback_data.value or session.current_vacancy_id
            vacancy = (
                await context.vacancy_repository.get_by_id(vacancy_id)
                if vacancy_id is not None
                else None
            )
            if vacancy is None:
                await callback.answer("Вакансия больше недоступна", show_alert=True)
                return
            if action == "hide_undo":
                try:
                    await context.application_repository.transition(
                        vacancy.id,
                        VacancyStatus.VIEWED,
                        source=VacancyStatusSource.USER,
                        reason="User undid hiding",
                    )
                except VacancyStatusTransitionError:
                    await callback.answer("Статус уже изменился", show_alert=True)
                else:
                    await callback.answer("Вакансия возвращена")
                await show_current_collection(context, callback)
                return
            context.ui.remove_from_collection(chat_id, vacancy.id)
            await callback.answer()
            await show_current_collection(context, callback)
            return
        if action == "lifecycle" or action.startswith("status_"):
            vacancy_id = callback_data.value or session.current_vacancy_id
            vacancy = (
                await context.vacancy_repository.get_by_id(vacancy_id)
                if vacancy_id is not None
                else None
            )
            if vacancy is None or vacancy.application is None:
                await callback.answer("Статус вакансии недоступен", show_alert=True)
                return
            if action == "lifecycle":
                await callback.answer()
                await context.ui.render(
                    callback,
                    lifecycle_text(vacancy, vacancy.application.status),
                    lifecycle_keyboard(vacancy.id, vacancy.application.status),
                    screen="lifecycle",
                )
                return
            target_status = action.removeprefix("status_")
            try:
                await context.application_repository.transition(
                    vacancy.id,
                    target_status,
                    source=VacancyStatusSource.USER,
                    reason="Lifecycle updated from Telegram UI",
                )
            except (ValueError, VacancyStatusTransitionError):
                await callback.answer(
                    "Такой переход статуса недоступен", show_alert=True
                )
                return
            await callback.answer("Статус обновлён ✅")
            if target_status == VacancyStatus.VIEWED.value and (
                session.collection_kind == "applied"
            ):
                context.ui.remove_from_collection(chat_id, vacancy.id)
            await show_current_collection(context, callback)
            return
        if action in {"manual_apply", "manual_apply_cancel", "manual_apply_yes"}:
            vacancy_id = callback_data.value or session.current_vacancy_id
            vacancy = (
                await context.vacancy_repository.get_by_id(vacancy_id)
                if vacancy_id is not None
                else None
            )
            if vacancy is None:
                await callback.answer("Вакансия больше недоступна", show_alert=True)
                return
            await restore_collection(chat_id, vacancy.id)
            status = vacancy.application.status if vacancy.application else "new"

            if action == "manual_apply_cancel":
                await callback.answer("Отменено")
                await show_current_collection(context, callback)
                return
            if has_registered_application(status):
                await callback.answer("Отклик уже зарегистрирован", show_alert=True)
                await show_current_collection(context, callback)
                return
            if not can_mark_applied_manual(status):
                await callback.answer(
                    "Текущий статус вакансии не позволяет отметить отклик",
                    show_alert=True,
                )
                return
            if action == "manual_apply":
                await callback.answer()
                await context.ui.render(
                    callback,
                    manual_application_confirmation_text(vacancy),
                    manual_application_confirmation_keyboard(vacancy.id),
                    screen="manual_apply_confirmation",
                )
                return

            try:
                await context.application_repository.mark_applied_manual(vacancy.id)
            except VacancyStatusTransitionError:
                logger.info(
                    "Manual application transition became stale for vacancy %s",
                    vacancy.id,
                )
                await callback.answer("Статус вакансии уже изменился", show_alert=True)
                await show_current_collection(context, callback)
                return
            context.ui.remove_from_collection(chat_id, vacancy.id)
            await callback.answer("Отклик зарегистрирован ✅")
            await context.ui.render(
                callback,
                manual_application_registered_text(vacancy),
                manual_application_registered_keyboard(),
                screen="manual_apply_result",
            )
            return
        if action == "full":
            await callback.answer()
            await restore_collection(chat_id, callback_data.value)
            context.ui.toggle_expanded(chat_id)
            await show_current_collection(context, callback)
            return
        if action in {"save", "skip"}:
            await restore_collection(chat_id, callback_data.value)
            vacancy_id = callback_data.value or session.current_vacancy_id
            vacancy = (
                await context.vacancy_repository.get_by_id(vacancy_id)
                if vacancy_id is not None
                else None
            )
            if vacancy is None:
                await callback.answer("Вакансия больше недоступна", show_alert=True)
                return
            current_status = (
                vacancy.application.status if vacancy.application else "new"
            )
            if has_registered_application(current_status):
                await callback.answer("Отклик уже отправлен", show_alert=True)
                return
            if action == "save":
                application = await context.application_repository.toggle_saved(
                    vacancy.id
                )
                await callback.answer(
                    "Убрано из избранного"
                    if application.status == "viewed"
                    else "Сохранено ❤️"
                )
                if (
                    application.status != VacancyStatus.SAVED.value
                    and session.collection_kind == "saved"
                ):
                    context.ui.remove_from_collection(chat_id, vacancy.id)
            else:
                await context.application_repository.hide(vacancy.id)
                await callback.answer("Вакансия скрыта")
                await context.ui.render(
                    callback,
                    hidden_vacancy_text(vacancy),
                    hidden_vacancy_keyboard(vacancy.id),
                    screen="hidden_result",
                )
                return
            await show_current_collection(context, callback)
            return

        await callback.answer("Эта кнопка устарела", show_alert=True)

    @router.callback_query(VacancyCallback.filter())
    async def vacancy_callback(
        callback: CallbackQuery,
        callback_data: VacancyCallback,
        state: FSMContext,
    ) -> None:
        """Handles buttons on cards created by versions before the UI redesign."""
        if not await authorized(callback):
            return
        if not isinstance(callback.message, Message):
            await callback.answer()
            return
        if callback_data.action == "cancel":
            await callback.answer("Закрыто")
            await context.ui.close(callback)
            return
        vacancy = await context.vacancy_repository.get_by_id(callback_data.vacancy_id)
        if vacancy is None:
            await callback.answer("Вакансия больше недоступна", show_alert=True)
            return
        if callback_data.action not in {"save", "skip"}:
            await callback.answer("Эта кнопка устарела", show_alert=True)
            return
        if callback_data.action == "save":
            application = await context.application_repository.toggle_saved(vacancy.id)
            status = application.status
        else:
            await context.application_repository.hide(vacancy.id)
            status = "hidden"
        context.ui.set_collection(
            callback.message.chat.id,
            [vacancy.id],
            title="Вакансия",
        )
        await state.clear()
        await callback.answer("Сохранено ❤️" if status == "saved" else "Вакансия скрыта")
        if status == "hidden":
            context.ui.remove_from_collection(callback.message.chat.id, vacancy.id)
        await show_current_collection(context, callback)

    @router.callback_query()
    async def invalid_callback(callback: CallbackQuery) -> None:
        if callback.from_user.id == context.settings.telegram_user_id:
            await callback.answer("Эта кнопка устарела", show_alert=True)
        else:
            await callback.answer("Этот бот является приватным.", show_alert=True)

    return router
