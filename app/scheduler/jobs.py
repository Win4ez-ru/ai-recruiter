import logging

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.bot.context import BotContext

logger = logging.getLogger(__name__)


async def scheduled_search(bot: Bot, context: BotContext) -> None:
    if context.search_lock.locked():
        logger.info("Skipping scheduled search because another search is running")
        return
    try:
        async with context.search_lock:
            await context.search_service.run()
        vacancies = await context.vacancy_repository.list_digest_candidates(
            context.settings.min_score_to_send,
            context.settings.max_vacancies_per_digest,
            only_unsent=True,
        )
        if not vacancies:
            logger.info("Scheduled search found no new digest candidates")
            return
        sent = await context.digest_service.send_vacancies(
            bot,
            context.settings.telegram_user_id,
            vacancies,
            mark_sent=True,
        )
        logger.info("Scheduled digest sent %s vacancies", sent)
    except Exception:
        logger.exception("Scheduled vacancy search failed")


def create_scheduler(bot: Bot, context: BotContext) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    scheduler.add_job(
        scheduled_search,
        trigger="interval",
        hours=context.settings.search_interval_hours,
        kwargs={"bot": bot, "context": context},
        id="vacancy_search",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    return scheduler
