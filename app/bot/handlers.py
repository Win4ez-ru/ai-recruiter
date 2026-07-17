from __future__ import annotations

import logging
from html import escape

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.bot.context import BotContext
from app.bot.keyboards import connect_hh_keyboard

logger = logging.getLogger(__name__)

SEARCH_ERROR_MESSAGES = {
    "hh_forbidden": (
        "HeadHunter отклонил запросы поиска (403). Проверьте сетевой маршрут "
        "или обратитесь в поддержку HH с request_id из структурированного лога."
    ),
    "hh_rate_limited": "HeadHunter ограничил частоту запросов. Повторите позже.",
    "hh_unavailable": "HeadHunter временно недоступен. Повторите поиск позже.",
    "openai_rate_limited": "OpenAI ограничил частоту запросов. Повторите позже.",
    "openai_unavailable": "OpenAI временно недоступен. Повторите позже.",
    "openai_configuration": (
        "OpenAI не настроен или выбранная модель недоступна. Проверьте .env."
    ),
}

HELP_TEXT = """<b>Персональный бот поиска iOS-вакансий</b>

/search — найти и проанализировать свежие вакансии
/new — показать еще не отправленные вакансии
/top — лучшие вакансии по рейтингу
/saved — сохраненные вакансии
/applied — вакансии, на которые вы откликнулись
/stats — статистика поиска и откликов
/profile — краткий профиль кандидата
/hh — подключение аккаунта HeadHunter
/help — эта справка"""


def build_handlers_router(context: BotContext) -> Router:
    router = Router(name="commands")

    async def authorized(message: Message) -> bool:
        if message.from_user and message.from_user.id == context.settings.telegram_user_id:
            return True
        await message.answer("Этот бот является приватным.")
        return False

    @router.message(Command("start"))
    async def start_handler(message: Message) -> None:
        if await authorized(message):
            await message.answer(HELP_TEXT)

    @router.message(Command("help"))
    async def help_handler(message: Message) -> None:
        if await authorized(message):
            await message.answer(HELP_TEXT)

    @router.message(Command("search"))
    async def search_handler(message: Message) -> None:
        if not await authorized(message):
            return
        if context.search_lock.locked():
            await message.answer("Поиск уже выполняется. Дождитесь завершения.")
            return
        await message.answer("Начинаю поиск вакансий.")

        async def progress(text: str) -> None:
            await message.answer(escape(text))

        try:
            async with context.search_lock:
                summary = await context.search_service.run(progress=progress)
            for error_code in summary.error_codes:
                await message.answer(SEARCH_ERROR_MESSAGES[error_code])
            vacancies = await context.vacancy_repository.list_digest_candidates(
                context.settings.min_score_to_send,
                context.settings.max_vacancies_per_digest,
                only_unsent=True,
            )
            sent = await context.digest_service.send_vacancies(
                message.bot,
                context.settings.telegram_user_id,
                vacancies,
                mark_sent=True,
            )
            await message.answer(
                f"Подходящих вакансий с оценкой от "
                f"{context.settings.min_score_to_send}: {summary.suitable}. "
                f"Отправлено карточек: {sent}."
            )
        except Exception:
            logger.exception("Manual vacancy search failed")
            await message.answer(
                "Поиск завершился с ошибкой внешнего сервиса. Подробности записаны в лог."
            )

    async def show_collection(
        message: Message, vacancies: list, empty_text: str, *, mark_sent: bool
    ) -> None:
        if not vacancies:
            await message.answer(empty_text)
            return
        await context.digest_service.send_vacancies(
            message.bot,
            context.settings.telegram_user_id,
            vacancies,
            mark_sent=mark_sent,
        )

    @router.message(Command("new"))
    async def new_handler(message: Message) -> None:
        if not await authorized(message):
            return
        vacancies = await context.vacancy_repository.list_digest_candidates(
            context.settings.min_score_to_send,
            context.settings.max_vacancies_per_digest,
            only_unsent=True,
        )
        await show_collection(
            message, vacancies, "Новых подходящих вакансий пока нет.", mark_sent=True
        )

    @router.message(Command("top"))
    async def top_handler(message: Message) -> None:
        if not await authorized(message):
            return
        vacancies = await context.vacancy_repository.list_digest_candidates(
            context.settings.min_score_to_send,
            context.settings.max_vacancies_per_digest,
            only_unsent=False,
        )
        await show_collection(
            message, vacancies, "Проанализированных вакансий пока нет.", mark_sent=False
        )

    @router.message(Command("saved"))
    async def saved_handler(message: Message) -> None:
        if not await authorized(message):
            return
        vacancies = await context.vacancy_repository.list_by_application_status("saved")
        await show_collection(
            message, vacancies, "Сохраненных вакансий пока нет.", mark_sent=False
        )

    @router.message(Command("applied"))
    async def applied_handler(message: Message) -> None:
        if not await authorized(message):
            return
        vacancies = await context.vacancy_repository.list_by_application_status("applied")
        await show_collection(
            message, vacancies, "Отмеченных откликов пока нет.", mark_sent=False
        )

    @router.message(Command("stats"))
    async def stats_handler(message: Message) -> None:
        if not await authorized(message):
            return
        stats = await context.vacancy_repository.stats()
        missing = (
            "\n".join(
                f"• {escape(skill)} — {count}" for skill, count in stats.common_missing_skills
            )
            or "нет данных"
        )
        await message.answer(
            f"<b>Статистика</b>\n\n"
            f"Всего вакансий: {stats.total_vacancies}\n"
            f"Проанализировано: {stats.analyzed}\n"
            f"Сохранено: {stats.saved}\n"
            f"Откликов: {stats.applied}\n"
            f"Интервью: {stats.interviews}\n"
            f"Отказов: {stats.rejected}\n"
            f"Средняя оценка: {stats.average_score:.1f}\n\n"
            f"<b>Чаще всего не хватает:</b>\n{missing}"
        )

    @router.message(Command("profile"))
    async def profile_handler(message: Message) -> None:
        if not await authorized(message):
            return
        profile = context.profile
        await message.answer(
            f"<b>{escape(profile.candidate_name)}</b>\n"
            f"Целевые роли: {escape(', '.join(profile.target_roles))}\n"
            f"Город: {escape(profile.location)}\n"
            f"Сильные навыки: {escape(', '.join(profile.strong_skills))}\n"
            f"Минимальная зарплата: {profile.minimum_salary_rub:,} ₽\n\n"
            f"Изменить профиль: <code>data/candidate_profile.json</code>\n"
            f"Изменить резюме: <code>data/resume.txt</code>"
        )

    @router.message(Command("hh"))
    async def hh_handler(message: Message) -> None:
        if not await authorized(message):
            return
        integration = await context.hh_integration_repository.get_integration(
            context.settings.telegram_user_id
        )
        if integration is not None:
            resumes = await context.hh_integration_repository.list_resumes(
                context.settings.telegram_user_id
            )
            await message.answer(
                "HeadHunter подключен. "
                f"Синхронизировано резюме: {len(resumes)}."
            )
            return
        if not context.settings.hh_oauth_configured:
            await message.answer(
                "OAuth HeadHunter не настроен. Заполни HH_CLIENT_ID, "
                "HH_CLIENT_SECRET и HH_REDIRECT_URI в .env."
            )
            return
        await message.answer(
            "Подключи аккаунт через официальный OAuth HeadHunter.",
            reply_markup=connect_hh_keyboard(),
        )

    @router.message()
    async def private_fallback(message: Message) -> None:
        if await authorized(message):
            await message.answer("Неизвестная команда. Используйте /help.")

    return router
