from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import EditMessageText

from app.bot.hh_callback_data import (
    DraftApplicationCallback,
    HHOAuthCallback,
    ScreenCallback,
)
from app.bot.keyboards import (
    application_result_keyboard,
    collection_keyboard,
    lifecycle_keyboard,
    main_menu_keyboard,
    manual_application_confirmation_keyboard,
)
from app.bot.ui import UIManager
from app.bot.views import collection_for_kind
from app.services.digest import DigestService


def test_ui_collection_navigation_wraps_and_resets_expanded_view() -> None:
    ui = UIManager()
    session = ui.set_collection(42, [10, 20, 30], title="Новые")

    assert session.current_vacancy_id == 10
    ui.move(42, -1)
    assert session.current_vacancy_id == 30
    ui.toggle_expanded(42)
    assert session.expanded is True
    ui.move(42, 1)
    assert session.current_vacancy_id == 10
    assert session.expanded is False


def test_removing_current_card_keeps_collection_on_nearest_item() -> None:
    ui = UIManager()
    session = ui.set_collection(42, [10, 20, 30], title="Новые")
    ui.move(42, 1)

    ui.remove_from_collection(42, 20)

    assert session.collection_ids == [10, 30]
    assert session.current_vacancy_id == 30


def test_cancelled_ui_operation_cannot_overwrite_newer_screen() -> None:
    ui = UIManager()
    generation = ui.start_operation(42)

    ui.cancel_operation(42)

    assert ui.operation_is_current(42, generation) is False


def test_ui_can_focus_oauth_vacancy_without_removing_existing_cards() -> None:
    ui = UIManager()
    session = ui.set_collection(42, [10, 20, 30], title="Лучшие", kind="top")

    ui.focus_vacancy(42, 30)

    assert session.collection_ids == [10, 20, 30]
    assert session.current_vacancy_id == 30


def test_product_keyboards_stay_compact_and_callback_data_fits_telegram() -> None:
    keyboard = collection_keyboard(
        123456,
        "https://hh.ru/vacancy/123456",
        "new",
        position=3,
        total=18,
        expanded=False,
    )

    assert len(keyboard.inline_keyboard) <= 5
    callback_values = [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    ]
    assert callback_values
    assert all(len(value.encode("utf-8")) <= 64 for value in callback_values)
    assert any(
        button.text == "✅ Я уже откликнулся"
        for row in keyboard.inline_keyboard
        for button in row
    )


def test_applied_card_replaces_application_actions_with_status() -> None:
    keyboard = collection_keyboard(
        123456,
        "https://hh.ru/vacancy/123456",
        "applied_manual",
        position=1,
        total=1,
        expanded=False,
    )
    labels = [button.text for row in keyboard.inline_keyboard for button in row]

    assert "📌 Изменить статус" in labels
    assert "✅ Я уже откликнулся" not in labels
    assert "✍️ Подготовить отклик" not in labels


def test_archived_lifecycle_offers_restore_action() -> None:
    keyboard = lifecycle_keyboard(123456, "archived")
    labels = [button.text for row in keyboard.inline_keyboard for button in row]

    assert "↩️ Вернуть к просмотру" in labels


@pytest.mark.parametrize("status", ["rejected", "archived", "hidden"])
def test_terminal_card_never_offers_invalid_application_actions(status: str) -> None:
    keyboard = collection_keyboard(
        123456,
        "https://hh.ru/vacancy/123456",
        status,
        position=1,
        total=1,
        expanded=False,
    )
    labels = [button.text for row in keyboard.inline_keyboard for button in row]

    assert "📌 Изменить статус" in labels
    assert "❤️ В избранное" not in labels
    assert "✍️ Подготовить отклик" not in labels
    assert "🙈 Скрыть" not in labels


def test_manual_application_confirmation_is_compact_and_explicit() -> None:
    keyboard = manual_application_confirmation_keyboard(123456)
    labels = [button.text for row in keyboard.inline_keyboard for button in row]

    assert labels == ["✅ Да, отметить", "⬅️ Не отмечать"]
    assert all(
        button.callback_data is not None
        and len(button.callback_data.encode("utf-8")) <= 64
        for row in keyboard.inline_keyboard
        for button in row
    )


def test_main_menu_exposes_core_sections_without_command_lists() -> None:
    keyboard = main_menu_keyboard()
    actions = {
        ScreenCallback.unpack(button.callback_data).action
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    }

    assert {
        "search",
        "new",
        "top",
        "saved",
        "applied",
        "stats",
        "profile",
        "hh",
    } <= actions


@pytest.mark.asyncio
async def test_top_collection_has_an_explicit_five_item_product_limit() -> None:
    repository = SimpleNamespace(
        list_digest_candidates=AsyncMock(return_value=[]),
    )
    callback_context = SimpleNamespace(
        settings=SimpleNamespace(min_score_to_send=65),
        vacancy_repository=repository,
    )

    await collection_for_kind(callback_context, "top")  # type: ignore[arg-type]

    repository.list_digest_candidates.assert_awaited_once_with(
        65,
        5,
        only_unsent=False,
    )


def test_retryable_application_error_offers_only_safe_recovery_actions() -> None:
    keyboard = application_result_keyboard(
        "https://hh.ru/vacancy/123",
        application_id=7,
        vacancy_id=123,
        can_retry=True,
        can_mark_applied=True,
    )
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    callbacks = [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    ]

    assert "🔄 Повторить отправку" in labels
    assert "🌐 Открыть вакансию на HH" in labels
    assert "✅ Я откликнулся на HH" in labels
    assert any(
        value is not None and DraftApplicationCallback.unpack(value).action == "retry"
        for value in callbacks
        if value is not None and value.startswith("hhd:")
    )


def test_oauth_error_offers_reconnect_without_automatic_retry() -> None:
    keyboard = application_result_keyboard(
        application_id=7,
        vacancy_id=123,
        requires_oauth=True,
    )
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    callbacks = [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    ]

    assert "🔐 Переподключить HeadHunter" in labels
    assert "🔄 Повторить отправку" not in labels
    assert any(
        value is not None and HHOAuthCallback.unpack(value).action == "connect"
        for value in callbacks
        if value is not None and value.startswith("hho:")
    )


@pytest.mark.asyncio
async def test_repeated_screens_edit_one_message_instead_of_sending_new_ones() -> None:
    class FakeBot:
        def __init__(self) -> None:
            self.sent = 0
            self.edited = 0

        async def send_message(self, **kwargs: object) -> SimpleNamespace:
            self.sent += 1
            return SimpleNamespace(message_id=700)

        async def edit_message_text(self, **kwargs: object) -> SimpleNamespace:
            self.edited += 1
            return SimpleNamespace(message_id=700)

        async def delete_message(self, **kwargs: object) -> None:
            return None

    bot = FakeBot()
    ui = UIManager()

    await ui.render_chat(bot, 42, "Первый экран")  # type: ignore[arg-type]
    await ui.render_chat(bot, 42, "Второй экран")  # type: ignore[arg-type]

    assert bot.sent == 1
    assert bot.edited == 1


@pytest.mark.asyncio
async def test_explicit_screen_overrides_persisted_screen_after_restart() -> None:
    class FakeRepository:
        get = AsyncMock(
            return_value=SimpleNamespace(
                message_id=700,
                screen="application_loading",
                collection_ids=[10],
                collection_title="Вакансии",
                collection_kind="top",
                collection_index=0,
                expanded=False,
                pending_vacancy_id=None,
            )
        )
        save = AsyncMock()

    class FakeBot:
        async def edit_message_text(self, **kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(message_id=700)

    ui = UIManager(FakeRepository())  # type: ignore[arg-type]

    await ui.render_chat(
        FakeBot(),  # type: ignore[arg-type]
        42,
        "Главное меню",
        screen="menu",
    )

    assert ui.session(42).screen == "menu"


@pytest.mark.asyncio
async def test_transient_edit_error_preserves_existing_ui_message() -> None:
    class FakeBot:
        def __init__(self) -> None:
            self.sent = 0
            self.deleted = 0

        async def edit_message_text(self, **kwargs: object) -> SimpleNamespace:
            raise TelegramNetworkError(
                EditMessageText(chat_id=42, message_id=700, text="Экран"),
                "temporary network failure",
            )

        async def send_message(self, **kwargs: object) -> SimpleNamespace:
            self.sent += 1
            return SimpleNamespace(message_id=701)

        async def delete_message(self, **kwargs: object) -> None:
            self.deleted += 1

    bot = FakeBot()
    ui = UIManager()
    ui.session(42).message_id = 700

    rendered = await ui.render_chat(bot, 42, "Новый экран")  # type: ignore[arg-type]

    assert rendered is None
    assert bot.deleted == 0
    assert bot.sent == 0
    assert ui.session(42).message_id == 700


@pytest.mark.asyncio
async def test_digest_marks_only_the_card_that_was_actually_rendered() -> None:
    class FakeRepository:
        def __init__(self) -> None:
            self.marked: list[int] = []

        async def mark_sent(self, vacancy_id: int) -> None:
            self.marked.append(vacancy_id)

    class FakeUI:
        def __init__(self) -> None:
            self.session = SimpleNamespace(screen="menu")
            self.collection_ids: list[int] = []
            self.cancelled = 0

        async def restore(self, chat_id: int) -> SimpleNamespace:
            return self.session

        def set_collection(
            self, chat_id: int, vacancy_ids: list[int], **kwargs: object
        ) -> SimpleNamespace:
            self.collection_ids = vacancy_ids
            return SimpleNamespace(collection_title=str(kwargs["title"]))

        def cancel_operation(self, chat_id: int) -> None:
            self.cancelled += 1

        async def render_chat(self, *args: object, **kwargs: object) -> object:
            return SimpleNamespace(message_id=1)

    def item(vacancy_id: int) -> SimpleNamespace:
        analysis = SimpleNamespace(
            match_score=80,
            decision="apply",
            role_level="junior",
            matched_skills=["Swift"],
            missing_skills=[],
            blocking_requirements=[],
            advantages=[],
            risks=[],
            resume_focus=[],
            reason="Подходит",
        )
        return SimpleNamespace(
            id=vacancy_id,
            title="Junior iOS Developer",
            company="Acme",
            url=f"https://hh.ru/vacancy/{vacancy_id}",
            description="Swift",
            requirements="",
            responsibilities="",
            key_skills=["Swift"],
            salary_from=None,
            salary_to=None,
            salary_currency=None,
            salary_gross=None,
            location="Санкт-Петербург",
            work_format="Удалённо",
            experience="1–3 года",
            employment="Полная занятость",
            published_at=None,
            analysis=analysis,
            application=None,
        )

    repository = FakeRepository()
    ui = FakeUI()
    service = DigestService(repository, ui)  # type: ignore[arg-type]

    rendered = await service.send_vacancies(
        SimpleNamespace(),  # type: ignore[arg-type]
        42,
        [item(1), item(2)],  # type: ignore[list-item]
        mark_sent=True,
    )

    assert rendered == 1
    assert ui.collection_ids == [1, 2]
    assert ui.cancelled == 1
    assert repository.marked == [1]

    ui.session.screen = "collection"
    deferred = await service.send_vacancies(
        SimpleNamespace(),  # type: ignore[arg-type]
        42,
        [item(2)],  # type: ignore[list-item]
        mark_sent=True,
    )

    assert deferred == 0
    assert repository.marked == [1]
