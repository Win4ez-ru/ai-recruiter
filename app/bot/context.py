from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.bot.ui import UIManager
from app.config import Settings
from app.repositories.application_repository import ApplicationRepository
from app.repositories.hh_application_repository import HHApplicationRepository
from app.repositories.hh_integration_repository import HHIntegrationRepository
from app.repositories.vacancy_repository import VacancyRepository
from app.schemas import CandidateProfile
from app.services.cover_letter import CoverLetterService
from app.services.digest import DigestService
from app.services.hh_application import HHApplicationService
from app.services.hh_oauth import HHOAuthService
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
    hh_integration_repository: HHIntegrationRepository
    hh_application_repository: HHApplicationRepository
    hh_oauth_service: HHOAuthService
    hh_application_service: HHApplicationService
    search_lock: asyncio.Lock
    ui: UIManager
