from __future__ import annotations

import logging
from html import escape

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot.context import BotContext
from app.bot.hh_callback_data import (
    ConfirmationCallback,
    DraftApplicationCallback,
    HHOAuthCallback,
    PrepareApplicationCallback,
    ResumeCallback,
)
from app.bot.keyboards import (
    application_edit_keyboard,
    application_preview_keyboard,
    application_result_keyboard,
    back_keyboard,
    connect_hh_keyboard,
    final_confirmation_keyboard,
    resume_keyboard,
)
from app.bot.screens import (
    application_loading_text,
    application_preview_text,
    confirmation_text,
    edit_letter_text,
    hh_text,
    result_text,
)
from app.bot.views import show_current_collection, show_main
from app.schemas import PreparedApplication
from app.services.hh_application import (
    HHApplicationError,
    HHNotAuthorizedError,
    HHResumeSelectionRequired,
)
from app.sources.hh import HHAuthorizationError

logger = logging.getLogger(__name__)


class CoverLetterEdit(StatesGroup):
    waiting_for_text = State()


def _error_text(message: str) -> str:
    return f"<b>⚠️ Не удалось продолжить</b>\n\n{escape(message)}"


async def _show_preview(
    context: BotContext,
    target: Message | CallbackQuery,
    preview: PreparedApplication,
) -> None:
    await context.ui.render(
        target,
        application_preview_text(preview),
        application_preview_keyboard(
            preview.draft_id,
            multiple_resumes=len(preview.resumes) > 1,
            manual_submission_required=preview.manual_submission_required,
        ),
        screen="application",
    )


async def _return_to_collection(
    context: BotContext, target: Message | CallbackQuery
) -> None:
    chat_id = (
        target.message.chat.id
        if isinstance(target, CallbackQuery) and isinstance(target.message, Message)
        else target.chat.id
        if isinstance(target, Message)
        else None
    )
    if chat_id is not None and context.ui.session(chat_id).collection_ids:
        await show_current_collection(context, target)
    else:
        await show_main(context, target)


def build_hh_applications_router(context: BotContext) -> Router:
    router = Router(name="hh_applications")

    async def is_authorized(callback: CallbackQuery) -> bool:
        if callback.from_user.id == context.settings.telegram_user_id:
            return True
        await callback.answer("Этот бот является приватным.", show_alert=True)
        return False

    async def show_connect(target: Message | CallbackQuery) -> None:
        configured = context.settings.hh_oauth_configured
        await context.ui.render(
            target,
            hh_text(connected=False, resumes=0, configured=configured),
            connect_hh_keyboard() if configured else back_keyboard(),
            screen="hh",
        )

    @router.callback_query(HHOAuthCallback.filter())
    async def oauth_callback(
        callback: CallbackQuery, callback_data: HHOAuthCallback
    ) -> None:
        if not await is_authorized(callback):
            return
        await callback.answer("Открываю HeadHunter…")
        if callback_data.action != "connect" or not isinstance(
            callback.message, Message
        ):
            return
        try:
            url = await context.hh_oauth_service.create_authorization_url(
                callback.from_user.id
            )
        except HHAuthorizationError:
            await context.ui.render(
                callback,
                _error_text("OAuth HeadHunter не настроен. Проверьте переменные HH_*."),
                back_keyboard(),
                screen="hh",
            )
            return
        await context.ui.render(
            callback,
            (
                "<b>🔐 Подтвердите доступ</b>\n\n"
                "Откройте официальную страницу HeadHunter и разрешите доступ. "
                f"Ссылка действует {context.settings.hh_oauth_state_ttl_seconds // 60} минут."
            ),
            connect_hh_keyboard(url),
            screen="hh_oauth",
        )

    @router.callback_query(PrepareApplicationCallback.filter())
    async def prepare_callback(
        callback: CallbackQuery, callback_data: PrepareApplicationCallback
    ) -> None:
        if not await is_authorized(callback):
            return
        await callback.answer("Готовлю отклик…")
        if not isinstance(callback.message, Message):
            return
        chat_id = callback.message.chat.id
        await context.ui.restore(chat_id)
        context.ui.focus_vacancy(chat_id, callback_data.vacancy_id)
        await context.ui.render(
            callback,
            application_loading_text(),
            back_keyboard(),
            screen="application_loading",
        )
        operation = context.ui.start_operation(chat_id)
        try:
            preview = await context.hh_application_service.prepare_application(
                user_id=callback.from_user.id,
                vacancy_id=callback_data.vacancy_id,
            )
        except HHNotAuthorizedError:
            if not context.ui.operation_is_current(chat_id, operation):
                return
            context.ui.set_pending_vacancy(chat_id, callback_data.vacancy_id)
            await show_connect(callback)
            return
        except HHResumeSelectionRequired as exc:
            if not context.ui.operation_is_current(chat_id, operation):
                return
            await context.ui.render(
                callback,
                "<b>📄 Выберите резюме</b>\n\nДля этой вакансии нужно выбрать резюме.",
                resume_keyboard(callback_data.vacancy_id, exc.resumes),
                screen="resume",
            )
            return
        except HHApplicationError as exc:
            logger.warning(
                "Could not prepare HH application for vacancy %s: %s",
                callback_data.vacancy_id,
                type(exc).__name__,
            )
            if not context.ui.operation_is_current(chat_id, operation):
                return
            await context.ui.render(
                callback,
                _error_text(exc.user_message),
                back_keyboard(),
                screen="application_result",
            )
            return
        if not context.ui.operation_is_current(chat_id, operation):
            return
        context.ui.set_pending_vacancy(chat_id, None)
        await _show_preview(context, callback, preview)

    @router.callback_query(ResumeCallback.filter())
    async def resume_callback(
        callback: CallbackQuery, callback_data: ResumeCallback
    ) -> None:
        if not await is_authorized(callback):
            return
        await callback.answer("Выбираю резюме…")
        if not isinstance(callback.message, Message):
            return
        chat_id = callback.message.chat.id
        operation = context.ui.start_operation(chat_id)
        resume = await context.hh_integration_repository.get_owned_resume(
            telegram_user_id=callback.from_user.id,
            resume_id=callback_data.resume_id,
        )
        if not context.ui.operation_is_current(chat_id, operation):
            return
        if resume is None:
            await context.ui.render(
                callback,
                _error_text("Резюме больше недоступно. Выберите другое."),
                back_keyboard(),
                screen="resume",
            )
            return
        await context.ui.render(
            callback,
            application_loading_text(),
            back_keyboard(),
            screen="application_loading",
        )
        try:
            preview = await context.hh_application_service.prepare_application(
                user_id=callback.from_user.id,
                vacancy_id=callback_data.vacancy_id,
                resume_id=resume.external_id,
            )
        except HHApplicationError as exc:
            if not context.ui.operation_is_current(chat_id, operation):
                return
            await context.ui.render(
                callback,
                _error_text(exc.user_message),
                back_keyboard(),
                screen="application_result",
            )
            return
        if not context.ui.operation_is_current(chat_id, operation):
            return
        await _show_preview(context, callback, preview)

    @router.callback_query(DraftApplicationCallback.filter())
    async def draft_callback(
        callback: CallbackQuery,
        callback_data: DraftApplicationCallback,
        state: FSMContext,
    ) -> None:
        if not await is_authorized(callback):
            return
        await callback.answer()
        if not isinstance(callback.message, Message):
            return
        preview = await context.hh_application_service.get_preview(
            user_id=callback.from_user.id,
            application_id=callback_data.application_id,
        )
        if preview is None:
            await context.ui.render(
                callback,
                _error_text("Черновик больше недоступен."),
                back_keyboard(),
                screen="application_result",
            )
            return

        action = callback_data.action
        if action != "edit":
            await state.clear()
        if action == "edit":
            await state.set_state(CoverLetterEdit.waiting_for_text)
            await state.update_data(application_id=preview.draft_id)
            await context.ui.render(
                callback,
                edit_letter_text(),
                application_edit_keyboard(preview.draft_id),
                screen="application_edit",
            )
            return
        if action == "resume":
            await context.ui.render(
                callback,
                "<b>📄 Выберите резюме</b>",
                resume_keyboard(preview.vacancy_id, preview.resumes),
                screen="resume",
            )
            return
        if action == "cancel":
            await _return_to_collection(context, callback)
            return
        if action == "back":
            await _show_preview(context, callback, preview)
            return
        if action != "confirm":
            await context.ui.render(
                callback,
                _error_text("Эта кнопка устарела."),
                back_keyboard(),
                screen="application_result",
            )
            return

        try:
            confirmation = await context.hh_application_service.create_confirmation(
                user_id=callback.from_user.id,
                application_id=preview.draft_id,
            )
        except HHApplicationError as exc:
            await context.ui.render(
                callback,
                _error_text(exc.user_message),
                back_keyboard(),
                screen="application_result",
            )
            return
        await context.ui.render(
            callback,
            confirmation_text(preview),
            final_confirmation_keyboard(
                confirmation.token,
                preview.draft_id,
                manual_submission_required=preview.manual_submission_required,
            ),
            screen="confirmation",
        )

    @router.message(CoverLetterEdit.waiting_for_text)
    async def cover_letter_edit(message: Message, state: FSMContext) -> None:
        if (
            not message.from_user
            or message.from_user.id != context.settings.telegram_user_id
        ):
            return
        data = await state.get_data()
        application_id = data.get("application_id")
        if not isinstance(application_id, int):
            await state.clear()
            await context.ui.render(
                message,
                _error_text("Состояние редактирования устарело."),
                back_keyboard(),
                screen="application_result",
            )
            return
        if not message.text:
            await context.ui.render(
                message,
                edit_letter_text(),
                application_edit_keyboard(application_id),
                screen="application_edit",
            )
            return
        text = message.text
        try:
            preview = await context.hh_application_service.update_cover_letter(
                user_id=message.from_user.id,
                application_id=application_id,
                cover_letter=text,
            )
        except HHApplicationError as exc:
            await context.ui.render(
                message,
                _error_text(exc.user_message),
                application_edit_keyboard(application_id),
                screen="application_edit",
            )
            return
        await state.clear()
        if preview is None:
            await context.ui.render(
                message,
                _error_text("Черновик больше недоступен."),
                back_keyboard(),
                screen="application_result",
            )
            return
        await _show_preview(context, message, preview)

    @router.callback_query(ConfirmationCallback.filter())
    async def final_confirmation_callback(
        callback: CallbackQuery, callback_data: ConfirmationCallback
    ) -> None:
        if not await is_authorized(callback):
            return
        await callback.answer("Отправляю отклик…")
        if not isinstance(callback.message, Message):
            return
        chat_id = callback.message.chat.id
        operation = context.ui.start_operation(chat_id)
        await context.ui.render(
            callback,
            "<b>📤 Отправляю отклик</b>\n\nЖду подтверждение от HeadHunter…",
            None,
            screen="confirmation",
        )
        try:
            result = await context.hh_application_service.submit_application(
                user_id=callback.from_user.id,
                confirmation_token=callback_data.token,
            )
        except Exception:
            logger.exception("Unexpected final HH application error")
            if not context.ui.operation_is_current(chat_id, operation):
                return
            await context.ui.render(
                callback,
                _error_text(
                    "Не удалось подтвердить результат. Проверьте отклики на HeadHunter."
                ),
                application_result_keyboard(),
                screen="application_result",
            )
            return
        if result.status == "submitted" and result.vacancy_id is not None:
            context.ui.remove_from_collection(chat_id, result.vacancy_id)
        if not context.ui.operation_is_current(chat_id, operation):
            return
        await context.ui.render(
            callback,
            result_text(result.message, status=result.status),
            application_result_keyboard(result.manual_url),
            screen="application_result",
        )

    return router
