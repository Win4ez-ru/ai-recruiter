from __future__ import annotations

import json
import logging
from typing import Any

from openai import OpenAIError

from app.models import Vacancy
from app.schemas import CandidateProfile, VacancyAnalysisResult, VacancyFilterResult
from app.services.openai_errors import normalize_openai_error

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты оцениваешь соответствие вакансии реальному профилю кандидата.
Правила:
1. Не выдумывай навыки или опыт кандидата.
2. Не считай отсутствие коммерческого опыта абсолютным блокером.
3. Отличай обязательные требования от желательных.
4. Сложные собственные проекты могут частично компенсировать отсутствие коммерческого опыта.
5. Не завышай оценку ради поддержки кандидата.
6. Не занижай оценку только из-за слова Middle: оцени фактические требования.
7. Блокеры: обязательные 4+ года опыта, обязательное руководство, обязательная
   релокация, отсутствие iOS/Swift, только Objective-C, жесткая Senior/Lead-позиция.
8. Причины пиши кратко и конкретно.
9. Оценка: 90–100 почти идеально; 75–89 хорошо; 60–74 допустимо;
   40–59 слабо; 0–39 не откликаться.
10. Верни только данные заданной схемы."""


class VacancyRanker:
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

    async def rank(
        self, vacancy: Vacancy, filter_result: VacancyFilterResult
    ) -> VacancyAnalysisResult | None:
        payload = {
            "candidate_profile": self.profile.model_dump(),
            "resume": self.resume,
            "vacancy": {
                "title": vacancy.title,
                "company": vacancy.company,
                "salary": {
                    "from": vacancy.salary_from,
                    "to": vacancy.salary_to,
                    "currency": vacancy.salary_currency,
                    "gross": vacancy.salary_gross,
                },
                "location": vacancy.location,
                "work_format": vacancy.work_format,
                "experience": vacancy.experience,
                "description": vacancy.description[:20_000],
                "requirements": vacancy.requirements,
                "responsibilities": vacancy.responsibilities,
                "key_skills": vacancy.key_skills,
            },
            "prefilter": filter_result.model_dump(),
        }
        logger.info("Calling OpenAI ranker for vacancy %s", vacancy.id)
        try:
            response = await self.client.responses.parse(
                model=self.model_name,
                input=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(payload, ensure_ascii=False),
                    },
                ],
                text_format=VacancyAnalysisResult,
            )
            parsed = response.output_parsed
            if parsed is None:
                logger.warning("OpenAI returned no parsed analysis for vacancy %s", vacancy.id)
                return None
            return VacancyAnalysisResult.model_validate(parsed)
        except OpenAIError as exc:
            error = normalize_openai_error(exc)
            logger.warning(
                "OpenAI analysis request failed",
                extra={
                    "event": "openai_rank_failed",
                    "vacancy_id": vacancy.id,
                    "error_code": error.code,
                    "error_type": type(exc).__name__,
                },
            )
            raise error from exc
        except Exception:
            logger.exception(
                "Unexpected OpenAI analysis processing error for vacancy %s",
                vacancy.id,
            )
            return None
