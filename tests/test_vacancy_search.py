from __future__ import annotations

import pytest

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
