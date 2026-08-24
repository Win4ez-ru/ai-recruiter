from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.schemas import VacancyAnalysisResult
from app.services.vacancy_filter import VacancyFilter
from app.services.vacancy_search import VacancySearchService
from app.sources.hh import HHApplicationAuthorizationError, HHRemoteError


class ForbiddenHHClient:
    def __init__(self) -> None:
        self.calls = 0

    async def search_vacancies(
        self, query: str, *, max_results: int = 100
    ) -> list[dict]:
        self.calls += 1
        raise HHRemoteError(
            403,
            "forbidden",
            request_id="request-123",
        )


@pytest.mark.asyncio
async def test_search_stops_after_provider_level_forbidden_error() -> None:
    client = ForbiddenHHClient()
    service = VacancySearchService(
        client,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        VacancyFilter(),
        object(),  # type: ignore[arg-type]
        min_score=65,
    )

    summary = await service.run()

    assert client.calls == 1
    assert summary.errors == 1
    assert summary.error_codes == ["hh_forbidden"]


class RejectedCredentialsHHClient:
    async def search_vacancies(
        self, query: str, *, max_results: int = 100
    ) -> list[dict]:
        raise HHApplicationAuthorizationError("rejected")


@pytest.mark.asyncio
async def test_search_reports_rejected_application_credentials() -> None:
    service = VacancySearchService(
        RejectedCredentialsHHClient(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        VacancyFilter(),
        object(),  # type: ignore[arg-type]
        min_score=65,
    )

    summary = await service.run()

    assert summary.errors == 1
    assert summary.error_codes == ["hh_configuration"]


class HappyHHClient:
    async def search_vacancies(
        self, query: str, *, max_results: int = 100
    ) -> list[dict]:
        return [
            {
                "id": "happy-1",
                "name": "Junior iOS Developer",
                "alternate_url": "https://hh.ru/vacancy/happy-1",
                "employer": {"name": "Acme"},
                "area": {"name": "Санкт-Петербург"},
                "snippet": {
                    "requirement": "Swift, SwiftUI",
                    "responsibility": "Разработка iOS-приложения",
                },
            }
        ]

    async def get_vacancy(self, external_id: str) -> dict:
        return {
            "id": external_id,
            "name": "Junior iOS Developer",
            "alternate_url": f"https://hh.ru/vacancy/{external_id}",
            "description": "Swift и SwiftUI без обязательного большого опыта",
            "employer": {"name": "Acme"},
            "area": {"name": "Санкт-Петербург"},
            "experience": {"name": "1–3 года"},
            "employment": {"name": "Полная занятость"},
            "schedule": {"name": "Удалённая работа"},
            "key_skills": [{"name": "Swift"}, {"name": "SwiftUI"}],
        }


class MemoryVacancyRepository:
    def __init__(self) -> None:
        self.item: SimpleNamespace | None = None

    async def external_ids_needing_refresh(self, external_ids, **kwargs) -> set[str]:
        return set(external_ids)

    async def create_if_new(self, data):
        self.item = SimpleNamespace(
            id=1,
            analysis=None,
            application=None,
            **data.model_dump(),
        )
        return self.item, True

    async def list_by_external_ids(self, external_ids) -> list[SimpleNamespace]:
        return [self.item] if self.item is not None else []

    async def save_analysis(self, vacancy_id, analysis, model_name, **metadata):
        assert self.item is not None
        self.item.analysis = SimpleNamespace(**analysis.model_dump(), **metadata)
        return self.item.analysis


class HappyRanker:
    model_name = "gpt://folder/yandexgpt-5.1"
    provider_name = "yandex"
    prompt_version = "rank-v2"

    def analysis_is_current(self, vacancy, filter_result) -> bool:
        return False

    def input_hash(self, vacancy, filter_result) -> str:
        return "f" * 64

    async def rank(self, vacancy, filter_result) -> VacancyAnalysisResult:
        return VacancyAnalysisResult(
            match_score=88,
            decision="apply",
            role_level="junior",
            matched_skills=["Swift", "SwiftUI"],
            missing_skills=[],
            blocking_requirements=[],
            advantages=["Портфолио"],
            risks=[],
            resume_focus=["iOS-проекты"],
            reason="Хорошее совпадение",
        )


@pytest.mark.asyncio
async def test_successful_search_reports_progress_and_persists_ai_metadata() -> None:
    repository = MemoryVacancyRepository()
    progress: list[str] = []

    async def capture_progress(stage: str) -> None:
        progress.append(stage)

    service = VacancySearchService(
        HappyHHClient(),  # type: ignore[arg-type]
        repository,  # type: ignore[arg-type]
        VacancyFilter(),
        HappyRanker(),  # type: ignore[arg-type]
        min_score=65,
    )

    summary = await service.run(progress=capture_progress)

    assert summary.found == 7
    assert summary.after_deduplication == 1
    assert summary.new_vacancies == 1
    assert summary.after_prefilter == 1
    assert summary.analyzed == 1
    assert summary.suitable == 1
    assert repository.item is not None
    assert repository.item.analysis.provider == "yandex"
    assert repository.item.analysis.input_hash == "f" * 64
    assert any("AI-анализ: 1 из 1" in stage for stage in progress)
