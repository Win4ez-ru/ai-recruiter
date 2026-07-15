import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from app.bot.formatter import format_vacancy_card
from app.bot.keyboards import vacancy_keyboard
from app.models import Vacancy
from app.repositories.vacancy_repository import VacancyRepository

logger = logging.getLogger(__name__)


class DigestService:
    def __init__(self, repository: VacancyRepository) -> None:
        self.repository = repository

    async def send_vacancies(
        self,
        bot: Bot,
        chat_id: int,
        vacancies: list[Vacancy],
        *,
        mark_sent: bool,
    ) -> int:
        sent = 0
        for vacancy in vacancies:
            if vacancy.analysis is None:
                continue
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=format_vacancy_card(vacancy),
                    reply_markup=vacancy_keyboard(
                        vacancy.id,
                        vacancy.url,
                        vacancy.application.status if vacancy.application else "new",
                    ),
                )
                if mark_sent:
                    await self.repository.mark_sent(vacancy.id)
                sent += 1
            except TelegramAPIError:
                logger.exception("Failed to send vacancy %s to Telegram", vacancy.id)
            except Exception:
                logger.exception("Unexpected digest error for vacancy %s", vacancy.id)
        return sent
