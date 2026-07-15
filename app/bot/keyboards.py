from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def vacancy_keyboard(
    vacancy_id: int, url: str, status: str = "new"
) -> InlineKeyboardMarkup:
    save_text = "Сохранено ✓" if status == "saved" else "Сохранить"
    applied_text = "Откликнулся ✓" if status == "applied" else "Откликнулся"
    skip_text = "Пропущено ✓" if status == "skipped" else "Пропустить"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Открыть вакансию", url=url)],
            [
                InlineKeyboardButton(text=save_text, callback_data=f"s:{vacancy_id}"),
                InlineKeyboardButton(
                    text="Сопроводительное", callback_data=f"c:{vacancy_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text=applied_text, callback_data=f"a:{vacancy_id}"
                ),
                InlineKeyboardButton(text=skip_text, callback_data=f"k:{vacancy_id}"),
            ],
        ]
    )
