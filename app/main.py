from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
from openai import AsyncOpenAI
from pydantic import ValidationError

from app.bot.callbacks import build_callbacks_router
from app.bot.context import BotContext
from app.bot.handlers import build_handlers_router
from app.config import Settings, get_settings
from app.database import Database
from app.logging_config import configure_logging
from app.repositories.application_repository import ApplicationRepository
from app.repositories.vacancy_repository import VacancyRepository
from app.scheduler.jobs import create_scheduler
from app.services.candidate_profile import (
    CandidateProfileError,
    load_candidate_profile,
    load_resume,
)
from app.services.cover_letter import CoverLetterService
from app.services.digest import DigestService
from app.services.vacancy_filter import VacancyFilter
from app.services.vacancy_ranker import VacancyRanker
from app.services.vacancy_search import VacancySearchService
from app.sources.hh import HHClient

logger = logging.getLogger(__name__)

BOT_COMMANDS = [
    BotCommand(command="start", description="Запуск и список команд"),
    BotCommand(command="search", description="Запустить поиск"),
    BotCommand(command="new", description="Новые вакансии"),
    BotCommand(command="top", description="Лучшие вакансии"),
    BotCommand(command="saved", description="Сохраненные"),
    BotCommand(command="applied", description="Мои отклики"),
    BotCommand(command="stats", description="Статистика"),
    BotCommand(command="profile", description="Профиль"),
    BotCommand(command="help", description="Справка"),
]


async def async_main(settings: Settings) -> None:
    profile = load_candidate_profile(settings.candidate_profile_path)
    resume = load_resume(settings.resume_path)
    database = Database(settings.database_url)
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    openai_client = AsyncOpenAI(api_key=settings.openai_api_key, max_retries=2)
    scheduler = None
    hh_client = HHClient(settings.hh_user_agent)

    try:
        await database.create_tables()
        vacancy_repository = VacancyRepository(database)
        application_repository = ApplicationRepository(database)
        ranker = VacancyRanker(
            openai_client, settings.openai_model, profile, resume
        )
        cover_letter_service = CoverLetterService(
            openai_client, settings.openai_model, profile, resume
        )
        search_service = VacancySearchService(
            hh_client,
            vacancy_repository,
            VacancyFilter(),
            ranker,
            min_score=settings.min_score_to_send,
        )
        context = BotContext(
            settings=settings,
            profile=profile,
            vacancy_repository=vacancy_repository,
            application_repository=application_repository,
            search_service=search_service,
            digest_service=DigestService(vacancy_repository),
            cover_letter_service=cover_letter_service,
            search_lock=asyncio.Lock(),
        )
        dispatcher = Dispatcher()
        dispatcher.include_router(build_callbacks_router(context))
        dispatcher.include_router(build_handlers_router(context))
        scheduler = create_scheduler(bot, context)

        await bot.set_my_commands(BOT_COMMANDS)
        await bot.delete_webhook(drop_pending_updates=False)
        scheduler.start()
        logger.info("Job Agent started")
        await dispatcher.start_polling(
            bot, allowed_updates=dispatcher.resolve_used_update_types()
        )
    finally:
        if scheduler and scheduler.running:
            scheduler.shutdown(wait=False)
        await hh_client.close()
        await openai_client.close()
        await bot.session.close()
        await database.close()
        logger.info("Job Agent stopped")


def run() -> None:
    try:
        settings = get_settings()
    except ValidationError as exc:
        fields = ", ".join(
            str(error.get("loc", ["?"])[0]) for error in exc.errors()
        )
        print(
            f"Ошибка конфигурации. Проверьте .env. Поля: {fields}",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc

    configure_logging(settings.log_level)
    try:
        asyncio.run(async_main(settings))
    except CandidateProfileError as exc:
        logger.error("%s", exc)
        raise SystemExit(2) from exc
    except KeyboardInterrupt:
        logger.info("Stopped by user")
    except Exception as exc:
        logger.error("Critical startup/runtime error: %s", exc)
        raise SystemExit(1) from exc
