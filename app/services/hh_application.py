from __future__ import annotations

from dataclasses import dataclass

from app.models import HHApplication, HHResume
from app.repositories.hh_application_repository import HHApplicationRepository
from app.repositories.hh_integration_repository import HHIntegrationRepository
from app.repositories.vacancy_repository import VacancyRepository
from app.schemas import ApplicationResult, HHResumeData, PreparedApplication
from app.services.cover_letter import CoverLetterService
from app.services.openai_errors import OpenAIServiceError
from app.services.hh_oauth import HHOAuthService
from app.sources.hh import (
    HHAPIError,
    HHAuthorizationError,
    HHClient,
    HHRemoteError,
    HHResumeNotFoundError,
)


class HHApplicationError(RuntimeError):
    user_message = "Не удалось подготовить отклик. Попробуй позже."


class HHNotAuthorizedError(HHApplicationError):
    user_message = "Подключи аккаунт HeadHunter, чтобы продолжить."


class HHNoResumesError(HHApplicationError):
    user_message = "В аккаунте HeadHunter нет доступных резюме."


class HHResumeUnavailableError(HHApplicationError):
    user_message = "Резюме больше недоступно. Выбери другое."


class HHResumeSelectionRequired(HHApplicationError):
    user_message = "Выбери резюме для отклика."

    def __init__(self, resumes: list[HHResumeData]) -> None:
        super().__init__(self.user_message)
        self.resumes = resumes


class HHConfirmationError(HHApplicationError):
    """An application confirmation is invalid or unavailable."""

    user_message = "Черновик недоступен, устарел или уже был обработан."


class HHRateLimitError(HHApplicationError):
    user_message = "HeadHunter временно ограничил число запросов. Попробуй позже."


class HHTemporaryApplicationError(HHApplicationError):
    user_message = "HeadHunter временно недоступен. Попробуй позже."


class HHCoverLetterUnavailableError(HHApplicationError):
    user_message = "Не удалось создать письмо: OpenAI временно недоступен."


@dataclass(slots=True)
class ConfirmationPreview:
    token: str
    application: HHApplication


def _resume_data(row: HHResume) -> HHResumeData:
    return HHResumeData(
        local_id=row.id,
        external_id=row.external_id,
        title=row.title,
        status=row.status,
        url=row.url,
        updated_at=row.external_updated_at,
        is_default=row.is_default,
    )


class HHApplicationService:
    """Prepares HH applications and enforces confirmation/idempotency.

    The current public HH OpenAPI does not document a POST operation for an
    applicant response. Final confirmation therefore returns a manual-action
    result instead of calling an undocumented endpoint.
    """

    def __init__(
        self,
        *,
        hh_client: HHClient,
        oauth_service: HHOAuthService,
        integration_repository: HHIntegrationRepository,
        application_repository: HHApplicationRepository,
        vacancy_repository: VacancyRepository,
        cover_letter_service: CoverLetterService,
        confirmation_ttl_seconds: int,
    ) -> None:
        self.hh_client = hh_client
        self.oauth_service = oauth_service
        self.integration_repository = integration_repository
        self.application_repository = application_repository
        self.vacancy_repository = vacancy_repository
        self.cover_letter_service = cover_letter_service
        self.confirmation_ttl_seconds = confirmation_ttl_seconds

    async def _token(self, user_id: int) -> str:
        try:
            return await self.oauth_service.get_access_token(user_id)
        except HHAuthorizationError as exc:
            raise HHNotAuthorizedError from exc

    async def _sync_resumes(self, user_id: int, token: str) -> list[HHResume]:
        try:
            remote = await self.hh_client.get_my_resumes(token)
        except HHAuthorizationError as exc:
            raise HHNotAuthorizedError from exc
        except HHRemoteError as exc:
            if exc.status_code == 429:
                raise HHRateLimitError from exc
            raise HHTemporaryApplicationError from exc
        except HHAPIError as exc:
            raise HHTemporaryApplicationError from exc
        if not remote:
            raise HHNoResumesError
        return await self.integration_repository.save_resumes(user_id, remote)

    async def prepare_application(
        self,
        *,
        user_id: int,
        vacancy_id: int,
        resume_id: str | None = None,
    ) -> PreparedApplication:
        vacancy = await self.vacancy_repository.get_by_id(vacancy_id)
        if vacancy is None or vacancy.source != "hh":
            raise HHApplicationError("Vacancy is unavailable")
        token = await self._token(user_id)
        resumes = await self._sync_resumes(user_id, token)
        selected = next(
            (item for item in resumes if item.external_id == resume_id), None
        )
        if resume_id and selected is None:
            raise HHResumeUnavailableError
        if selected is None:
            selected = next((item for item in resumes if item.is_default), None)
        if selected is None and len(resumes) == 1:
            selected = resumes[0]
        if selected is None:
            raise HHResumeSelectionRequired([_resume_data(item) for item in resumes])
        try:
            await self.hh_client.get_owned_resume(token, selected.external_id)
        except HHResumeNotFoundError as exc:
            raise HHResumeUnavailableError from exc
        except HHRemoteError as exc:
            if exc.status_code == 429:
                raise HHRateLimitError from exc
            raise HHTemporaryApplicationError from exc
        except HHAPIError as exc:
            raise HHTemporaryApplicationError from exc
        await self.integration_repository.set_default_resume(
            telegram_user_id=user_id, external_id=selected.external_id
        )
        existing = await self.application_repository.find_by_identity(
            telegram_user_id=user_id,
            vacancy_external_id=vacancy.external_id,
            resume_external_id=selected.external_id,
        )
        cover_letter = existing.cover_letter if existing else None
        if not cover_letter:
            try:
                cover_letter = await self.cover_letter_service.generate(vacancy)
            except OpenAIServiceError as exc:
                raise HHCoverLetterUnavailableError from exc
        if not cover_letter:
            raise HHApplicationError("Cover letter generation failed")
        draft = await self.application_repository.save_draft(
            telegram_user_id=user_id,
            vacancy_id=vacancy.id,
            vacancy_external_id=vacancy.external_id,
            resume_external_id=selected.external_id,
            cover_letter=cover_letter,
        )
        refreshed_resumes = await self.integration_repository.list_resumes(user_id)
        selected_data = next(
            _resume_data(item)
            for item in refreshed_resumes
            if item.external_id == selected.external_id
        )
        return PreparedApplication(
            draft_id=draft.id,
            vacancy_id=vacancy.id,
            vacancy_title=vacancy.title,
            company=vacancy.company,
            vacancy_url=vacancy.url,
            resume=selected_data,
            resumes=[_resume_data(item) for item in refreshed_resumes],
            cover_letter=draft.cover_letter,
        )

    async def get_preview(
        self, *, user_id: int, application_id: int
    ) -> PreparedApplication | None:
        draft = await self.application_repository.get_owned(application_id, user_id)
        if draft is None:
            return None
        vacancy = await self.vacancy_repository.get_by_id(draft.vacancy_id)
        if vacancy is None:
            return None
        resumes = await self.integration_repository.list_resumes(user_id)
        selected = next(
            (item for item in resumes if item.external_id == draft.resume_external_id),
            None,
        )
        if selected is None:
            return None
        return PreparedApplication(
            draft_id=draft.id,
            vacancy_id=vacancy.id,
            vacancy_title=vacancy.title,
            company=vacancy.company,
            vacancy_url=vacancy.url,
            resume=_resume_data(selected),
            resumes=[_resume_data(item) for item in resumes],
            cover_letter=draft.cover_letter,
        )

    async def update_cover_letter(
        self, *, user_id: int, application_id: int, cover_letter: str
    ) -> PreparedApplication | None:
        text = cover_letter.strip()
        if not text:
            raise HHApplicationError("Cover letter cannot be empty")
        row = await self.application_repository.update_cover_letter(
            application_id=application_id,
            telegram_user_id=user_id,
            cover_letter=text,
        )
        if row is None:
            return None
        return await self.get_preview(user_id=user_id, application_id=application_id)

    async def create_confirmation(
        self, *, user_id: int, application_id: int
    ) -> ConfirmationPreview:
        token = await self.application_repository.create_confirmation(
            application_id=application_id,
            telegram_user_id=user_id,
            ttl_seconds=self.confirmation_ttl_seconds,
        )
        application = await self.application_repository.get_owned(
            application_id, user_id
        )
        if token is None or application is None:
            raise HHConfirmationError("Черновик недоступен или уже отправлен.")
        return ConfirmationPreview(token=token, application=application)

    async def submit_application(
        self, *, user_id: int, confirmation_token: str
    ) -> ApplicationResult:
        lease = await self.application_repository.acquire_submission(
            raw_token=confirmation_token, telegram_user_id=user_id
        )
        messages = {
            "invalid": "Подтверждение недействительно.",
            "forbidden": "Нельзя подтвердить чужой отклик.",
            "expired": "Подтверждение устарело. Подготовь отклик заново.",
            "used": "Это подтверждение уже использовано.",
            "submitting": "Отклик уже обрабатывается.",
            "submitted": "Отклик уже был отправлен.",
        }
        if lease.outcome != "acquired" or lease.application is None:
            status = "submitted" if lease.outcome == "submitted" else "failed"
            return ApplicationResult(status=status, message=messages[lease.outcome])
        application = lease.application
        vacancy = await self.vacancy_repository.get_by_id(application.vacancy_id)
        if vacancy is None:
            await self.application_repository.mark_manual_action(
                application.id,
                code="vacancy_unavailable",
                message="Вакансия больше недоступна.",
            )
            return ApplicationResult(
                status="manual_action_required",
                message="Вакансия больше недоступна.",
            )
        try:
            access_token = await self._token(user_id)
            await self.hh_client.get_owned_resume(
                access_token, application.resume_external_id
            )
        except HHResumeNotFoundError:
            await self.application_repository.mark_manual_action(
                application.id,
                code="resume_not_found",
                message="Резюме больше недоступно.",
            )
            return ApplicationResult(
                status="manual_action_required",
                message="Резюме больше недоступно. Выбери другое.",
                manual_url=vacancy.url,
            )
        except HHNotAuthorizedError:
            await self.application_repository.mark_manual_action(
                application.id,
                code="authorization_expired",
                message="Авторизация HeadHunter истекла.",
            )
            return ApplicationResult(
                status="manual_action_required",
                message="Авторизация HeadHunter истекла. Подключи аккаунт заново.",
                manual_url=vacancy.url,
            )
        except HHAuthorizationError:
            await self.application_repository.mark_manual_action(
                application.id,
                code="authorization_expired",
                message="Авторизация HeadHunter истекла.",
            )
            return ApplicationResult(
                status="manual_action_required",
                message="Авторизация HeadHunter истекла. Подключи аккаунт заново.",
                manual_url=vacancy.url,
            )
        except HHRemoteError as exc:
            is_rate_limit = exc.status_code == 429
            message = (
                "HeadHunter временно ограничил число запросов. Проверь отклики "
                "на сайте и попробуй позже."
                if is_rate_limit
                else "HeadHunter временно недоступен. Проверь отклики на сайте."
            )
            await self.application_repository.mark_manual_action(
                application.id,
                code="rate_limit" if is_rate_limit else "temporary_hh_error",
                message=message,
            )
            return ApplicationResult(
                status="manual_action_required",
                message=message,
                manual_url=vacancy.url,
            )
        except HHAPIError:
            message = (
                "Не удалось подтвердить результат проверки в HeadHunter. "
                "Проверь отклики на сайте."
            )
            await self.application_repository.mark_manual_action(
                application.id,
                code="hh_request_failed",
                message=message,
            )
            return ApplicationResult(
                status="manual_action_required",
                message=message,
                manual_url=vacancy.url,
            )

        try:
            details = await self.hh_client.get_vacancy(vacancy.external_id)
        except HHAPIError:
            details = {}
        manual_url = str(
            details.get("apply_alternate_url")
            or details.get("response_url")
            or vacancy.url
        )
        if details.get("archived"):
            message = "Вакансия закрыта или находится в архиве."
            code = "vacancy_archived"
        elif details.get("response_url"):
            message = "Для этой вакансии отклик заполняется на сайте работодателя."
            code = "external_response_form"
        elif details.get("has_test") or (
            isinstance(details.get("test"), dict)
            and details["test"].get("required")
        ):
            message = (
                "Работодатель требует пройти тест. Заверши отклик на сайте "
                "HeadHunter."
            )
            code = "test_required"
        else:
            message = (
                "Публичный API HeadHunter сейчас не документирует отправку отклика "
                "соискателя. Открой вакансию и заверши отклик на HeadHunter."
            )
            code = "official_api_submission_unavailable"

        await self.application_repository.mark_manual_action(
            application.id,
            code=code,
            message=message,
        )
        return ApplicationResult(
            status="manual_action_required",
            message=message,
            manual_url=manual_url,
        )
