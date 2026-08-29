from __future__ import annotations

import json
import logging
import re

from app.models import Vacancy
from app.schemas import CandidateProfile
from app.services.ai_errors import AIServiceError
from app.services.ai_provider import AIProvider

logger = logging.getLogger(__name__)
MAX_RESUME_CHARS = 20_000
MAX_DESCRIPTION_CHARS = 12_000
MAX_REQUIREMENTS_CHARS = 6_000


class CoverLetterService:
    def __init__(
        self,
        client: AIProvider,
        model_name: str,
        profile: CandidateProfile,
        resume: str,
    ) -> None:
        self.client = client
        self.model_name = model_name
        self.profile = profile
        self.resume = resume

    async def generate(self, vacancy: Vacancy) -> str | None:
        analysis = vacancy.analysis
        language = (
            "русский"
            if re.search(r"[А-Яа-яЁё]", f"{vacancy.title} {vacancy.description}")
            else "английский"
        )
        data = {
            "profile": self.profile.model_dump(),
            "resume": self.resume[:MAX_RESUME_CHARS],
            "vacancy": {
                "title": vacancy.title,
                "company": vacancy.company,
                "description": vacancy.description[:MAX_DESCRIPTION_CHARS],
                "requirements": vacancy.requirements[:MAX_REQUIREMENTS_CHARS],
                "key_skills": vacancy.key_skills,
            },
            "analysis": {
                "matched_skills": analysis.matched_skills if analysis else [],
                "missing_skills": analysis.missing_skills if analysis else [],
                "advantages": analysis.advantages if analysis else [],
                "risks": analysis.risks if analysis else [],
                "resume_focus": analysis.resume_focus if analysis else [],
            },
        }
        prompt = f"""Напиши индивидуальное сопроводительное письмо. Язык: {language}.
Длина 500–900 символов. Не используй клише про динамичную компанию, дружный
коллектив или мечту работать здесь. Не выдумывай коммерческий опыт и не называй
кандидата Senior/Middle. Упомяни 1–2 требования вакансии и свяжи их с реальными
проектами, выбрав только релевантные примеры из profile.projects. Уверенно и кратко
обозначь отсутствие большого коммерческого опыта.
Заверши предложением показать проекты/код или выполнить тестовое. Без эмодзи.
Верни только готовое письмо без заголовка и пояснений.

Данные:
{json.dumps(data, ensure_ascii=False)}"""
        logger.info(
            "Calling AI cover-letter generator for vacancy %s",
            vacancy.id,
            extra={"event": "ai_cover_letter_started", "provider": self.client.name},
        )
        try:
            text = await self.client.generate_text(
                model=self.model_name,
                prompt=prompt,
            )
            if not text:
                logger.warning(
                    "AI provider returned an empty cover letter for %s", vacancy.id
                )
                return None
            return text.strip()
        except AIServiceError:
            logger.warning(
                "AI cover-letter request failed",
                extra={
                    "event": "ai_cover_letter_failed",
                    "provider": self.client.name,
                    "vacancy_id": vacancy.id,
                },
            )
            raise
        except Exception:
            logger.exception(
                "Unexpected cover-letter processing error for vacancy %s",
                vacancy.id,
            )
            return None
