from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.hh_callback_data import (
    ConfirmationCallback,
    DraftApplicationCallback,
    HHOAuthCallback,
    PrepareApplicationCallback,
    ResumeCallback,
    ScreenCallback,
    VacancyCallback,
)
from app.schemas import HHResumeData
from app.vacancy_status import (
    VacancyStatus,
    allowed_transitions,
    can_mark_applied_manual,
    has_registered_application,
    is_excluded_from_recommendations,
    normalize_status,
)

LIFECYCLE_BUTTON_LABELS = {
    VacancyStatus.VIEWED: "↩️ Вернуть к просмотру",
    VacancyStatus.SAVED: "❤️ Вернуть в избранное",
    VacancyStatus.INTERVIEW: "🤝 Интервью",
    VacancyStatus.TEST_TASK: "🧪 Тестовое",
    VacancyStatus.REJECTED: "⛔ Отказ",
    VacancyStatus.OFFER: "🎉 Оффер",
    VacancyStatus.OFFER_ACCEPTED: "🏁 Оффер принят",
    VacancyStatus.ARCHIVED: "🗄 В архив",
}


def _ui(text: str, action: str, value: int = 0) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text,
        callback_data=ScreenCallback(action=action, value=value).pack(),
    )


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_ui("🔎 Найти вакансии", "search")],
            [_ui("✨ Новые", "new"), _ui("🏆 Лучшие", "top")],
            [_ui("❤️ Избранное", "saved"), _ui("📤 Отклики", "applied")],
            [_ui("📊 Аналитика", "stats"), _ui("👤 Профиль", "profile")],
            [_ui("🔐 HeadHunter", "hh"), _ui("💡 Помощь", "help")],
            [_ui("➖ Свернуть", "close")],
        ]
    )


def back_keyboard(*, search: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if search:
        rows.append([_ui("🔎 Запустить поиск", "search")])
    rows.append([_ui("⬅️ Назад", "back"), _ui("🏠 Меню", "menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def collection_keyboard(
    vacancy_id: int,
    url: str,
    status: str,
    *,
    position: int,
    total: int,
    expanded: bool,
) -> InlineKeyboardMarkup:
    current_status = normalize_status(status)
    saved = current_status == VacancyStatus.SAVED
    excluded = is_excluded_from_recommendations(current_status)
    detail_row = [_ui("💬 Кратко" if expanded else "📄 Полностью", "full", vacancy_id)]
    action_rows: list[list[InlineKeyboardButton]]
    if excluded:
        action_rows = [
            [InlineKeyboardButton(text="🔗 Открыть вакансию", url=url)],
            [_ui("📌 Изменить статус", "lifecycle", vacancy_id)],
            [_ui("🏠 Главное меню", "menu")],
        ]
    else:
        detail_row.append(
            _ui("💔 Убрать" if saved else "❤️ В избранное", "save", vacancy_id)
        )
        action_rows = [
            [
                InlineKeyboardButton(text="🔗 Открыть на HH", url=url),
                InlineKeyboardButton(
                    text="✍️ Через бота",
                    callback_data=PrepareApplicationCallback(
                        vacancy_id=vacancy_id
                    ).pack(),
                ),
            ],
        ]
        if can_mark_applied_manual(current_status):
            action_rows.append(
                [_ui("✅ Я уже откликнулся", "manual_apply", vacancy_id)]
            )
        action_rows.append(
            [_ui("🙈 Скрыть", "skip", vacancy_id), _ui("🏠 Меню", "menu")]
        )
    navigation = (
        [
            [
                _ui("◀ Назад", "prev", vacancy_id),
                _ui(f"{position} / {total}", "noop", vacancy_id),
                _ui("Вперёд ▶", "next", vacancy_id),
            ],
        ]
        if total > 1
        else []
    )
    return InlineKeyboardMarkup(inline_keyboard=[*navigation, detail_row, *action_rows])


def lifecycle_keyboard(vacancy_id: int, status: str) -> InlineKeyboardMarkup:
    current = normalize_status(status)
    targets = [
        target
        for target in allowed_transitions(current)
        if target in LIFECYCLE_BUTTON_LABELS
    ]
    rows = [
        [_ui(LIFECYCLE_BUTTON_LABELS[target], f"status_{target.value}", vacancy_id)]
        for target in sorted(targets, key=lambda item: item.value)
    ]
    rows.append([_ui("⬅️ К вакансии", "back"), _ui("🏠 Меню", "menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def vacancy_keyboard(
    vacancy_id: int, url: str, status: str = "new"
) -> InlineKeyboardMarkup:
    """Compatibility keyboard for old cards still present in user chats."""
    applied = has_registered_application(status)
    rows = [
        [InlineKeyboardButton(text="🔗 Открыть на HH", url=url)],
        [
            InlineKeyboardButton(
                text="❤️ В избранное",
                callback_data=VacancyCallback(
                    action="save", vacancy_id=vacancy_id
                ).pack(),
            ),
            InlineKeyboardButton(
                text="🙈 Скрыть",
                callback_data=VacancyCallback(
                    action="skip", vacancy_id=vacancy_id
                ).pack(),
            ),
        ],
    ]
    if applied:
        rows.append([_ui("✅ Отклик уже зарегистрирован", "noop", vacancy_id)])
    else:
        rows.extend(
            [
                [
                    InlineKeyboardButton(
                        text="✍️ Отклик через бота",
                        callback_data=PrepareApplicationCallback(
                            vacancy_id=vacancy_id
                        ).pack(),
                    )
                ],
                [_ui("✅ Я уже откликнулся", "manual_apply", vacancy_id)],
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def manual_application_confirmation_keyboard(vacancy_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _ui("✅ Да", "manual_apply_yes", vacancy_id),
                _ui("❌ Отмена", "manual_apply_cancel", vacancy_id),
            ]
        ]
    )


def manual_application_registered_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_ui("✅ Отклик уже зарегистрирован", "noop")],
            [_ui("➡️ К следующей вакансии", "manual_apply_continue")],
            [_ui("🏠 Главное меню", "menu")],
        ]
    )


def hidden_vacancy_keyboard(vacancy_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_ui("↩️ Отменить скрытие", "hide_undo", vacancy_id)],
            [_ui("Следующая вакансия ▶", "hide_continue", vacancy_id)],
            [_ui("🏠 Главное меню", "menu")],
        ]
    )


def connect_hh_keyboard(url: str | None = None) -> InlineKeyboardMarkup:
    connect = (
        InlineKeyboardButton(text="Продолжить на HeadHunter ↗", url=url)
        if url
        else InlineKeyboardButton(
            text="🔐 Подключить HeadHunter",
            callback_data=HHOAuthCallback(action="connect").pack(),
        )
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [connect],
            [_ui("⬅️ Назад", "back"), _ui("🏠 Меню", "menu")],
        ]
    )


def hh_connected_keyboard(pending_vacancy_id: int | None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if pending_vacancy_id is not None:
        rows.append(
            [
                InlineKeyboardButton(
                    text="✍️ Продолжить отклик",
                    callback_data=PrepareApplicationCallback(
                        vacancy_id=pending_vacancy_id
                    ).pack(),
                )
            ]
        )
        rows.append([_ui("Не продолжать", "pending_clear")])
    rows.append([_ui("🏠 Главное меню", "menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def resume_keyboard(
    vacancy_id: int, resumes: list[HHResumeData]
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=("✓ " if resume.is_default else "📄 ") + resume.title[:42],
                callback_data=ResumeCallback(
                    vacancy_id=vacancy_id, resume_id=resume.local_id
                ).pack(),
            )
        ]
        for resume in resumes
        if resume.local_id is not None
    ]
    rows.append([_ui("⬅️ К вакансии", "back", vacancy_id), _ui("🏠 Меню", "menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def application_preview_keyboard(
    application_id: int,
    *,
    multiple_resumes: bool,
    manual_submission_required: bool = False,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=(
                    "↗️ Перейти к ручному шагу"
                    if manual_submission_required
                    else "📤 Продолжить"
                ),
                callback_data=DraftApplicationCallback(
                    action="confirm", application_id=application_id
                ).pack(),
            ),
            InlineKeyboardButton(
                text="✏️ Изменить",
                callback_data=DraftApplicationCallback(
                    action="edit", application_id=application_id
                ).pack(),
            ),
        ]
    ]
    if multiple_resumes:
        rows.append(
            [
                InlineKeyboardButton(
                    text="📄 Сменить резюме",
                    callback_data=DraftApplicationCallback(
                        action="resume", application_id=application_id
                    ).pack(),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ К вакансии",
                callback_data=DraftApplicationCallback(
                    action="cancel", application_id=application_id
                ).pack(),
            ),
            _ui("🏠 Меню", "menu"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def application_edit_keyboard(application_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Отмена",
                    callback_data=DraftApplicationCallback(
                        action="back", application_id=application_id
                    ).pack(),
                ),
                _ui("🏠 Меню", "menu"),
            ]
        ]
    )


def final_confirmation_keyboard(
    token: str,
    application_id: int,
    *,
    manual_submission_required: bool = False,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=(
                        "↗️ Показать ручной шаг"
                        if manual_submission_required
                        else "🚀 Да, отправить"
                    ),
                    callback_data=ConfirmationCallback(token=token).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=DraftApplicationCallback(
                        action="back", application_id=application_id
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=DraftApplicationCallback(
                        action="cancel", application_id=application_id
                    ).pack(),
                ),
            ],
        ]
    )


def application_result_keyboard(url: str | None = None) -> InlineKeyboardMarkup:
    rows = []
    if url:
        rows.append([InlineKeyboardButton(text="Открыть на HeadHunter ↗", url=url)])
    rows.append([_ui("⬅️ К вакансиям", "back"), _ui("🏠 Меню", "menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def manual_action_keyboard(url: str) -> InlineKeyboardMarkup:
    return application_result_keyboard(url)
