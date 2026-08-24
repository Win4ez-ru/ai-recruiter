from __future__ import annotations

import logging

from aiogram import Bot

from app.bot.formatter import format_vacancy_card
from app.bot.keyboards import collection_keyboard
from app.bot.ui import UIManager
from app.models import Vacancy
from app.repositories.vacancy_repository import VacancyRepository
from app.vacancy_status import is_excluded_from_recommendations

logger = logging.getLogger(__name__)


class DigestService:
    def __init__(self, repository: VacancyRepository, ui: UIManager) -> None:
        self.repository = repository
        self.ui = ui

    async def send_vacancies(
        self,
        bot: Bot,
        chat_id: int,
        vacancies: list[Vacancy],
        *,
        mark_sent: bool,
    ) -> int:
        valid = [
            vacancy
            for vacancy in vacancies
            if vacancy.analysis is not None
            and (
                vacancy.application is None
                or not is_excluded_from_recommendations(vacancy.application.status)
            )
        ]
        if not valid:
            return 0
        current = await self.ui.restore(chat_id)
        if current.screen != "menu":
            logger.info(
                "Deferred vacancy collection while Telegram UI is active",
                extra={"event": "digest_ui_deferred", "screen": current.screen},
            )
            return 0
        self.ui.cancel_operation(chat_id)
        session = self.ui.set_collection(
            chat_id,
            [vacancy.id for vacancy in valid],
            title="Новые вакансии",
            kind="new",
        )
        vacancy = valid[0]
        status = vacancy.application.status if vacancy.application else "new"
        rendered = await self.ui.render_chat(
            bot,
            chat_id,
            format_vacancy_card(
                vacancy,
                position=1,
                total=len(valid),
                collection_title=session.collection_title,
            ),
            collection_keyboard(
                vacancy.id,
                vacancy.url,
                status,
                position=1,
                total=len(valid),
                expanded=False,
            ),
            screen="collection",
        )
        if rendered is None:
            return 0
        if mark_sent:
            await self.repository.mark_sent(vacancy.id)
        logger.info("Rendered vacancy collection with %s items", len(valid))
        return 1
