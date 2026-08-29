import logging
from datetime import timedelta

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.bot.context import BotContext
from app.models import utc_now

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
            **context.search_service.analysis_scope,
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


async def reconcile_stale_submissions(context: BotContext) -> None:
    """Quarantine abandoned POST leases without ever replaying the request."""

    try:
        (
            repaired,
            recovered,
        ) = await context.hh_application_repository.reconcile_incomplete_finalizations(
            stale_before=utc_now()
            - timedelta(seconds=context.settings.hh_submission_recovery_seconds)
        )
        if repaired or recovered:
            logger.warning(
                "Reconciled stale HeadHunter application state",
                extra={
                    "event": "hh_application_periodic_reconciliation",
                    "repaired": repaired,
                    "recovered_unknown": recovered,
                },
            )
    except Exception:
        logger.exception("Periodic HeadHunter application reconciliation failed")


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
    scheduler.add_job(
        reconcile_stale_submissions,
        trigger="interval",
        seconds=context.settings.hh_submission_recovery_seconds,
        kwargs={"context": context},
        id="hh_submission_reconciliation",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    return scheduler
