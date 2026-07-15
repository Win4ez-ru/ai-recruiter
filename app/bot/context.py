from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.config import Settings
from app.repositories.application_repository import ApplicationRepository
from app.repositories.vacancy_repository import VacancyRepository
from app.schemas import CandidateProfile
from app.services.cover_letter import CoverLetterService
from app.services.digest import DigestService
from app.services.vacancy_search import VacancySearchService


@dataclass(slots=True)
class BotContext:
    settings: Settings
    profile: CandidateProfile
    vacancy_repository: VacancyRepository
    application_repository: ApplicationRepository
    search_service: VacancySearchService
    digest_service: DigestService
    cover_letter_service: CoverLetterService
    search_lock: asyncio.Lock
