from __future__ import annotations

import asyncio
import logging

from aiogram.types import CallbackQuery, Message

from app.bot.context import BotContext
from app.bot.formatter import format_vacancy_card
from app.bot.keyboards import (
    back_keyboard,
    collection_keyboard,
    connect_hh_keyboard,
    main_menu_keyboard,
)
from app.bot.screens import (
    empty_collection_text,
    help_text,
    hh_text,
    main_menu_text,
    profile_text,
    search_progress_text,
    stats_text,
)
from app.models import Vacancy

logger = logging.getLogger(__name__)
ScreenTarget = Message | CallbackQuery
COLLECTION_LIMIT = 100

SEARCH_ERROR_MESSAGES = {
    "hh_configuration": "HH не принял данные приложения. Проверьте HH_CLIENT_ID и HH_CLIENT_SECRET.",
    "hh_forbidden": "HH временно отклонил поиск. Повторите позже.",
    "hh_rate_limited": "HH ограничил частоту запросов. Повторите позже.",
    "hh_unavailable": "HH сейчас недоступен. Повторите поиск позже.",
    "ai_rate_limited": "AI временно ограничил запросы. Повторите позже.",
    "ai_unavailable": "AI сейчас недоступен. Повторите позже.",
    "ai_configuration": "AI-модель не настроена или недоступна.",
}


def target_chat_id(target: ScreenTarget) -> int | None:
    if isinstance(target, CallbackQuery):
        return target.message.chat.id if isinstance(target.message, Message) else None
    return target.chat.id


async def show_main(
    context: BotContext, target: ScreenTarget, *, notice: str | None = None
) -> None:
    integration, new_count = await asyncio.gather(
        context.hh_integration_repository.get_integration(
            context.settings.telegram_user_id
        ),
        context.vacancy_repository.count_new_candidates(
            context.settings.min_score_to_send
        ),
    )
    await context.ui.render(
        target,
        main_menu_text(
            context.profile,
            hh_connected=integration is not None,
            ai_provider=context.settings.ai_provider,
            new_count=new_count,
            demo_mode=context.settings.demo_mode,
            notice=notice,
        ),
        main_menu_keyboard(),
        screen="menu",
    )


async def show_help(context: BotContext, target: ScreenTarget) -> None:
    await context.ui.render(target, help_text(), back_keyboard(), screen="help")


async def show_profile(context: BotContext, target: ScreenTarget) -> None:
    await context.ui.render(
        target, profile_text(context.profile), back_keyboard(), screen="profile"
    )


async def show_stats(context: BotContext, target: ScreenTarget) -> None:
    stats = await context.vacancy_repository.stats()
    await context.ui.render(target, stats_text(stats), back_keyboard(), screen="stats")


async def show_hh(context: BotContext, target: ScreenTarget) -> None:
    integration = await context.hh_integration_repository.get_integration(
        context.settings.telegram_user_id
    )
    resumes = (
        await context.hh_integration_repository.list_resumes(
            context.settings.telegram_user_id
        )
        if integration is not None
        else []
    )
    await context.ui.render(
        target,
        hh_text(
            connected=integration is not None,
            resumes=len(resumes),
            configured=context.settings.hh_oauth_configured,
        ),
        (
            back_keyboard()
            if integration is not None or not context.settings.hh_oauth_configured
            else connect_hh_keyboard()
        ),
        screen="hh",
    )


async def collection_for_kind(context: BotContext, kind: str) -> list[Vacancy]:
    if kind == "new":
        return await context.vacancy_repository.list_digest_candidates(
            context.settings.min_score_to_send,
            COLLECTION_LIMIT,
            only_unsent=True,
        )
    if kind == "top":
        return await context.vacancy_repository.list_digest_candidates(
            context.settings.min_score_to_send,
            COLLECTION_LIMIT,
            only_unsent=False,
        )
    if kind in {"saved", "applied"}:
        if kind == "applied":
            return await context.vacancy_repository.list_applied(limit=COLLECTION_LIMIT)
        return await context.vacancy_repository.list_by_application_status(
            kind, limit=COLLECTION_LIMIT
        )
    return []


async def show_collection_kind(
    context: BotContext,
    target: ScreenTarget,
    kind: str,
) -> None:
    titles = {
        "new": "Новые вакансии",
        "top": "Лучшие совпадения",
        "saved": "Избранное",
        "applied": "Мои отклики",
    }
    empty = {
        "new": "Новых подходящих вакансий пока нет.",
        "top": "Проанализированных вакансий пока нет.",
        "saved": "Вы ещё ничего не сохранили.",
        "applied": "Подтверждённых откликов пока нет.",
    }
    vacancies = await collection_for_kind(context, kind)
    await show_collection(
        context,
        target,
        vacancies,
        title=titles[kind],
        empty_message=empty[kind],
        kind=kind,
    )


async def show_collection(
    context: BotContext,
    target: ScreenTarget,
    vacancies: list[Vacancy],
    *,
    title: str,
    empty_message: str,
    kind: str = "custom",
) -> None:
    chat_id = target_chat_id(target)
    if chat_id is None:
        return
    valid = [vacancy for vacancy in vacancies if vacancy.analysis is not None]
    if not valid:
        context.ui.set_collection(chat_id, [], title=title, kind=kind)
        await context.ui.render(
            target,
            empty_collection_text(title, empty_message),
            back_keyboard(search=True),
            screen="collection_empty",
        )
        return
    context.ui.set_collection(
        chat_id,
        [vacancy.id for vacancy in valid],
        title=title,
        kind=kind,
    )
    await show_current_collection(context, target)


async def show_current_collection(
    context: BotContext,
    target: ScreenTarget,
) -> bool:
    chat_id = target_chat_id(target)
    if chat_id is None:
        return False
    session = await context.ui.restore(chat_id)
    vacancy_id = session.current_vacancy_id
    while vacancy_id is not None:
        vacancy = await context.vacancy_repository.get_by_id(vacancy_id)
        if vacancy is not None and vacancy.analysis is not None:
            break
        context.ui.remove_from_collection(chat_id, vacancy_id)
        vacancy_id = session.current_vacancy_id
    else:
        await context.ui.render(
            target,
            empty_collection_text(
                session.collection_title, "В этой подборке больше нет вакансий."
            ),
            back_keyboard(search=True),
            screen="collection_empty",
        )
        return False

    position = session.collection_index + 1
    total = len(session.collection_ids)
    status = vacancy.application.status if vacancy.application else "new"
    rendered = await context.ui.render(
        target,
        format_vacancy_card(
            vacancy,
            position=position,
            total=total,
            collection_title=session.collection_title,
            expanded=session.expanded,
        ),
        collection_keyboard(
            vacancy.id,
            vacancy.url,
            status,
            position=position,
            total=total,
            expanded=session.expanded,
        ),
        screen="collection",
    )
    if rendered is not None and status == "new":
        await context.application_repository.mark_viewed(vacancy.id)
    if rendered is not None and not vacancy.is_sent:
        await context.vacancy_repository.mark_sent(vacancy.id)
    return rendered is not None


async def run_search(context: BotContext, target: ScreenTarget) -> None:
    chat_id = target_chat_id(target)
    if chat_id is None:
        return
    if context.search_lock.locked():
        await context.ui.render(
            target,
            search_progress_text("Поиск уже выполняется. Результаты скоро появятся."),
            back_keyboard(),
            screen="search",
        )
        return

    await context.ui.render(
        target,
        search_progress_text(),
        back_keyboard(),
        screen="search",
    )

    async def progress(text: str) -> None:
        if context.ui.session(chat_id).screen != "search":
            return
        await context.ui.render_chat(
            target.bot,
            chat_id,
            search_progress_text(text),
            back_keyboard(),
            screen="search",
        )

    try:
        async with context.search_lock:
            summary = await context.search_service.run(progress=progress)
        if context.ui.session(chat_id).screen != "search":
            return
        fresh_vacancies = await context.vacancy_repository.list_digest_candidates(
            context.settings.min_score_to_send,
            COLLECTION_LIMIT,
            only_unsent=True,
        )
        vacancies = fresh_vacancies
        cached = False
        if not vacancies:
            vacancies = await context.vacancy_repository.list_digest_candidates(
                context.settings.min_score_to_send,
                COLLECTION_LIMIT,
                only_unsent=False,
            )
            cached = bool(vacancies)
        if vacancies:
            suffix = " • сохранённые результаты" if cached else ""
            if summary.error_codes:
                suffix += " • неполный результат"
            await show_collection(
                context,
                target,
                vacancies,
                title=f"Результаты поиска{suffix}",
                empty_message="Подходящих вакансий не найдено.",
                kind="top" if cached else "new",
            )
            return
        errors = " ".join(SEARCH_ERROR_MESSAGES[code] for code in summary.error_codes)
        message = errors or (
            f"Найдено {summary.found}, но подходящих под профиль вакансий нет."
        )
        await context.ui.render(
            target,
            empty_collection_text("Поиск завершён", message),
            back_keyboard(search=True),
            screen="search_result",
        )
    except Exception:
        logger.exception("Manual vacancy search failed")
        await context.ui.render(
            target,
            empty_collection_text(
                "Не удалось завершить поиск",
                "Один из внешних сервисов не ответил. Попробуйте ещё раз.",
            ),
            back_keyboard(search=True),
            screen="search_error",
        )
