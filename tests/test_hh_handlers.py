from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import CallbackQuery, Chat, Message, User
from pydantic import Field

from app.bot.callbacks import build_callbacks_router
from app.bot.handlers import build_handlers_router
from app.bot.hh_applications import CoverLetterEdit, build_hh_applications_router
from app.bot.hh_callback_data import (
    ConfirmationCallback,
    DraftApplicationCallback,
    PrepareApplicationCallback,
    ResumeCallback,
    ScreenCallback,
)
from app.bot.keyboards import final_confirmation_keyboard, resume_keyboard
from app.bot.screens import application_preview_text, confirmation_text
from app.schemas import ApplicationResult, HHResumeData, PreparedApplication
from app.services.hh_application import (
    ConfirmationPreview,
    HHConfirmationError,
    HHNotAuthorizedError,
)


class CapturingMessage(Message):
    captured: list[dict] = Field(default_factory=list, exclude=True)

    async def answer(self, text: str, **kwargs: object) -> Message:
        self.captured.append({"text": text, **kwargs})
        return self

    async def edit_reply_markup(self, **kwargs: object) -> Message:
        self.captured.append({"edit_reply_markup": kwargs})
        return self


class CapturingCallback(CallbackQuery):
    captured_answers: list[dict] = Field(default_factory=list, exclude=True)

    async def answer(self, text: str | None = None, **kwargs: object) -> bool:
        self.captured_answers.append({"text": text, **kwargs})
        return True


def message(text: str = "callback") -> CapturingMessage:
    return CapturingMessage(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=42, type="private"),
        from_user=User(id=42, is_bot=False, first_name="User"),
        text=text,
    )


def callback(msg: CapturingMessage) -> CapturingCallback:
    return CapturingCallback(
        id="callback-id",
        from_user=User(id=42, is_bot=False, first_name="User"),
        chat_instance="chat-instance",
        message=msg,
        data="test",
    )


def preview() -> PreparedApplication:
    resume = HHResumeData(external_id="resume-1", title="iOS Developer")
    return PreparedApplication(
        draft_id=7,
        vacancy_id=3,
        vacancy_title="iOS Developer",
        company="Acme",
        vacancy_url="https://hh.ru/vacancy/3",
        resume=resume,
        resumes=[resume],
        cover_letter="Моё сопроводительное письмо.",
    )


class FakeApplicationService:
    def __init__(self) -> None:
        self.prepare_error: Exception | None = None
        self.prepare_calls = 0
        self.submit_calls = 0
        self.confirm_error: Exception | None = None
        self.submit_error: Exception | None = None
        self.current_preview = preview()

    async def prepare_application(self, **kwargs: object) -> PreparedApplication:
        self.prepare_calls += 1
        if self.prepare_error:
            raise self.prepare_error
        return self.current_preview

    async def get_preview(self, **kwargs: object) -> PreparedApplication:
        return self.current_preview

    async def create_confirmation(self, **kwargs: object) -> ConfirmationPreview:
        if self.confirm_error:
            raise self.confirm_error
        return ConfirmationPreview(
            token="one-time-token",
            application=SimpleNamespace(),  # type: ignore[arg-type]
        )

    async def submit_application(self, **kwargs: object) -> ApplicationResult:
        self.submit_calls += 1
        if self.submit_error:
            raise self.submit_error
        return ApplicationResult(
            status="manual_action_required",
            message="Заверши отклик на HeadHunter.",
            vacancy_id=3,
            manual_url="https://hh.ru/vacancy/3",
        )

    async def update_cover_letter(self, **kwargs: object) -> PreparedApplication:
        self.current_preview.cover_letter = str(kwargs["cover_letter"])
        return self.current_preview


class FakeUI:
    def __init__(self) -> None:
        self.current_session = SimpleNamespace(
            collection_ids=[],
            current_vacancy_id=None,
            screen="menu",
            collection_kind="custom",
            pending_vacancy_id=None,
            operation_generation=0,
        )

    def session(self, chat_id: int) -> SimpleNamespace:
        return self.current_session

    async def restore(self, chat_id: int) -> SimpleNamespace:
        return self.current_session

    def start_operation(self, chat_id: int) -> int:
        self.current_session.operation_generation += 1
        return self.current_session.operation_generation

    def cancel_operation(self, chat_id: int) -> None:
        self.current_session.operation_generation += 1

    def set_screen(self, chat_id: int, screen: str) -> SimpleNamespace:
        self.current_session.screen = screen
        return self.current_session

    def operation_is_current(self, chat_id: int, generation: int) -> bool:
        return self.current_session.operation_generation == generation

    def set_pending_vacancy(self, chat_id: int, vacancy_id: int | None) -> None:
        self.current_session.pending_vacancy_id = vacancy_id

    def focus_vacancy(self, chat_id: int, vacancy_id: int) -> SimpleNamespace:
        if vacancy_id not in self.current_session.collection_ids:
            return self.set_collection(chat_id, [vacancy_id], title="Вакансия")
        self.current_session.current_vacancy_id = vacancy_id
        return self.current_session

    def set_collection(
        self, chat_id: int, vacancy_ids: list[int], **kwargs: object
    ) -> SimpleNamespace:
        self.current_session.collection_ids = vacancy_ids
        self.current_session.collection_kind = str(kwargs.get("kind", "custom"))
        self.current_session.current_vacancy_id = (
            vacancy_ids[0] if vacancy_ids else None
        )
        return self.current_session

    def remove_from_collection(self, chat_id: int, vacancy_id: int) -> SimpleNamespace:
        self.current_session.collection_ids = [
            item for item in self.current_session.collection_ids if item != vacancy_id
        ]
        self.current_session.current_vacancy_id = (
            self.current_session.collection_ids[0]
            if self.current_session.collection_ids
            else None
        )
        return self.current_session

    async def render(
        self,
        target: Message | CallbackQuery,
        text: str,
        reply_markup: object = None,
        *,
        screen: str | None = None,
    ) -> None:
        target_message = target.message if isinstance(target, CallbackQuery) else target
        assert isinstance(target_message, CapturingMessage)
        target_message.captured.append(
            {"text": text, "reply_markup": reply_markup, "screen": screen}
        )
        if screen is not None:
            self.current_session.screen = screen


def context(service: FakeApplicationService) -> SimpleNamespace:
    return SimpleNamespace(
        settings=SimpleNamespace(
            telegram_user_id=42,
            hh_oauth_configured=True,
            hh_oauth_state_ttl_seconds=600,
        ),
        hh_application_service=service,
        hh_oauth_service=SimpleNamespace(
            create_authorization_url=AsyncMock(
                return_value="https://hh.ru/oauth/authorize"
            )
        ),
        hh_integration_repository=SimpleNamespace(
            get_owned_resume=AsyncMock(
                return_value=SimpleNamespace(external_id="resume-1")
            )
        ),
        ui=FakeUI(),
    )


def handler(router: object, observer: str, name: str):
    handlers = getattr(router, observer).handlers
    return next(item.callback for item in handlers if item.callback.__name__ == name)


@pytest.mark.asyncio
async def test_prepare_handler_offers_oauth_and_answers_callback() -> None:
    service = FakeApplicationService()
    service.prepare_error = HHNotAuthorizedError()
    router = build_hh_applications_router(context(service))  # type: ignore[arg-type]
    target = handler(router, "callback_query", "prepare_callback")
    msg = message()
    cb = callback(msg)

    await target(cb, PrepareApplicationCallback(vacancy_id=3))

    assert cb.captured_answers
    assert "Подключение HeadHunter" in msg.captured[-1]["text"]
    assert msg.captured[-1]["reply_markup"] is not None


@pytest.mark.asyncio
async def test_prepare_handler_focuses_vacancy_from_oauth_intent() -> None:
    service = FakeApplicationService()
    callback_context = context(service)
    callback_context.ui.set_collection(42, [4, 3], title="Лучшие")
    router = build_hh_applications_router(callback_context)  # type: ignore[arg-type]
    target = handler(router, "callback_query", "prepare_callback")

    await target(callback(message()), PrepareApplicationCallback(vacancy_id=3))

    assert callback_context.ui.current_session.current_vacancy_id == 3


@pytest.mark.asyncio
async def test_first_confirmation_does_not_call_submit_service() -> None:
    service = FakeApplicationService()
    router = build_hh_applications_router(context(service))  # type: ignore[arg-type]
    target = handler(router, "callback_query", "draft_callback")
    msg = message()
    cb = callback(msg)
    state = AsyncMock()

    await target(
        cb,
        DraftApplicationCallback(action="confirm", application_id=7),
        state,
    )

    assert cb.captured_answers
    assert service.submit_calls == 0
    assert "Подтвердить отправку" in msg.captured[-1]["text"]


@pytest.mark.asyncio
async def test_final_confirmation_calls_submit_and_answers_callback() -> None:
    service = FakeApplicationService()
    router = build_hh_applications_router(context(service))  # type: ignore[arg-type]
    target = handler(router, "callback_query", "final_confirmation_callback")
    msg = message()
    cb = callback(msg)

    await target(cb, ConfirmationCallback(token="one-time-token"))

    assert cb.captured_answers[0]["text"] == "Отправляю отклик…"
    assert service.submit_calls == 1
    assert "Заверши отклик" in msg.captured[-1]["text"]


@pytest.mark.asyncio
async def test_submitted_result_removes_actual_vacancy_not_current_card() -> None:
    service = FakeApplicationService()

    async def submitted(**kwargs: object) -> ApplicationResult:
        return ApplicationResult(
            status="submitted",
            message="Отклик отправлен.",
            vacancy_id=3,
        )

    service.submit_application = submitted  # type: ignore[method-assign]
    callback_context = context(service)
    callback_context.ui.set_collection(42, [3, 4], title="Лучшие")
    callback_context.ui.current_session.current_vacancy_id = 4
    router = build_hh_applications_router(callback_context)  # type: ignore[arg-type]
    target = handler(router, "callback_query", "final_confirmation_callback")

    await target(callback(message()), ConfirmationCallback(token="one-time-token"))

    assert callback_context.ui.current_session.collection_ids == [4]


@pytest.mark.asyncio
async def test_confirmation_error_is_shown_safely() -> None:
    service = FakeApplicationService()
    service.confirm_error = HHConfirmationError("expired")
    router = build_hh_applications_router(context(service))  # type: ignore[arg-type]
    target = handler(router, "callback_query", "draft_callback")
    msg = message()
    cb = callback(msg)

    await target(
        cb,
        DraftApplicationCallback(action="confirm", application_id=7),
        AsyncMock(),
    )

    assert cb.captured_answers
    assert service.submit_calls == 0
    assert HHConfirmationError.user_message in msg.captured[-1]["text"]


@pytest.mark.asyncio
async def test_unexpected_submit_error_is_not_exposed() -> None:
    service = FakeApplicationService()
    service.submit_error = RuntimeError("secret raw payload")
    router = build_hh_applications_router(context(service))  # type: ignore[arg-type]
    target = handler(router, "callback_query", "final_confirmation_callback")
    msg = message()
    cb = callback(msg)

    await target(cb, ConfirmationCallback(token="one-time-token"))

    assert cb.captured_answers
    assert "secret raw payload" not in msg.captured[-1]["text"]
    assert "Проверьте отклики" in msg.captured[-1]["text"]


@pytest.mark.asyncio
async def test_cover_letter_edit_updates_draft_and_clears_fsm() -> None:
    service = FakeApplicationService()
    router = build_hh_applications_router(context(service))  # type: ignore[arg-type]
    target = handler(router, "message", "cover_letter_edit")
    msg = message("Новый текст письма")
    state = AsyncMock()
    state.get_data.return_value = {"application_id": 7}

    await target(msg, state)

    state.clear.assert_awaited_once()
    assert service.current_preview.cover_letter == "Новый текст письма"
    assert "Новый текст письма" in msg.captured[-1]["text"]


def test_edit_state_is_registered() -> None:
    assert CoverLetterEdit.waiting_for_text.state.endswith("waiting_for_text")


def test_resume_callback_uses_short_local_identifier() -> None:
    keyboard = resume_keyboard(
        123456,
        [
            HHResumeData(
                local_id=987654,
                external_id="x" * 128,
                title="iOS Developer",
            )
        ],
    )
    callback_data = keyboard.inline_keyboard[0][0].callback_data

    assert callback_data is not None
    assert len(callback_data.encode("utf-8")) <= 64
    assert "x" * 20 not in callback_data


def test_manual_resume_fallback_is_disclosed_before_confirmation() -> None:
    application = preview()
    application.manual_submission_required = True
    keyboard = final_confirmation_keyboard(
        "one-time-token",
        application.draft_id,
        manual_submission_required=True,
    )
    labels = [button.text for row in keyboard.inline_keyboard for button in row]

    assert "Автоматической отправки не будет" in confirmation_text(application)
    assert "резюме нужно будет выбрать" in application_preview_text(application)
    assert "↗️ Показать ручной шаг" in labels


@pytest.mark.asyncio
async def test_resume_selection_does_not_overwrite_screen_after_back() -> None:
    service = FakeApplicationService()
    callback_context = context(service)

    async def cancel_during_lookup(**kwargs: object) -> SimpleNamespace:
        callback_context.ui.cancel_operation(42)
        return SimpleNamespace(external_id="resume-1")

    callback_context.hh_integration_repository.get_owned_resume = cancel_during_lookup
    router = build_hh_applications_router(callback_context)  # type: ignore[arg-type]
    target = handler(router, "callback_query", "resume_callback")
    msg = message()

    await target(
        callback(msg),
        ResumeCallback(vacancy_id=3, resume_id=7),
    )

    assert msg.captured == []
    assert service.prepare_calls == 0


@pytest.mark.asyncio
async def test_invalid_callback_always_gets_answer() -> None:
    router = build_callbacks_router(
        SimpleNamespace(settings=SimpleNamespace(telegram_user_id=42))  # type: ignore[arg-type]
    )
    target = handler(router, "callback_query", "invalid_callback")
    msg = message()
    cb = CapturingCallback(
        id="callback-id",
        from_user=User(id=99, is_bot=False, first_name="Other"),
        chat_instance="chat-instance",
        message=msg,
        data="unknown",
    )

    await target(cb)

    assert cb.captured_answers[0]["text"] == "Этот бот является приватным."


@pytest.mark.asyncio
async def test_command_cancels_in_flight_application_operation() -> None:
    ui = FakeUI()
    generation = ui.start_operation(42)
    command_context = SimpleNamespace(
        settings=SimpleNamespace(
            telegram_user_id=42,
            min_score_to_send=65,
            ai_provider="ollama",
            demo_mode=False,
        ),
        ui=ui,
        profile=SimpleNamespace(candidate_name="Demo Candidate"),
        hh_integration_repository=SimpleNamespace(
            get_integration=AsyncMock(return_value=None)
        ),
        vacancy_repository=SimpleNamespace(
            count_new_candidates=AsyncMock(return_value=0)
        ),
    )
    router = build_handlers_router(command_context)  # type: ignore[arg-type]
    target = handler(router, "message", "start_handler")

    await target(message("/start"), AsyncMock())

    assert ui.operation_is_current(42, generation) is False
    assert ui.current_session.screen == "menu"


@pytest.mark.asyncio
async def test_manual_application_confirmation_updates_same_ui_and_lifecycle() -> None:
    ui = FakeUI()
    ui.set_collection(42, [3], title="Вакансии")
    vacancy = SimpleNamespace(
        id=3,
        title="iOS Developer",
        company="Acme",
        application=None,
    )
    lifecycle_repository = SimpleNamespace(mark_applied_manual=AsyncMock())
    callback_context = SimpleNamespace(
        settings=SimpleNamespace(telegram_user_id=42, min_score_to_send=65),
        ui=ui,
        vacancy_repository=SimpleNamespace(get_by_id=AsyncMock(return_value=vacancy)),
        application_repository=lifecycle_repository,
    )
    router = build_callbacks_router(callback_context)  # type: ignore[arg-type]
    target = handler(router, "callback_query", "screen_callback")
    msg = message()

    prompt_callback = callback(msg)
    await target(
        prompt_callback,
        ScreenCallback(action="manual_apply", value=3),
        AsyncMock(),
    )

    assert prompt_callback.captured_answers
    assert "Вы действительно уже отправили отклик" in msg.captured[-1]["text"]
    assert msg.captured[-1]["screen"] == "manual_apply_confirmation"
    lifecycle_repository.mark_applied_manual.assert_not_awaited()

    confirm_callback = callback(msg)
    await target(
        confirm_callback,
        ScreenCallback(action="manual_apply_yes", value=3),
        AsyncMock(),
    )

    lifecycle_repository.mark_applied_manual.assert_awaited_once_with(3)
    assert ui.current_session.collection_ids == []
    assert "Отклик зарегистрирован" in msg.captured[-1]["text"]
    assert msg.captured[-1]["screen"] == "manual_apply_result"
    assert confirm_callback.captured_answers[0]["text"] == "Отклик зарегистрирован ✅"
