from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio

from app.database import Database
from app.models import Application, HHApplication
from app.repositories.hh_application_repository import HHApplicationRepository
from app.repositories.hh_integration_repository import HHIntegrationRepository
from app.repositories.vacancy_repository import VacancyRepository
from app.schemas import HHResumeData, VacancyCreate
from app.services.hh_application import (
    HHApplicationService,
    HHNotAuthorizedError,
    HHResumeSelectionRequired,
)
from app.sources.hh import (
    HHAPIError,
    HHAuthorizationError,
    HHRemoteError,
    HHResumeNotFoundError,
)


@pytest_asyncio.fixture
async def database() -> Database:
    db = Database("sqlite+aiosqlite:///:memory:")
    await db.create_tables()
    yield db
    await db.close()


class FakeOAuth:
    def __init__(self, token: str | None = "token") -> None:
        self.token = token

    async def get_access_token(self, user_id: int) -> str:
        if self.token is None:
            raise HHAuthorizationError("missing")
        return self.token


class FakeHHClient:
    def __init__(self, resumes: list[HHResumeData]) -> None:
        self.resumes = resumes
        self.resume_checks = 0
        self.resume_error: Exception | None = None
        self.vacancy_details: dict = {
            "apply_alternate_url": "https://hh.ru/vacancy/1"
        }

    async def get_my_resumes(self, token: str) -> list[HHResumeData]:
        return self.resumes

    async def get_owned_resume(self, token: str, resume_id: str) -> dict:
        self.resume_checks += 1
        if self.resume_error is not None:
            raise self.resume_error
        return {"id": resume_id}

    async def get_vacancy(self, vacancy_id: str) -> dict:
        return {"id": vacancy_id, **self.vacancy_details}


class FakeCoverLetter:
    calls = 0

    async def generate(self, vacancy: object) -> str:
        self.calls += 1
        return "Письмо под конкретную вакансию с предложением показать проекты и код."


async def make_service(
    database: Database,
    *,
    resumes: list[HHResumeData] | None = None,
    oauth_token: str | None = "token",
) -> tuple[HHApplicationService, int, HHApplicationRepository]:
    vacancy_repository = VacancyRepository(database)
    vacancy, _ = await vacancy_repository.create_if_new(
        VacancyCreate(
            external_id="vacancy-1",
            title="iOS Developer",
            company="Acme",
            url="https://hh.ru/vacancy/1",
        )
    )
    integration_repository = HHIntegrationRepository(database)
    application_repository = HHApplicationRepository(database)
    client = FakeHHClient(
        resumes
        or [
            HHResumeData(
                external_id="resume-1",
                title="iOS Developer",
                status="published",
            )
        ]
    )
    service = HHApplicationService(
        hh_client=client,  # type: ignore[arg-type]
        oauth_service=FakeOAuth(oauth_token),  # type: ignore[arg-type]
        integration_repository=integration_repository,
        application_repository=application_repository,
        vacancy_repository=vacancy_repository,
        cover_letter_service=FakeCoverLetter(),  # type: ignore[arg-type]
        confirmation_ttl_seconds=900,
    )
    return service, vacancy.id, application_repository


@pytest.mark.asyncio
async def test_prepare_application_creates_draft(database: Database) -> None:
    service, vacancy_id, repository = await make_service(database)
    preview = await service.prepare_application(user_id=42, vacancy_id=vacancy_id)

    assert preview.resume.external_id == "resume-1"
    assert preview.resume.local_id is not None
    assert preview.cover_letter
    row = await repository.get_owned(preview.draft_id, 42)
    assert row is not None and row.api_status == "draft"


@pytest.mark.asyncio
async def test_prepare_requires_oauth(database: Database) -> None:
    service, vacancy_id, _ = await make_service(database, oauth_token=None)
    with pytest.raises(HHNotAuthorizedError):
        await service.prepare_application(user_id=42, vacancy_id=vacancy_id)


@pytest.mark.asyncio
async def test_prepare_requires_resume_choice_when_multiple(database: Database) -> None:
    service, vacancy_id, _ = await make_service(
        database,
        resumes=[
            HHResumeData(external_id="one", title="First"),
            HHResumeData(external_id="two", title="Second"),
        ],
    )
    with pytest.raises(HHResumeSelectionRequired) as exc_info:
        await service.prepare_application(user_id=42, vacancy_id=vacancy_id)
    assert len(exc_info.value.resumes) == 2


@pytest.mark.asyncio
async def test_first_confirmation_does_not_finalize_application(
    database: Database,
) -> None:
    service, vacancy_id, repository = await make_service(database)
    preview = await service.prepare_application(user_id=42, vacancy_id=vacancy_id)
    confirmation = await service.create_confirmation(
        user_id=42, application_id=preview.draft_id
    )
    row = await repository.get_owned(preview.draft_id, 42)

    assert confirmation.token
    assert row is not None and row.api_status == "awaiting_confirmation"


@pytest.mark.asyncio
async def test_final_confirmation_is_one_time_and_uses_manual_fallback(
    database: Database,
) -> None:
    service, vacancy_id, repository = await make_service(database)
    preview = await service.prepare_application(user_id=42, vacancy_id=vacancy_id)
    confirmation = await service.create_confirmation(
        user_id=42, application_id=preview.draft_id
    )

    first = await service.submit_application(
        user_id=42, confirmation_token=confirmation.token
    )
    second = await service.submit_application(
        user_id=42, confirmation_token=confirmation.token
    )
    row = await repository.get_owned(preview.draft_id, 42)

    assert first.status == "manual_action_required"
    assert first.manual_url == "https://hh.ru/vacancy/1"
    assert second.status == "failed"
    assert row is not None and row.attempts == 1
    assert row.api_status == "manual_action_required"


@pytest.mark.asyncio
async def test_concurrent_confirmation_acquires_only_one_submission(
    database: Database,
) -> None:
    service, vacancy_id, repository = await make_service(database)
    preview = await service.prepare_application(user_id=42, vacancy_id=vacancy_id)
    confirmation = await service.create_confirmation(
        user_id=42, application_id=preview.draft_id
    )

    results = await asyncio.gather(
        service.submit_application(user_id=42, confirmation_token=confirmation.token),
        service.submit_application(user_id=42, confirmation_token=confirmation.token),
    )
    row = await repository.get_owned(preview.draft_id, 42)

    assert sum(result.status == "manual_action_required" for result in results) == 1
    assert row is not None and row.attempts == 1


@pytest.mark.asyncio
async def test_expired_and_foreign_confirmation_are_rejected(
    database: Database,
) -> None:
    service, vacancy_id, repository = await make_service(database)
    preview = await service.prepare_application(user_id=42, vacancy_id=vacancy_id)
    foreign = await service.create_confirmation(
        user_id=42, application_id=preview.draft_id
    )
    result = await service.submit_application(
        user_id=99, confirmation_token=foreign.token
    )
    assert result.status == "failed"

    expired = await repository.create_confirmation(
        application_id=preview.draft_id,
        telegram_user_id=42,
        ttl_seconds=-1,
    )
    assert expired is not None
    expired_result = await service.submit_application(
        user_id=42, confirmation_token=expired
    )
    row = await repository.get_owned(preview.draft_id, 42)
    assert expired_result.status == "failed"
    assert row is not None and row.attempts == 0


@pytest.mark.asyncio
async def test_unique_identity_reuses_single_draft(database: Database) -> None:
    service, vacancy_id, repository = await make_service(database)
    first = await service.prepare_application(user_id=42, vacancy_id=vacancy_id)
    second = await service.prepare_application(user_id=42, vacancy_id=vacancy_id)

    assert first.draft_id == second.draft_id
    assert (
        await repository.count_for_identity(
            telegram_user_id=42,
            vacancy_external_id="vacancy-1",
            resume_external_id="resume-1",
        )
        == 1
    )


@pytest.mark.asyncio
async def test_manual_fallback_does_not_mark_vacancy_applied(database: Database) -> None:
    service, vacancy_id, _ = await make_service(database)
    preview = await service.prepare_application(user_id=42, vacancy_id=vacancy_id)
    confirmation = await service.create_confirmation(
        user_id=42, application_id=preview.draft_id
    )
    await service.submit_application(user_id=42, confirmation_token=confirmation.token)

    async with database.session_factory() as session:
        legacy = await session.get(Application, vacancy_id)
        hh_application = await session.get(HHApplication, preview.draft_id)
    assert legacy is None or legacy.status != "applied"
    assert hh_application is not None
    assert hh_application.confirmed_at is not None
    assert hh_application.submitting_at is not None
    assert hh_application.submitted_at is None


@pytest.mark.asyncio
async def test_required_test_uses_manual_action_message(database: Database) -> None:
    service, vacancy_id, repository = await make_service(database)
    service.hh_client.vacancy_details = {  # type: ignore[attr-defined]
        "has_test": True,
        "apply_alternate_url": "https://hh.ru/applicant/vacancy_response?vacancyId=1",
    }
    preview = await service.prepare_application(user_id=42, vacancy_id=vacancy_id)
    confirmation = await service.create_confirmation(
        user_id=42, application_id=preview.draft_id
    )

    result = await service.submit_application(
        user_id=42, confirmation_token=confirmation.token
    )
    row = await repository.get_owned(preview.draft_id, 42)

    assert result.status == "manual_action_required"
    assert "пройти тест" in result.message
    assert row is not None and row.error_code == "test_required"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (HHResumeNotFoundError("missing"), "resume_not_found"),
        (HHRemoteError(429, "rate_limit"), "rate_limit"),
        (HHRemoteError(503, "service_unavailable"), "temporary_hh_error"),
        (HHAPIError("timeout"), "hh_request_failed"),
    ],
)
async def test_final_preflight_failures_use_safe_manual_result(
    database: Database, error: Exception, expected_code: str
) -> None:
    service, vacancy_id, repository = await make_service(database)
    preview = await service.prepare_application(user_id=42, vacancy_id=vacancy_id)
    service.hh_client.resume_error = error  # type: ignore[attr-defined]
    confirmation = await service.create_confirmation(
        user_id=42, application_id=preview.draft_id
    )

    result = await service.submit_application(
        user_id=42, confirmation_token=confirmation.token
    )
    row = await repository.get_owned(preview.draft_id, 42)

    assert result.status == "manual_action_required"
    assert result.manual_url == "https://hh.ru/vacancy/1"
    assert row is not None and row.error_code == expected_code
    assert row.api_status == "manual_action_required"
