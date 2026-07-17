from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import CallbackQuery, Chat, Message, User
from pydantic import Field

from app.bot.callbacks import build_callbacks_router
from app.bot.hh_applications import CoverLetterEdit, build_hh_applications_router
from app.bot.hh_callback_data import (
    ConfirmationCallback,
    DraftApplicationCallback,
    PrepareApplicationCallback,
)
from app.bot.keyboards import resume_keyboard
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
        self.submit_calls = 0
        self.confirm_error: Exception | None = None
        self.submit_error: Exception | None = None
        self.current_preview = preview()

    async def prepare_application(self, **kwargs: object) -> PreparedApplication:
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
            manual_url="https://hh.ru/vacancy/3",
        )

    async def update_cover_letter(self, **kwargs: object) -> PreparedApplication:
        self.current_preview.cover_letter = str(kwargs["cover_letter"])
        return self.current_preview


def context(service: FakeApplicationService) -> SimpleNamespace:
    return SimpleNamespace(
        settings=SimpleNamespace(
            telegram_user_id=42,
            hh_oauth_configured=True,
            hh_oauth_state_ttl_seconds=600,
        ),
        hh_application_service=service,
        hh_oauth_service=SimpleNamespace(
            create_authorization_url=AsyncMock(return_value="https://hh.ru/oauth/authorize")
        ),
        hh_integration_repository=SimpleNamespace(
            get_owned_resume=AsyncMock(
                return_value=SimpleNamespace(external_id="resume-1")
            )
        ),
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
    assert "Подключи аккаунт" in msg.captured[-1]["text"]
    assert msg.captured[-1]["reply_markup"] is not None


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
    assert "Подтверждение отправки" in msg.captured[-1]["text"]


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
    assert msg.captured[-1]["text"] == HHConfirmationError.user_message


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
    assert "Проверь отклики" in msg.captured[-1]["text"]


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
