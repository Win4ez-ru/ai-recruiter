from __future__ import annotations

import logging
from html import escape

from aiogram import Router
from aiogram.exceptions import TelegramAPIError
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
    application_preview_keyboard,
    connect_hh_keyboard,
    final_confirmation_keyboard,
    manual_action_keyboard,
    resume_keyboard,
)
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


def _preview_text(preview: PreparedApplication) -> str:
    return (
        "<b>Предварительный просмотр отклика</b>\n\n"
        f"<b>Вакансия:</b> {escape(preview.vacancy_title)}\n"
        f"<b>Компания:</b> {escape(preview.company)}\n"
        f"<b>Резюме:</b> {escape(preview.resume.title)}\n\n"
        "<b>Сопроводительное письмо:</b>\n"
        f"<pre>{escape(preview.cover_letter)}</pre>"
    )


async def _send_preview(message: Message, preview: PreparedApplication) -> None:
    await message.answer(
        _preview_text(preview),
        reply_markup=application_preview_keyboard(
            preview.draft_id, multiple_resumes=len(preview.resumes) > 1
        ),
    )


def build_hh_applications_router(context: BotContext) -> Router:
    router = Router(name="hh_applications")

    async def is_authorized(callback: CallbackQuery) -> bool:
        if callback.from_user.id == context.settings.telegram_user_id:
            return True
        await callback.answer("Этот бот является приватным.", show_alert=True)
        return False

    async def show_connect(message: Message) -> None:
        if not context.settings.hh_oauth_configured:
            await message.answer(
                "OAuth HeadHunter не настроен. Заполни HH_CLIENT_ID, "
                "HH_CLIENT_SECRET и HH_REDIRECT_URI в .env."
            )
            return
        await message.answer(
            "Подключи аккаунт HeadHunter через официальный OAuth.",
            reply_markup=connect_hh_keyboard(),
        )

    @router.callback_query(HHOAuthCallback.filter())
    async def oauth_callback(
        callback: CallbackQuery, callback_data: HHOAuthCallback
    ) -> None:
        if not await is_authorized(callback):
            return
        await callback.answer()
        if callback_data.action != "connect" or not isinstance(callback.message, Message):
            return
        try:
            url = await context.hh_oauth_service.create_authorization_url(
                callback.from_user.id
            )
        except HHAuthorizationError:
            await callback.message.answer(
                "OAuth HeadHunter не настроен. Проверь переменные HH_* в .env."
            )
            return
        await callback.message.answer(
            "Открой HeadHunter и подтверди доступ. "
            f"Ссылка действует {context.settings.hh_oauth_state_ttl_seconds // 60} мин.",
            reply_markup=connect_hh_keyboard(url),
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
        try:
            preview = await context.hh_application_service.prepare_application(
                user_id=callback.from_user.id,
                vacancy_id=callback_data.vacancy_id,
            )
        except HHNotAuthorizedError:
            await show_connect(callback.message)
            return
        except HHResumeSelectionRequired as exc:
            await callback.message.answer(
                exc.user_message,
                reply_markup=resume_keyboard(callback_data.vacancy_id, exc.resumes),
            )
            return
        except HHApplicationError as exc:
            logger.warning(
                "Could not prepare HH application for vacancy %s: %s",
                callback_data.vacancy_id,
                type(exc).__name__,
            )
            await callback.message.answer(exc.user_message)
            return
        await _send_preview(callback.message, preview)

    @router.callback_query(ResumeCallback.filter())
    async def resume_callback(
        callback: CallbackQuery, callback_data: ResumeCallback
    ) -> None:
        if not await is_authorized(callback):
            return
        await callback.answer("Выбираю резюме…")
        if not isinstance(callback.message, Message):
            return
        resume = await context.hh_integration_repository.get_owned_resume(
            telegram_user_id=callback.from_user.id,
            resume_id=callback_data.resume_id,
        )
        if resume is None:
            await callback.message.answer("Резюме больше недоступно. Выбери другое.")
            return
        try:
            preview = await context.hh_application_service.prepare_application(
                user_id=callback.from_user.id,
                vacancy_id=callback_data.vacancy_id,
                resume_id=resume.external_id,
            )
        except HHApplicationError as exc:
            await callback.message.answer(exc.user_message)
            return
        await _send_preview(callback.message, preview)

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
            await callback.message.answer("Черновик отклика больше недоступен.")
            return

        if callback_data.action != "edit":
            await state.clear()

        if callback_data.action == "edit":
            await state.set_state(CoverLetterEdit.waiting_for_text)
            await state.update_data(application_id=preview.draft_id)
            await callback.message.answer(
                "Отправь новый полный текст сопроводительного письма. "
                "Для отмены нажми кнопку ниже.",
                reply_markup=application_preview_keyboard(
                    preview.draft_id, multiple_resumes=len(preview.resumes) > 1
                ),
            )
            return
        if callback_data.action == "resume":
            await callback.message.answer(
                "Выбери резюме:",
                reply_markup=resume_keyboard(preview.vacancy_id, preview.resumes),
            )
            return
        if callback_data.action == "cancel":
            await state.clear()
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except TelegramAPIError:
                logger.debug("Could not remove cancelled application keyboard")
            await callback.message.answer("Подготовка отклика отменена.")
            return
        if callback_data.action == "back":
            await _send_preview(callback.message, preview)
            return
        if callback_data.action != "confirm":
            await callback.message.answer("Эта кнопка устарела.")
            return

        try:
            confirmation = await context.hh_application_service.create_confirmation(
                user_id=callback.from_user.id,
                application_id=preview.draft_id,
            )
        except HHApplicationError as exc:
            await callback.message.answer(exc.user_message)
            return
        await callback.message.answer(
            "<b>Подтверждение отправки</b>\n\n"
            f"<b>Вакансия:</b> {escape(preview.vacancy_title)}\n"
            f"<b>Компания:</b> {escape(preview.company)}\n"
            f"<b>Резюме:</b> {escape(preview.resume.title)}\n\n"
            "После финального подтверждения отклик будет отправлен работодателю, "
            "если это поддерживается официальным API. Если API требует ручной "
            "сценарий, откроется HeadHunter.",
            reply_markup=final_confirmation_keyboard(
                confirmation.token, preview.draft_id
            ),
        )

    @router.message(CoverLetterEdit.waiting_for_text)
    async def cover_letter_edit(message: Message, state: FSMContext) -> None:
        if not message.from_user or message.from_user.id != context.settings.telegram_user_id:
            return
        if not message.text:
            await message.answer("Отправь письмо обычным текстовым сообщением.")
            return
        data = await state.get_data()
        application_id = data.get("application_id")
        if not isinstance(application_id, int):
            await state.clear()
            await message.answer("Состояние редактирования устарело.")
            return
        try:
            preview = await context.hh_application_service.update_cover_letter(
                user_id=message.from_user.id,
                application_id=application_id,
                cover_letter=message.text,
            )
        except HHApplicationError as exc:
            await message.answer(exc.user_message)
            return
        await state.clear()
        if preview is None:
            await message.answer("Черновик отклика больше недоступен.")
            return
        await _send_preview(message, preview)

    @router.callback_query(ConfirmationCallback.filter())
    async def final_confirmation_callback(
        callback: CallbackQuery, callback_data: ConfirmationCallback
    ) -> None:
        if not await is_authorized(callback):
            return
        await callback.answer("Отправляю отклик…")
        if not isinstance(callback.message, Message):
            return
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramAPIError:
            logger.debug("Could not disable final confirmation keyboard")
        try:
            result = await context.hh_application_service.submit_application(
                user_id=callback.from_user.id,
                confirmation_token=callback_data.token,
            )
        except Exception:
            logger.exception("Unexpected final HH application error")
            await callback.message.answer(
                "Не удалось подтвердить результат отправки. Проверь отклики в "
                "HeadHunter."
            )
            return
        keyboard = (
            manual_action_keyboard(result.manual_url) if result.manual_url else None
        )
        await callback.message.answer(escape(result.message), reply_markup=keyboard)

    return router
