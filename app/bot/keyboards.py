from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.hh_callback_data import (
    ConfirmationCallback,
    DraftApplicationCallback,
    HHOAuthCallback,
    PrepareApplicationCallback,
    ResumeCallback,
    VacancyCallback,
)
from app.schemas import HHResumeData


def vacancy_keyboard(
    vacancy_id: int, url: str, status: str = "new"
) -> InlineKeyboardMarkup:
    save_text = "Сохранено ✓" if status == "saved" else "Сохранить"
    skip_text = "Пропущено ✓" if status == "skipped" else "Пропустить"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Открыть вакансию", url=url)],
            [
                InlineKeyboardButton(
                    text=save_text,
                    callback_data=VacancyCallback(
                        action="save", vacancy_id=vacancy_id
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text=skip_text,
                    callback_data=VacancyCallback(
                        action="skip", vacancy_id=vacancy_id
                    ).pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=(
                        "Отклик отправлен ✓"
                        if status == "applied"
                        else "📝 Подготовить отклик"
                    ),
                    callback_data=PrepareApplicationCallback(
                        vacancy_id=vacancy_id
                    ).pack(),
                )
            ],
        ]
    )


def connect_hh_keyboard(url: str | None = None) -> InlineKeyboardMarkup:
    button = (
        InlineKeyboardButton(text="Подключить HeadHunter", url=url)
        if url
        else InlineKeyboardButton(
            text="Подключить HeadHunter",
            callback_data=HHOAuthCallback(action="connect").pack(),
        )
    )
    return InlineKeyboardMarkup(inline_keyboard=[[button]])


def resume_keyboard(
    vacancy_id: int, resumes: list[HHResumeData]
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=("✓ " if resume.is_default else "") + resume.title[:45],
                callback_data=ResumeCallback(
                    vacancy_id=vacancy_id, resume_id=resume.local_id
                ).pack(),
            )
        ]
        for resume in resumes
        if resume.local_id is not None
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data=VacancyCallback(
                    action="cancel", vacancy_id=vacancy_id
                ).pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def application_preview_keyboard(
    application_id: int, *, multiple_resumes: bool
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="✅ Отправить отклик",
                callback_data=DraftApplicationCallback(
                    action="confirm", application_id=application_id
                ).pack(),
            )
        ],
        [
            InlineKeyboardButton(
                text="✏️ Изменить письмо",
                callback_data=DraftApplicationCallback(
                    action="edit", application_id=application_id
                ).pack(),
            )
        ],
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
                text="❌ Отмена",
                callback_data=DraftApplicationCallback(
                    action="cancel", application_id=application_id
                ).pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def final_confirmation_keyboard(
    token: str, application_id: int
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚀 Да, отправить",
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
                    text="❌ Отмена",
                    callback_data=DraftApplicationCallback(
                        action="cancel", application_id=application_id
                    ).pack(),
                ),
            ],
        ]
    )


def manual_action_keyboard(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Открыть на HeadHunter", url=url)]]
    )
