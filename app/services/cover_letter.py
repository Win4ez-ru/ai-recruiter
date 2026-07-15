from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.models import Vacancy
from app.schemas import CandidateProfile

logger = logging.getLogger(__name__)


class CoverLetterService:
    def __init__(
        self,
        client: Any,
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
            "resume": self.resume,
            "vacancy": {
                "title": vacancy.title,
                "company": vacancy.company,
                "description": vacancy.description[:15_000],
                "requirements": vacancy.requirements,
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
проектами. Уверенно и кратко обозначь отсутствие большого коммерческого опыта.
Заверши предложением показать проекты/код или выполнить тестовое. Без эмодзи.
Верни только готовое письмо без заголовка и пояснений.

Данные:
{json.dumps(data, ensure_ascii=False)}"""
        logger.info("Calling OpenAI cover-letter generator for vacancy %s", vacancy.id)
        try:
            response = await self.client.responses.create(
                model=self.model_name,
                input=prompt,
            )
            text = (response.output_text or "").strip()
            if not text:
                logger.warning("OpenAI returned an empty cover letter for %s", vacancy.id)
                return None
            return text
        except Exception:
            logger.exception("Cover letter generation failed for vacancy %s", vacancy.id)
            return None
