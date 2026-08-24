from __future__ import annotations

import json
import logging
from hashlib import sha256

from app.models import Vacancy
from app.schemas import CandidateProfile, VacancyAnalysisResult, VacancyFilterResult
from app.services.ai_errors import AIServiceError
from app.services.ai_provider import AIProvider

logger = logging.getLogger(__name__)
PROMPT_VERSION = "vacancy-ranker-2026-08-24.v2"
MAX_RESUME_CHARS = 20_000
MAX_DESCRIPTION_CHARS = 12_000
MAX_REQUIREMENTS_CHARS = 6_000
MAX_RESPONSIBILITIES_CHARS = 6_000

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
        client: AIProvider,
        model_name: str,
        profile: CandidateProfile,
        resume: str,
    ) -> None:
        self.client = client
        self.model_name = model_name
        self.profile = profile
        self.resume = resume

    @property
    def provider_name(self) -> str:
        return self.client.name

    @property
    def prompt_version(self) -> str:
        return PROMPT_VERSION

    def input_hash(self, vacancy: Vacancy, filter_result: VacancyFilterResult) -> str:
        fingerprint = {
            "provider": self.provider_name,
            "model": self.model_name,
            "prompt_version": self.prompt_version,
            "payload": self._payload(vacancy, filter_result),
        }
        canonical = json.dumps(
            fingerprint,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(canonical.encode("utf-8")).hexdigest()

    def analysis_is_current(
        self, vacancy: Vacancy, filter_result: VacancyFilterResult
    ) -> bool:
        analysis = vacancy.analysis
        return bool(
            analysis is not None
            and analysis.provider == self.provider_name
            and analysis.model_name == self.model_name
            and analysis.prompt_version == self.prompt_version
            and analysis.input_hash == self.input_hash(vacancy, filter_result)
        )

    def _payload(
        self, vacancy: Vacancy, filter_result: VacancyFilterResult
    ) -> dict[str, object]:
        return {
            "candidate_profile": self.profile.model_dump(),
            "resume": self.resume[:MAX_RESUME_CHARS],
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
                "description": vacancy.description[:MAX_DESCRIPTION_CHARS],
                "requirements": vacancy.requirements[:MAX_REQUIREMENTS_CHARS],
                "responsibilities": vacancy.responsibilities[
                    :MAX_RESPONSIBILITIES_CHARS
                ],
                "key_skills": vacancy.key_skills,
            },
            "prefilter": filter_result.model_dump(),
        }

    async def rank(
        self, vacancy: Vacancy, filter_result: VacancyFilterResult
    ) -> VacancyAnalysisResult | None:
        payload = self._payload(vacancy, filter_result)
        logger.info(
            "Calling AI ranker for vacancy %s",
            vacancy.id,
            extra={"event": "ai_rank_started", "provider": self.client.name},
        )
        try:
            return await self.client.generate_structured(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": json.dumps(payload, ensure_ascii=False),
                    },
                ],
                response_model=VacancyAnalysisResult,
            )
        except AIServiceError:
            logger.warning(
                "AI analysis request failed",
                extra={
                    "event": "ai_rank_failed",
                    "provider": self.client.name,
                    "vacancy_id": vacancy.id,
                },
            )
            raise
        except Exception:
            logger.exception(
                "Unexpected AI analysis processing error for vacancy %s",
                vacancy.id,
            )
            return None
