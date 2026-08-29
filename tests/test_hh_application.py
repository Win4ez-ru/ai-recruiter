from __future__ import annotations

import asyncio
from datetime import timedelta

import httpx
import pytest
import pytest_asyncio

from app.database import Database
from app.models import Application, HHApplication, utc_now
from app.repositories.application_repository import ApplicationRepository
from app.repositories.hh_application_repository import HHApplicationRepository
from app.repositories.hh_integration_repository import HHIntegrationRepository
from app.repositories.vacancy_repository import VacancyRepository
from app.schemas import HHResumeData, VacancyCreate
from app.services.hh_application import (
    MANUAL_RESUME_EXTERNAL_ID,
    HHAlreadyAppliedError,
    HHApplicationService,
    HHNotAuthorizedError,
    HHResumeSelectionRequired,
    HHTemporaryApplicationError,
)
from app.sources.hh import (
    HHAPIError,
    HHAuthorizationError,
    HHRemoteError,
    HHTransportError,
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
        self.resume_list_error: Exception | None = None
        self.application_error: Exception | None = None
        self.application_calls = 0
        self.application_messages: list[str] = []
        self.vacancy_details: dict = {"apply_alternate_url": "https://hh.ru/vacancy/1"}

    async def get_my_resumes(self, token: str) -> list[HHResumeData]:
        if self.resume_list_error is not None:
            raise self.resume_list_error
        return self.resumes

    async def get_vacancy(self, vacancy_id: str) -> dict:
        return {"id": vacancy_id, **self.vacancy_details}

    async def apply_to_vacancy(
        self,
        token: str,
        *,
        resume_id: str,
        vacancy_id: str,
        message: str,
    ) -> str | None:
        self.application_calls += 1
        self.application_messages.append(message)
        if self.application_error is not None:
            raise self.application_error
        return "negotiation-1"


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
    default_resume_id: str = "",
    demo_mode: bool = False,
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
        default_resume_id=default_resume_id,
        demo_mode=demo_mode,
    )
    return service, vacancy.id, application_repository


@pytest.mark.asyncio
async def test_demo_mode_runs_confirmation_without_external_submission(
    database: Database,
) -> None:
    service, vacancy_id, repository = await make_service(database, demo_mode=True)
    preview = await service.prepare_application(user_id=42, vacancy_id=vacancy_id)
    confirmation = await service.create_confirmation(
        user_id=42, application_id=preview.draft_id
    )

    result = await service.submit_application(
        user_id=42, confirmation_token=confirmation.token
    )
    row = await repository.get_owned(preview.draft_id, 42)

    assert result.status == "demo"
    assert "не отправлен" in result.message
    assert service.hh_client.application_calls == 0  # type: ignore[attr-defined]
    assert row is not None and row.error_code == "demo_mode"


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
async def test_prepare_uses_manual_fallback_when_hh_blocks_resume_list(
    database: Database,
) -> None:
    service, vacancy_id, repository = await make_service(database)
    service.hh_client.resume_list_error = HHRemoteError(  # type: ignore[attr-defined]
        403,
        "forbidden",
        request_id="request-123",
    )

    preview = await service.prepare_application(user_id=42, vacancy_id=vacancy_id)
    restored = await service.get_preview(user_id=42, application_id=preview.draft_id)
    confirmation = await service.create_confirmation(
        user_id=42, application_id=preview.draft_id
    )
    result = await service.submit_application(
        user_id=42, confirmation_token=confirmation.token
    )
    row = await repository.get_owned(preview.draft_id, 42)

    assert preview.resume.external_id == MANUAL_RESUME_EXTERNAL_ID
    assert preview.resume.title == "Выбрать на сайте HeadHunter"
    assert preview.resumes == []
    assert preview.manual_submission_required is True
    assert restored is not None
    assert restored.resume.external_id == MANUAL_RESUME_EXTERNAL_ID
    assert restored.manual_submission_required is True
    assert service.hh_client.application_calls == 0  # type: ignore[attr-defined]
    assert result.status == "manual_action_required"
    assert result.manual_url == "https://hh.ru/vacancy/1"
    assert row is not None
    assert row.resume_external_id == MANUAL_RESUME_EXTERNAL_ID


@pytest.mark.asyncio
async def test_configured_resume_fallback_can_submit(
    database: Database,
) -> None:
    service, vacancy_id, repository = await make_service(
        database,
        default_resume_id="configured-resume",
    )
    service.hh_client.resume_list_error = HHRemoteError(  # type: ignore[attr-defined]
        403,
        "forbidden",
    )

    preview = await service.prepare_application(user_id=42, vacancy_id=vacancy_id)
    restored = await service.get_preview(user_id=42, application_id=preview.draft_id)
    confirmation = await service.create_confirmation(
        user_id=42, application_id=preview.draft_id
    )
    result = await service.submit_application(
        user_id=42, confirmation_token=confirmation.token
    )
    row = await repository.get_owned(preview.draft_id, 42)

    assert preview.resume.external_id == "configured-resume"
    assert preview.manual_submission_required is False
    assert restored is not None
    assert restored.resume.external_id == "configured-resume"
    assert result.status == "submitted"
    assert row is not None and row.api_status == "submitted"


@pytest.mark.asyncio
async def test_prepare_does_not_fallback_for_other_hh_failures(
    database: Database,
) -> None:
    service, vacancy_id, _ = await make_service(database)
    service.hh_client.resume_list_error = HHRemoteError(  # type: ignore[attr-defined]
        503,
        "service_unavailable",
        request_id="request-456",
    )

    with pytest.raises(HHTemporaryApplicationError):
        await service.prepare_application(user_id=42, vacancy_id=vacancy_id)


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
async def test_final_confirmation_submits_only_once(
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

    assert first.status == "submitted"
    assert first.external_id == "negotiation-1"
    assert second.status == "submitted"
    assert row is not None and row.attempts == 1
    assert row.api_status == "submitted"
    assert row.external_application_id == "negotiation-1"
    assert service.hh_client.application_calls == 1  # type: ignore[attr-defined]


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

    assert sum(result.status == "submitted" for result in results) >= 1
    assert service.hh_client.application_calls == 1  # type: ignore[attr-defined]
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
async def test_successful_submission_marks_vacancy_applied(database: Database) -> None:
    service, vacancy_id, _ = await make_service(database)
    preview = await service.prepare_application(user_id=42, vacancy_id=vacancy_id)
    confirmation = await service.create_confirmation(
        user_id=42, application_id=preview.draft_id
    )
    await service.submit_application(user_id=42, confirmation_token=confirmation.token)

    async with database.session_factory() as session:
        legacy = await session.get(Application, vacancy_id)
        hh_application = await session.get(HHApplication, preview.draft_id)
    assert legacy is not None and legacy.status == "applied_bot"
    assert legacy.application_source == "bot"
    assert legacy.applied_at is not None
    assert hh_application is not None
    assert hh_application.confirmed_at is not None
    assert hh_application.submitting_at is not None
    assert hh_application.submitted_at is not None


@pytest.mark.asyncio
async def test_successful_submission_overrides_concurrent_hidden_status(
    database: Database,
) -> None:
    service, vacancy_id, _ = await make_service(database)
    preview = await service.prepare_application(user_id=42, vacancy_id=vacancy_id)
    confirmation = await service.create_confirmation(
        user_id=42, application_id=preview.draft_id
    )
    status_repository = ApplicationRepository(database)
    await status_repository.hide(vacancy_id)

    result = await service.submit_application(
        user_id=42,
        confirmation_token=confirmation.token,
    )
    lifecycle = await status_repository.get(vacancy_id)

    assert result.status == "submitted"
    assert result.vacancy_id == vacancy_id
    assert lifecycle is not None and lifecycle.status == "applied_bot"
    assert lifecycle.application_source == "bot"


@pytest.mark.asyncio
async def test_startup_reconciles_legacy_submitted_row(
    database: Database,
) -> None:
    service, vacancy_id, repository = await make_service(database)
    first = await service.prepare_application(user_id=42, vacancy_id=vacancy_id)
    await repository.mark_submitted(first.draft_id, external_id="legacy-response")

    repaired, recovered = await repository.reconcile_incomplete_finalizations(
        stale_before=utc_now() - timedelta(minutes=5)
    )
    lifecycle = await ApplicationRepository(database).get(vacancy_id)

    assert (repaired, recovered) == (1, 0)
    assert lifecycle is not None and lifecycle.status == "applied_bot"
    assert lifecycle.application_source == "bot"


@pytest.mark.asyncio
async def test_startup_quarantines_abandoned_submission_lease(
    database: Database,
) -> None:
    service, vacancy_id, repository = await make_service(database)
    preview = await service.prepare_application(user_id=42, vacancy_id=vacancy_id)
    confirmation = await service.create_confirmation(
        user_id=42, application_id=preview.draft_id
    )
    lease = await repository.acquire_submission(
        raw_token=confirmation.token, telegram_user_id=42
    )
    assert lease.outcome == "acquired"

    repaired, recovered = await repository.reconcile_incomplete_finalizations(
        stale_before=utc_now() + timedelta(seconds=1)
    )
    abandoned = await repository.get_owned(preview.draft_id, 42)

    assert (repaired, recovered) == (0, 1)
    assert abandoned is not None
    assert abandoned.api_status == "manual_action_required"
    assert abandoned.error_code == "submission_result_unknown"


@pytest.mark.asyncio
async def test_registered_manual_application_blocks_stale_bot_action(
    database: Database,
) -> None:
    service, vacancy_id, _ = await make_service(database)
    await ApplicationRepository(database).mark_applied_manual(vacancy_id)

    with pytest.raises(HHAlreadyAppliedError):
        await service.prepare_application(user_id=42, vacancy_id=vacancy_id)


@pytest.mark.asyncio
async def test_already_applied_is_treated_as_submitted(database: Database) -> None:
    service, vacancy_id, repository = await make_service(database)
    service.hh_client.application_error = HHRemoteError(  # type: ignore[attr-defined]
        403,
        "negotiations",
        "already_applied",
    )
    preview = await service.prepare_application(user_id=42, vacancy_id=vacancy_id)
    confirmation = await service.create_confirmation(
        user_id=42, application_id=preview.draft_id
    )

    result = await service.submit_application(
        user_id=42, confirmation_token=confirmation.token
    )
    row = await repository.get_owned(preview.draft_id, 42)
    lifecycle = await ApplicationRepository(database).get(vacancy_id)

    assert result.status == "submitted"
    assert "уже существует" in result.message
    assert row is not None and row.api_status == "submitted"
    assert lifecycle is not None and lifecycle.status == "applied_manual"
    assert lifecycle.status_source == "import"
    assert lifecycle.application_source == "manual"


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

    repeated = await service.submit_application(
        user_id=42, confirmation_token=confirmation.token
    )
    assert repeated.status == "manual_action_required"
    assert repeated.can_retry is False
    assert repeated.can_mark_applied is True
    assert repeated.manual_url == "https://hh.ru/vacancy/1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_code", "expected_status", "can_retry"),
    [
        (
            HHRemoteError(403, "negotiations", "resume_not_found"),
            "resume_not_found",
            "manual_action_required",
            False,
        ),
        (HHRemoteError(429, "rate_limit"), "rate_limit", "failed", True),
        (
            HHRemoteError(503, "service_unavailable"),
            "temporary_hh_error",
            "failed",
            True,
        ),
        (
            HHTransportError("unknown result"),
            "submission_result_unknown",
            "manual_action_required",
            False,
        ),
        (HHAPIError("timeout"), "hh_request_failed", "manual_action_required", False),
    ],
)
async def test_submission_failures_use_safe_manual_result(
    database: Database,
    error: Exception,
    expected_code: str,
    expected_status: str,
    can_retry: bool,
) -> None:
    service, vacancy_id, repository = await make_service(database)
    preview = await service.prepare_application(user_id=42, vacancy_id=vacancy_id)
    service.hh_client.application_error = error  # type: ignore[attr-defined]
    confirmation = await service.create_confirmation(
        user_id=42, application_id=preview.draft_id
    )

    result = await service.submit_application(
        user_id=42, confirmation_token=confirmation.token
    )
    row = await repository.get_owned(preview.draft_id, 42)

    assert result.status == expected_status
    assert result.application_id == preview.draft_id
    assert result.can_retry is can_retry
    assert result.manual_url == "https://hh.ru/vacancy/1"
    assert row is not None and row.error_code == expected_code
    assert row.api_status == expected_status
    assert row.cover_letter == preview.cover_letter
    assert row.resume_external_id == preview.resume.external_id


@pytest.mark.asyncio
async def test_retry_reuses_edited_cover_letter_and_selected_resume(
    database: Database,
) -> None:
    service, vacancy_id, repository = await make_service(database)
    preview = await service.prepare_application(user_id=42, vacancy_id=vacancy_id)
    edited_letter = "Отредактированное письмо, которое нельзя потерять."
    updated = await service.update_cover_letter(
        user_id=42,
        application_id=preview.draft_id,
        cover_letter=edited_letter,
    )
    assert updated is not None
    service.hh_client.application_error = HHRemoteError(  # type: ignore[attr-defined]
        503, "service_unavailable"
    )

    first_confirmation = await service.create_confirmation(
        user_id=42, application_id=preview.draft_id
    )
    failed = await service.submit_application(
        user_id=42, confirmation_token=first_confirmation.token
    )
    saved_after_failure = await repository.get_owned(preview.draft_id, 42)

    assert failed.status == "failed"
    assert failed.can_retry is True
    assert saved_after_failure is not None
    assert saved_after_failure.cover_letter == edited_letter
    assert saved_after_failure.resume_external_id == "resume-1"

    service.hh_client.application_error = None  # type: ignore[attr-defined]
    retry_confirmation = await service.create_confirmation(
        user_id=42, application_id=preview.draft_id
    )
    submitted = await service.submit_application(
        user_id=42, confirmation_token=retry_confirmation.token
    )
    client = service.hh_client

    assert submitted.status == "submitted"
    assert client.application_messages == [  # type: ignore[attr-defined]
        edited_letter,
        edited_letter,
    ]


@pytest.mark.asyncio
async def test_oauth_recovery_preserves_draft_before_success(
    database: Database,
) -> None:
    service, vacancy_id, repository = await make_service(database)
    preview = await service.prepare_application(user_id=42, vacancy_id=vacancy_id)
    oauth = service.oauth_service
    oauth.token = None  # type: ignore[attr-defined]
    confirmation = await service.create_confirmation(
        user_id=42, application_id=preview.draft_id
    )

    unauthorized = await service.submit_application(
        user_id=42, confirmation_token=confirmation.token
    )
    preserved = await repository.get_owned(preview.draft_id, 42)

    assert unauthorized.status == "failed"
    assert unauthorized.requires_oauth is True
    assert unauthorized.can_retry is False
    assert preserved is not None
    assert preserved.cover_letter == preview.cover_letter
    assert preserved.resume_external_id == preview.resume.external_id

    repeated = await service.submit_application(
        user_id=42, confirmation_token=confirmation.token
    )
    assert repeated.requires_oauth is True
    assert repeated.can_retry is False
    assert repeated.can_mark_applied is True

    oauth.token = "fresh-token"  # type: ignore[attr-defined]
    resumed = await service.prepare_application(user_id=42, vacancy_id=vacancy_id)
    assert resumed.draft_id == preview.draft_id
    assert resumed.cover_letter == preview.cover_letter
    assert service.cover_letter_service.calls == 1  # type: ignore[attr-defined]
    retry_confirmation = await service.create_confirmation(
        user_id=42, application_id=preview.draft_id
    )
    submitted = await service.submit_application(
        user_id=42, confirmation_token=retry_confirmation.token
    )

    assert submitted.status == "submitted"


@pytest.mark.asyncio
async def test_unknown_submission_result_never_offers_automatic_retry(
    database: Database,
) -> None:
    service, vacancy_id, _ = await make_service(database)
    preview = await service.prepare_application(user_id=42, vacancy_id=vacancy_id)
    service.hh_client.application_error = HHTransportError(  # type: ignore[attr-defined]
        "connection lost after POST"
    )
    confirmation = await service.create_confirmation(
        user_id=42, application_id=preview.draft_id
    )

    result = await service.submit_application(
        user_id=42, confirmation_token=confirmation.token
    )

    assert result.status == "manual_action_required"
    assert result.result_uncertain is True
    assert result.can_retry is False
    assert result.can_mark_applied is True

    repeated = await service.submit_application(
        user_id=42, confirmation_token=confirmation.token
    )
    assert repeated.result_uncertain is True
    assert repeated.can_retry is False


@pytest.mark.asyncio
async def test_connection_failure_is_retryable_because_post_was_not_sent(
    database: Database,
) -> None:
    service, vacancy_id, repository = await make_service(database)
    preview = await service.prepare_application(user_id=42, vacancy_id=vacancy_id)
    transport_error = HHTransportError("could not connect")
    transport_error.__cause__ = httpx.ConnectError("connection refused")
    service.hh_client.application_error = transport_error  # type: ignore[attr-defined]
    confirmation = await service.create_confirmation(
        user_id=42, application_id=preview.draft_id
    )

    result = await service.submit_application(
        user_id=42, confirmation_token=confirmation.token
    )
    preserved = await repository.get_owned(preview.draft_id, 42)

    assert result.status == "failed"
    assert result.error_code == "hh_connection_failed"
    assert result.can_retry is True
    assert result.result_uncertain is False
    assert preserved is not None and preserved.cover_letter == preview.cover_letter
