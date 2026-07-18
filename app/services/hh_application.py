from __future__ import annotations

import logging
from dataclasses import dataclass

from app.models import HHApplication, HHResume
from app.repositories.application_repository import ApplicationRepository
from app.repositories.hh_application_repository import HHApplicationRepository
from app.repositories.hh_integration_repository import HHIntegrationRepository
from app.repositories.vacancy_repository import VacancyRepository
from app.schemas import ApplicationResult, HHResumeData, PreparedApplication
from app.services.cover_letter import CoverLetterService
from app.services.hh_oauth import HHOAuthService
from app.services.openai_errors import OpenAIServiceError
from app.sources.hh import (
    HHAPIError,
    HHAuthorizationError,
    HHClient,
    HHRemoteError,
    HHTransportError,
)

logger = logging.getLogger(__name__)
MANUAL_RESUME_EXTERNAL_ID = "__select_on_hh__"
MANUAL_RESUME_TITLE = "Выбрать на сайте HeadHunter"
APPLICATION_ERROR_MESSAGES = {
    "test_required": (
        "test_required",
        "Для отклика требуется пройти тест на HeadHunter.",
    ),
    "resume_not_found": (
        "resume_not_found",
        "Резюме больше недоступно. Выбери его на HeadHunter.",
    ),
    "resume_visibility_conflict": (
        "resume_visibility_conflict",
        "Видимость резюме не позволяет отправить этот отклик.",
    ),
    "invalid_vacancy": (
        "vacancy_unavailable",
        "Вакансия закрыта или больше недоступна.",
    ),
}


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


class HHResumeAccessForbiddenError(HHApplicationError):
    """HH blocks its own-resume endpoint despite valid applicant OAuth."""


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


def _manual_resume_data() -> HHResumeData:
    return HHResumeData(
        external_id=MANUAL_RESUME_EXTERNAL_ID,
        title=MANUAL_RESUME_TITLE,
        status="manual_selection",
        is_default=True,
    )


def _configured_resume_data(external_id: str) -> HHResumeData:
    return HHResumeData(
        external_id=external_id,
        title="Основное резюме HeadHunter",
        status="configured",
        is_default=True,
    )


def _application_error_details(exc: HHRemoteError) -> tuple[str, str]:
    known = APPLICATION_ERROR_MESSAGES.get(exc.error_value or "")
    if known is not None:
        return known
    if exc.status_code == 429:
        return (
            "rate_limit",
            "HeadHunter временно ограничил число запросов. Попробуй позже.",
        )
    if exc.status_code >= 500:
        return (
            "temporary_hh_error",
            "HeadHunter временно недоступен. Попробуй позже.",
        )
    return (
        "application_denied",
        "HeadHunter не разрешил автоматический отклик. Заверши его на сайте.",
    )


class HHApplicationService:
    """Prepares HH applications and enforces confirmation/idempotency.

    Official applicant responses use POST /negotiations/response. Vacancies
    with tests or external forms retain an explicit manual fallback.
    """

    def __init__(
        self,
        *,
        hh_client: HHClient,
        oauth_service: HHOAuthService,
        integration_repository: HHIntegrationRepository,
        application_repository: HHApplicationRepository,
        status_repository: ApplicationRepository,
        vacancy_repository: VacancyRepository,
        cover_letter_service: CoverLetterService,
        confirmation_ttl_seconds: int,
        default_resume_id: str = "",
    ) -> None:
        self.hh_client = hh_client
        self.oauth_service = oauth_service
        self.integration_repository = integration_repository
        self.application_repository = application_repository
        self.status_repository = status_repository
        self.vacancy_repository = vacancy_repository
        self.cover_letter_service = cover_letter_service
        self.confirmation_ttl_seconds = confirmation_ttl_seconds
        self.default_resume_id = default_resume_id.strip()

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
            logger.warning(
                "HeadHunter resume synchronization was rejected",
                extra={
                    "event": "hh_resume_sync_rejected",
                    "status_code": exc.status_code,
                    "error_type": exc.error_type,
                    "error_value": exc.error_value,
                    "request_id": exc.request_id,
                },
            )
            if exc.status_code == 429:
                raise HHRateLimitError from exc
            if exc.status_code == 403 and exc.error_type == "forbidden":
                raise HHResumeAccessForbiddenError from exc
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
        try:
            resumes = await self._sync_resumes(user_id, token)
        except HHResumeAccessForbiddenError:
            if self.default_resume_id:
                logger.warning(
                    "Using configured HeadHunter resume fallback",
                    extra={
                        "event": "hh_configured_resume_fallback",
                        "vacancy_id": vacancy.id,
                    },
                )
                selected_data = _configured_resume_data(self.default_resume_id)
            else:
                logger.warning(
                    "Using manual HeadHunter resume selection fallback",
                    extra={
                        "event": "hh_manual_resume_fallback",
                        "vacancy_id": vacancy.id,
                    },
                )
                selected_data = _manual_resume_data()
            available_resumes: list[HHResumeData] = []
        else:
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
                raise HHResumeSelectionRequired(
                    [_resume_data(item) for item in resumes]
                )
            await self.integration_repository.set_default_resume(
                telegram_user_id=user_id, external_id=selected.external_id
            )
            refreshed_resumes = await self.integration_repository.list_resumes(user_id)
            selected_data = next(
                _resume_data(item)
                for item in refreshed_resumes
                if item.external_id == selected.external_id
            )
            available_resumes = [_resume_data(item) for item in refreshed_resumes]

        existing = await self.application_repository.find_by_identity(
            telegram_user_id=user_id,
            vacancy_external_id=vacancy.external_id,
            resume_external_id=selected_data.external_id,
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
            resume_external_id=selected_data.external_id,
            cover_letter=cover_letter,
        )
        return PreparedApplication(
            draft_id=draft.id,
            vacancy_id=vacancy.id,
            vacancy_title=vacancy.title,
            company=vacancy.company,
            vacancy_url=vacancy.url,
            resume=selected_data,
            resumes=available_resumes,
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
        if draft.resume_external_id == MANUAL_RESUME_EXTERNAL_ID:
            selected_data = _manual_resume_data()
            available_resumes: list[HHResumeData] = []
        else:
            resumes = await self.integration_repository.list_resumes(user_id)
            selected = next(
                (
                    item
                    for item in resumes
                    if item.external_id == draft.resume_external_id
                ),
                None,
            )
            if (
                selected is None
                and draft.resume_external_id == self.default_resume_id
            ):
                selected_data = _configured_resume_data(self.default_resume_id)
                available_resumes = []
            elif selected is None:
                return None
            else:
                selected_data = _resume_data(selected)
                available_resumes = [_resume_data(item) for item in resumes]
        return PreparedApplication(
            draft_id=draft.id,
            vacancy_id=vacancy.id,
            vacancy_title=vacancy.title,
            company=vacancy.company,
            vacancy_url=vacancy.url,
            resume=selected_data,
            resumes=available_resumes,
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

    async def _manual_result(
        self,
        application_id: int,
        *,
        code: str,
        message: str,
        manual_url: str | None,
    ) -> ApplicationResult:
        await self.application_repository.mark_manual_action(
            application_id,
            code=code,
            message=message,
        )
        return ApplicationResult(
            status="manual_action_required",
            message=message,
            manual_url=manual_url,
        )

    async def _submitted_result(
        self,
        application: HHApplication,
        *,
        external_id: str | None,
        message: str,
    ) -> ApplicationResult:
        await self.application_repository.mark_submitted(
            application.id,
            external_id=external_id,
        )
        try:
            await self.status_repository.set_status(application.vacancy_id, "applied")
        except Exception:
            logger.exception(
                "Could not synchronize local applied status",
                extra={
                    "event": "hh_application_status_sync_failed",
                    "application_id": application.id,
                },
            )
        return ApplicationResult(
            status="submitted",
            message=message,
            external_id=external_id,
        )

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
            return await self._manual_result(
                application.id,
                code="vacancy_unavailable",
                message="Вакансия больше недоступна.",
                manual_url=None,
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
            return await self._manual_result(
                application.id,
                code="vacancy_archived",
                message="Вакансия закрыта или находится в архиве.",
                manual_url=manual_url,
            )
        if details.get("response_url"):
            return await self._manual_result(
                application.id,
                code="external_response_form",
                message="Для этой вакансии отклик заполняется на сайте работодателя.",
                manual_url=manual_url,
            )
        if details.get("has_test") or (
            isinstance(details.get("test"), dict)
            and details["test"].get("required")
        ):
            return await self._manual_result(
                application.id,
                code="test_required",
                message=(
                    "Работодатель требует пройти тест. Заверши отклик на сайте "
                    "HeadHunter."
                ),
                manual_url=manual_url,
            )
        if application.resume_external_id == MANUAL_RESUME_EXTERNAL_ID:
            return await self._manual_result(
                application.id,
                code="resume_selection_required",
                message="Открой вакансию и выбери резюме на HeadHunter.",
                manual_url=manual_url,
            )

        try:
            access_token = await self._token(user_id)
            external_id = await self.hh_client.apply_to_vacancy(
                access_token,
                resume_id=application.resume_external_id,
                vacancy_id=vacancy.external_id,
                message=application.cover_letter,
            )
        except (HHNotAuthorizedError, HHAuthorizationError):
            return await self._manual_result(
                application.id,
                code="authorization_expired",
                message="Авторизация HeadHunter истекла. Подключи аккаунт заново.",
                manual_url=manual_url,
            )
        except HHRemoteError as exc:
            if exc.error_type == "negotiations" and exc.error_value == "already_applied":
                return await self._submitted_result(
                    application,
                    external_id=None,
                    message=(
                        "Отклик уже существует на HeadHunter. Локальный статус "
                        "синхронизирован."
                    ),
                )
            code, message = _application_error_details(exc)
            return await self._manual_result(
                application.id,
                code=code,
                message=message,
                manual_url=exc.fallback_url or manual_url,
            )
        except HHTransportError:
            return await self._manual_result(
                application.id,
                code="submission_result_unknown",
                message=(
                    "Соединение оборвалось во время отправки. Проверь отклики на "
                    "HeadHunter перед повторной попыткой."
                ),
                manual_url=manual_url,
            )
        except HHAPIError:
            return await self._manual_result(
                application.id,
                code="hh_request_failed",
                message="Не удалось подтвердить отправку. Проверь отклики на HeadHunter.",
                manual_url=manual_url,
            )

        logger.info(
            "HeadHunter application submitted",
            extra={
                "event": "hh_application_submitted",
                "application_id": application.id,
                "vacancy_id": vacancy.external_id,
            },
        )
        return await self._submitted_result(
            application,
            external_id=external_id,
            message="Отклик успешно отправлен через HeadHunter.",
        )
