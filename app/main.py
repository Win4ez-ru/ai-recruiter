from __future__ import annotations

import asyncio
import logging
import signal
import sys
from collections.abc import Awaitable, Callable

import httpx
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
from openai import AsyncOpenAI
from pydantic import ValidationError

from app.bot.callbacks import build_callbacks_router
from app.bot.context import BotContext
from app.bot.handlers import build_handlers_router
from app.bot.hh_applications import build_hh_applications_router
from app.config import Settings, get_settings
from app.database import Database
from app.health import HealthRegistry, HealthStatus
from app.logging_config import configure_logging
from app.network.retry import RetryPolicy
from app.network.telegram import (
    FailoverTelegramSession,
    TelegramTransportStatus,
    build_telegram_session,
    telegram_backoff_config,
    wait_for_telegram,
)
from app.repositories.application_repository import ApplicationRepository
from app.repositories.hh_application_repository import HHApplicationRepository
from app.repositories.hh_integration_repository import HHIntegrationRepository
from app.repositories.vacancy_repository import VacancyRepository
from app.scheduler.jobs import create_scheduler
from app.services.candidate_profile import (
    CandidateProfileError,
    load_candidate_profile,
    load_resume,
)
from app.services.cover_letter import CoverLetterService
from app.services.digest import DigestService
from app.services.hh_application import HHApplicationService
from app.services.hh_oauth import HHOAuthService
from app.services.hh_oauth_callback import ApplicationHTTPServer
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
    BotCommand(command="hh", description="Подключить HeadHunter"),
    BotCommand(command="help", description="Справка"),
]


async def async_main(settings: Settings) -> None:
    loop = asyncio.get_running_loop()
    main_task = asyncio.current_task()
    registered_signals: list[signal.Signals] = []

    def request_shutdown(signal_name: str) -> None:
        logger.info(
            "Shutdown signal received",
            extra={"event": "shutdown_requested", "signal": signal_name},
        )
        if main_task is not None and not main_task.cancelling():
            main_task.cancel()

    for signal_name in ("SIGINT", "SIGTERM"):
        shutdown_signal = getattr(signal, signal_name, None)
        if shutdown_signal is None:
            continue
        try:
            loop.add_signal_handler(
                shutdown_signal,
                request_shutdown,
                signal_name,
            )
        except (NotImplementedError, RuntimeError):
            continue
        registered_signals.append(shutdown_signal)

    health = HealthRegistry()
    health.set_component("database", "starting")
    health.set_component("telegram", "starting")

    def update_telegram_health(status: TelegramTransportStatus) -> None:
        if status.routes_in_cooldown == status.configured_routes:
            state: HealthStatus = "down"
        elif status.routes_in_cooldown:
            state = "degraded"
        else:
            state = "ok"
        health.set_component(
            "telegram",
            state,
            detail=f"route={status.active_route}",
        )

    profile = load_candidate_profile(settings.candidate_profile_path)
    resume = load_resume(settings.resume_path)
    database: Database | None = None
    telegram_session: FailoverTelegramSession | None = None
    openai_http_client: httpx.AsyncClient | None = None
    openai_client: AsyncOpenAI | None = None
    hh_client: HHClient | None = None
    scheduler = None
    callback_server: ApplicationHTTPServer | None = None

    try:
        database = Database(
            settings.database_url_value,
            connect_timeout_seconds=settings.database_connect_timeout_seconds,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_recycle_seconds=settings.database_pool_recycle_seconds,
        )
        telegram_session = build_telegram_session(
            settings,
            status_callback=update_telegram_health,
        )
        bot = Bot(
            token=settings.telegram_bot_token_value,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
            session=telegram_session,
        )
        openai_http_client = httpx.AsyncClient(
            proxy=settings.openai_proxy_value,
            timeout=httpx.Timeout(settings.openai_timeout_seconds),
            trust_env=settings.openai_trust_env,
        )
        openai_client = AsyncOpenAI(
            api_key=settings.openai_api_key_value,
            max_retries=settings.openai_max_retries,
            timeout=settings.openai_timeout_seconds,
            http_client=openai_http_client,
        )
        hh_client = HHClient(
            settings.hh_user_agent,
            api_base_url=settings.hh_api_base_url,
            auth_base_url=settings.hh_auth_base_url,
            client_id=settings.hh_client_id,
            client_secret=settings.hh_client_secret_value,
            redirect_uri=settings.hh_redirect_uri,
            timeout=httpx.Timeout(
                connect=settings.hh_connect_timeout_seconds,
                read=settings.hh_read_timeout_seconds,
                write=settings.hh_write_timeout_seconds,
                pool=settings.hh_pool_timeout_seconds,
            ),
            retry_policy=RetryPolicy(
                max_attempts=settings.hh_retry_attempts,
                base_delay_seconds=settings.hh_retry_base_delay_seconds,
                max_delay_seconds=settings.hh_retry_max_delay_seconds,
                jitter_ratio=settings.hh_retry_jitter_ratio,
            ),
            proxy_url=settings.hh_proxy_value,
            trust_env=settings.hh_trust_env,
        )
        if settings.database_auto_create:
            await database.create_tables()
        else:
            await database.check_connection()
        health.set_component("database", "ok")
        vacancy_repository = VacancyRepository(database)
        application_repository = ApplicationRepository(database)
        hh_integration_repository = HHIntegrationRepository(database)
        hh_application_repository = HHApplicationRepository(database)
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
        hh_oauth_service = HHOAuthService(
            hh_client, hh_integration_repository, settings
        )
        hh_application_service = HHApplicationService(
            hh_client=hh_client,
            oauth_service=hh_oauth_service,
            integration_repository=hh_integration_repository,
            application_repository=hh_application_repository,
            status_repository=application_repository,
            vacancy_repository=vacancy_repository,
            cover_letter_service=cover_letter_service,
            confirmation_ttl_seconds=settings.hh_confirmation_ttl_seconds,
            default_resume_id=settings.hh_default_resume_id,
        )
        context = BotContext(
            settings=settings,
            profile=profile,
            vacancy_repository=vacancy_repository,
            application_repository=application_repository,
            search_service=search_service,
            digest_service=DigestService(vacancy_repository),
            cover_letter_service=cover_letter_service,
            hh_integration_repository=hh_integration_repository,
            hh_application_repository=hh_application_repository,
            hh_oauth_service=hh_oauth_service,
            hh_application_service=hh_application_service,
            search_lock=asyncio.Lock(),
        )
        dispatcher = Dispatcher()
        dispatcher.include_router(build_hh_applications_router(context))
        dispatcher.include_router(build_callbacks_router(context))
        dispatcher.include_router(build_handlers_router(context))
        scheduler = create_scheduler(bot, context)
        callback_server = ApplicationHTTPServer(
            settings=settings,
            oauth_service=hh_oauth_service,
            bot=bot,
            health=health,
        )

        await callback_server.start()
        health.mark_ready()
        await wait_for_telegram(
            bot,
            BOT_COMMANDS,
            backoff_config=telegram_backoff_config(settings),
        )
        health.set_component("telegram", "ok")
        scheduler.start()
        logger.info("Job Agent started")
        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
            polling_timeout=settings.telegram_polling_timeout_seconds,
            backoff_config=telegram_backoff_config(settings),
            close_bot_session=False,
            handle_signals=False,
        )
    except asyncio.CancelledError:
        logger.info("Job Agent shutdown requested")
    finally:
        health.mark_stopping()
        if scheduler and scheduler.running:
            try:
                scheduler.shutdown(wait=False)
            except Exception:
                logger.exception("Failed to stop scheduler")
        if callback_server is not None:
            await _close_resource("http_server", callback_server.close)
        if hh_client is not None:
            await _close_resource("hh_client", hh_client.close)
        if openai_client is not None:
            await _close_resource("openai_client", openai_client.close)
        elif openai_http_client is not None:
            await _close_resource("openai_http_client", openai_http_client.aclose)
        if telegram_session is not None:
            await _close_resource("telegram_session", telegram_session.close)
        if database is not None:
            await _close_resource("database", database.close)
        for shutdown_signal in registered_signals:
            loop.remove_signal_handler(shutdown_signal)
        logger.info("Job Agent stopped")


async def _close_resource(
    name: str,
    close: Callable[[], Awaitable[None]],
) -> None:
    try:
        await close()
    except Exception:
        logger.exception("Failed to close resource %s", name)


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

    configure_logging(
        settings.log_level,
        output_format=settings.log_format,
        file_enabled=settings.log_file_enabled,
        file_path=settings.log_file_path,
        file_max_bytes=settings.log_file_max_bytes,
        file_backup_count=settings.log_file_backup_count,
    )
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
