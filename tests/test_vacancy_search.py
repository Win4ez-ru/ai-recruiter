from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.database import Database
from app.repositories.vacancy_repository import VacancyRepository
from app.schemas import VacancyAnalysisResult
from app.services.ai_errors import AIResponseValidationError
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
        return bool(
            vacancy.analysis is not None
            and vacancy.analysis.provider == self.provider_name
            and vacancy.analysis.prompt_version == self.prompt_version
            and vacancy.analysis.input_hash == self.input_hash(vacancy, filter_result)
        )

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
    assert summary.ai_requests == 1
    assert any("1/1" in stage for stage in progress)


class MultiHHClient:
    async def search_vacancies(
        self, query: str, *, max_results: int = 100
    ) -> list[dict]:
        return [
            {
                "id": f"concurrent-{index}",
                "name": "Junior iOS Developer",
                "alternate_url": f"https://hh.ru/vacancy/concurrent-{index}",
                "employer": {"name": f"Company {index}"},
                "area": {"name": "Москва"},
                "snippet": {
                    "requirement": "Swift, SwiftUI",
                    "responsibility": "Разработка iOS-приложения",
                },
            }
            for index in range(6)
        ]

    async def get_vacancy(self, external_id: str) -> dict:
        return {
            "id": external_id,
            "name": "Junior iOS Developer",
            "alternate_url": f"https://hh.ru/vacancy/{external_id}",
            "description": "Swift, SwiftUI, junior iOS",
            "employer": {"name": external_id},
            "area": {"name": "Москва"},
            "experience": {"name": "1–3 года"},
            "employment": {"name": "Полная занятость"},
            "schedule": {"name": "Удалённая работа"},
            "key_skills": [{"name": "Swift"}],
        }


class MultiMemoryRepository:
    def __init__(self) -> None:
        self.items: dict[str, SimpleNamespace] = {}

    async def external_ids_needing_refresh(self, external_ids, **kwargs) -> set[str]:
        return set(external_ids) - set(self.items)

    async def create_if_new(self, data):
        item = SimpleNamespace(
            id=len(self.items) + 1,
            analysis=None,
            application=None,
            **data.model_dump(),
        )
        self.items[data.external_id] = item
        return item, True

    async def list_by_external_ids(self, external_ids) -> list[SimpleNamespace]:
        return [self.items[item] for item in external_ids if item in self.items]

    async def save_analysis(self, vacancy_id, analysis, model_name, **metadata):
        item = next(item for item in self.items.values() if item.id == vacancy_id)
        item.analysis = SimpleNamespace(**analysis.model_dump(), **metadata)
        return item.analysis


class ConcurrentRanker(HappyRanker):
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0
        self.calls = 0

    def analysis_is_current(self, vacancy, filter_result) -> bool:
        return vacancy.analysis is not None

    async def rank(self, vacancy, filter_result) -> VacancyAnalysisResult:
        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        return await super().rank(vacancy, filter_result)


@pytest.mark.asyncio
async def test_yandex_ranking_uses_bounded_concurrency_and_then_cache() -> None:
    repository = MultiMemoryRepository()
    ranker = ConcurrentRanker()
    service = VacancySearchService(
        MultiHHClient(),  # type: ignore[arg-type]
        repository,  # type: ignore[arg-type]
        VacancyFilter(),
        ranker,  # type: ignore[arg-type]
        min_score=65,
        ranking_concurrency=3,
        search_queries=["Swift"],
    )

    first = await service.run()
    second = await service.run()

    assert first.analyzed == 6
    assert first.ai_requests == 6
    assert ranker.max_active == 3
    assert ranker.calls == 6
    assert second.ai_requests == 0
    assert second.cached_analyses == 6
    assert second.suitable == 6


class PartiallyMalformedRanker(ConcurrentRanker):
    async def rank(self, vacancy, filter_result) -> VacancyAnalysisResult:
        if vacancy.external_id == "concurrent-2":
            self.calls += 1
            raise AIResponseValidationError("invalid structured output")
        return await super().rank(vacancy, filter_result)


@pytest.mark.asyncio
async def test_one_malformed_ai_response_does_not_abort_other_vacancies() -> None:
    ranker = PartiallyMalformedRanker()
    service = VacancySearchService(
        MultiHHClient(),  # type: ignore[arg-type]
        MultiMemoryRepository(),  # type: ignore[arg-type]
        VacancyFilter(),
        ranker,  # type: ignore[arg-type]
        min_score=65,
        ranking_concurrency=3,
        search_queries=["Swift"],
    )

    summary = await service.run()

    assert summary.ai_requests == 6
    assert summary.analyzed == 5
    assert summary.errors == 1
    assert summary.error_codes == ["ai_invalid_response"]


@pytest.mark.asyncio
async def test_concurrent_ranking_persists_safely_to_sqlite(tmp_path) -> None:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'concurrent.db'}")
    await database.create_tables()
    repository = VacancyRepository(database)
    ranker = ConcurrentRanker()
    service = VacancySearchService(
        MultiHHClient(),  # type: ignore[arg-type]
        repository,
        VacancyFilter(),
        ranker,  # type: ignore[arg-type]
        min_score=65,
        ranking_concurrency=3,
        search_queries=["Swift"],
    )

    try:
        summary = await service.run()
        ranked = await repository.list_digest_candidates(
            65,
            10,
            only_unsent=False,
            **service.analysis_scope,
        )
    finally:
        await database.close()

    assert summary.analyzed == 6
    assert summary.errors == 0
    assert len(ranked) == 6


@pytest.mark.asyncio
async def test_one_analysis_save_failure_does_not_rollback_other_results() -> None:
    class PartiallyFailingRepository(MultiMemoryRepository):
        async def save_analysis(self, vacancy_id, analysis, model_name, **metadata):
            if vacancy_id == 3:
                raise RuntimeError("simulated isolated write failure")
            return await super().save_analysis(
                vacancy_id, analysis, model_name, **metadata
            )

    repository = PartiallyFailingRepository()
    service = VacancySearchService(
        MultiHHClient(),  # type: ignore[arg-type]
        repository,  # type: ignore[arg-type]
        VacancyFilter(),
        ConcurrentRanker(),  # type: ignore[arg-type]
        min_score=65,
        ranking_concurrency=3,
        search_queries=["Swift"],
    )

    summary = await service.run()

    assert summary.ai_requests == 6
    assert summary.analyzed == 5
    assert summary.errors == 1
    assert sum(item.analysis is not None for item in repository.items.values()) == 5
