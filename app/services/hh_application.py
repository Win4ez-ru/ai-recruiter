from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.models import HHApplication, HHResume
from app.repositories.hh_application_repository import HHApplicationRepository
from app.repositories.hh_integration_repository import HHIntegrationRepository
from app.repositories.vacancy_repository import VacancyRepository
from app.schemas import ApplicationResult, HHResumeData, PreparedApplication
from app.services.ai_errors import AIServiceError
from app.services.cover_letter import CoverLetterService
from app.services.hh_oauth import HHOAuthService
from app.sources.hh import (
    HHAPIError,
    HHAuthorizationError,
    HHClient,
    HHRemoteError,
    HHTransportError,
)
from app.vacancy_status import has_registered_application

logger = logging.getLogger(__name__)
MANUAL_RESUME_EXTERNAL_ID = "__select_on_hh__"
MANUAL_RESUME_TITLE = "Выбрать на сайте HeadHunter"
APPLICATION_ERROR_POLICIES = {
    "test_required": (
        "test_required",
        "Для отклика требуется пройти тест на HeadHunter.",
        False,
        True,
    ),
    "resume_not_found": (
        "resume_not_found",
        "Резюме больше недоступно. Выберите его на HeadHunter.",
        False,
        True,
    ),
    "resume_visibility_conflict": (
        "resume_visibility_conflict",
        "Видимость резюме не позволяет отправить этот отклик.",
        False,
        True,
    ),
    "invalid_vacancy": (
        "vacancy_unavailable",
        "Вакансия закрыта или больше недоступна.",
        False,
        False,
    ),
}
RETRYABLE_APPLICATION_ERRORS = frozenset(
    {"rate_limit", "temporary_hh_error", "hh_connection_failed"}
)
MANUAL_COMPLETION_ERRORS = frozenset(
    {
        "application_denied",
        "authorization_expired",
        "external_response_form",
        "hh_connection_failed",
        "hh_request_failed",
        "rate_limit",
        "resume_not_found",
        "resume_selection_required",
        "resume_visibility_conflict",
        "submission_result_unknown",
        "temporary_hh_error",
        "test_required",
    }
)
UNCERTAIN_APPLICATION_ERRORS = frozenset(
    {"hh_request_failed", "submission_result_unknown"}
)


class HHApplicationError(RuntimeError):
    user_message = "Не удалось подготовить отклик. Попробуйте позже."


class HHNotAuthorizedError(HHApplicationError):
    user_message = "Подключите аккаунт HeadHunter, чтобы продолжить."


class HHAlreadyAppliedError(HHApplicationError):
    user_message = "Отклик на эту вакансию уже зарегистрирован."


class HHNoResumesError(HHApplicationError):
    user_message = "В аккаунте HeadHunter нет доступных резюме."


class HHResumeUnavailableError(HHApplicationError):
    user_message = "Резюме больше недоступно. Выберите другое."


class HHResumeSelectionRequired(HHApplicationError):
    user_message = "Выберите резюме для отклика."

    def __init__(self, resumes: list[HHResumeData]) -> None:
        super().__init__(self.user_message)
        self.resumes = resumes


class HHConfirmationError(HHApplicationError):
    """An application confirmation is invalid or unavailable."""

    user_message = "Черновик недоступен, устарел или уже был обработан."


class HHRateLimitError(HHApplicationError):
    user_message = "HeadHunter временно ограничил число запросов. Попробуйте позже."


class HHTemporaryApplicationError(HHApplicationError):
    user_message = "HeadHunter временно недоступен. Попробуйте позже."


class HHResumeAccessForbiddenError(HHApplicationError):
    """HH blocks its own-resume endpoint despite valid applicant OAuth."""


class HHCoverLetterUnavailableError(HHApplicationError):
    user_message = "Не удалось создать письмо: AI-модель временно недоступна."


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


def _application_error_details(
    exc: HHRemoteError,
) -> tuple[str, str, bool, bool]:
    """Return code, message, retryability and manual-completion availability."""

    known = APPLICATION_ERROR_POLICIES.get(exc.error_value or "")
    if known is not None:
        return known
    if exc.status_code == 429:
        return (
            "rate_limit",
            "HeadHunter временно ограничил число запросов. Попробуйте позже.",
            True,
            True,
        )
    if exc.status_code >= 500:
        return (
            "temporary_hh_error",
            "HeadHunter временно недоступен. Попробуйте позже.",
            True,
            True,
        )
    return (
        "application_denied",
        "HeadHunter не разрешил автоматический отклик. Завершите его на сайте.",
        False,
        True,
    )


def _transport_failure_is_safe_to_retry(exc: HHTransportError) -> bool:
    """Connect/pool failures happen before the non-idempotent POST is accepted."""

    return isinstance(
        exc.__cause__,
        (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout),
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
        vacancy_repository: VacancyRepository,
        cover_letter_service: CoverLetterService,
        confirmation_ttl_seconds: int,
        default_resume_id: str = "",
        demo_mode: bool = False,
    ) -> None:
        self.hh_client = hh_client
        self.oauth_service = oauth_service
        self.integration_repository = integration_repository
        self.application_repository = application_repository
        self.vacancy_repository = vacancy_repository
        self.cover_letter_service = cover_letter_service
        self.confirmation_ttl_seconds = confirmation_ttl_seconds
        self.default_resume_id = default_resume_id.strip()
        self.demo_mode = demo_mode

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
        if vacancy.application is not None and has_registered_application(
            vacancy.application.status
        ):
            raise HHAlreadyAppliedError
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
            except AIServiceError as exc:
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
            manual_submission_required=(
                selected_data.external_id == MANUAL_RESUME_EXTERNAL_ID
            ),
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
            if selected is None and draft.resume_external_id == self.default_resume_id:
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
            manual_submission_required=(
                draft.resume_external_id == MANUAL_RESUME_EXTERNAL_ID
            ),
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
        can_mark_applied: bool = False,
        result_uncertain: bool = False,
    ) -> ApplicationResult:
        application = await self.application_repository.mark_manual_action(
            application_id,
            code=code,
            message=message,
        )
        return ApplicationResult(
            status="manual_action_required",
            message=message,
            vacancy_id=application.vacancy_id,
            application_id=application.id,
            manual_url=manual_url,
            error_code=code,
            can_mark_applied=can_mark_applied,
            result_uncertain=result_uncertain,
        )

    async def _failed_result(
        self,
        application_id: int,
        *,
        code: str,
        message: str,
        manual_url: str | None,
        can_retry: bool,
        can_mark_applied: bool = False,
        requires_oauth: bool = False,
    ) -> ApplicationResult:
        application = await self.application_repository.mark_failed(
            application_id,
            code=code,
            message=message,
        )
        return ApplicationResult(
            status="failed",
            message=message,
            vacancy_id=application.vacancy_id,
            application_id=application.id,
            manual_url=manual_url,
            error_code=code,
            can_retry=can_retry,
            can_mark_applied=can_mark_applied,
            requires_oauth=requires_oauth,
        )

    async def _submitted_result(
        self,
        application: HHApplication,
        *,
        external_id: str | None,
        message: str,
        submitted_through_bot: bool = True,
    ) -> ApplicationResult:
        await self.application_repository.finalize_submitted(
            application.id,
            external_id=external_id,
            submitted_through_bot=submitted_through_bot,
        )
        return ApplicationResult(
            status="submitted",
            message=message,
            vacancy_id=application.vacancy_id,
            application_id=application.id,
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
            "expired": "Подтверждение устарело. Подготовьте отклик заново.",
            "used": "Это подтверждение уже использовано.",
            "submitting": "Отклик уже обрабатывается.",
            "submitted": "Отклик уже был отправлен.",
        }
        if lease.outcome != "acquired" or lease.application is None:
            application = lease.application
            error_code = application.error_code if application is not None else None
            status = (
                "submitted"
                if lease.outcome == "submitted"
                else "manual_action_required"
                if application is not None
                and application.api_status == "manual_action_required"
                else "failed"
            )
            can_retry = bool(
                application is not None
                and (
                    (
                        lease.outcome == "expired"
                        and application.api_status == "awaiting_confirmation"
                    )
                    or (
                        application.api_status == "failed"
                        and error_code in RETRYABLE_APPLICATION_ERRORS
                    )
                )
            )
            manual_url: str | None = None
            if application is not None:
                vacancy = await self.vacancy_repository.get_by_id(
                    application.vacancy_id
                )
                manual_url = vacancy.url if vacancy is not None else None
            return ApplicationResult(
                status=status,
                message=(
                    application.error_message
                    if application is not None and application.error_message
                    else messages[lease.outcome]
                ),
                vacancy_id=application.vacancy_id if application is not None else None,
                application_id=application.id if application is not None else None,
                manual_url=manual_url,
                error_code=error_code,
                can_retry=can_retry,
                can_mark_applied=error_code in MANUAL_COMPLETION_ERRORS,
                requires_oauth=error_code == "authorization_expired",
                result_uncertain=error_code in UNCERTAIN_APPLICATION_ERRORS,
            )
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
                can_mark_applied=True,
            )
        if details.get("has_test") or (
            isinstance(details.get("test"), dict) and details["test"].get("required")
        ):
            return await self._manual_result(
                application.id,
                code="test_required",
                message=(
                    "Работодатель требует пройти тест. Завершите отклик на сайте "
                    "HeadHunter."
                ),
                manual_url=manual_url,
                can_mark_applied=True,
            )
        if application.resume_external_id == MANUAL_RESUME_EXTERNAL_ID:
            return await self._manual_result(
                application.id,
                code="resume_selection_required",
                message="Откройте вакансию и выберите резюме на HeadHunter.",
                manual_url=manual_url,
                can_mark_applied=True,
            )

        if self.demo_mode:
            await self.application_repository.mark_manual_action(
                application.id,
                code="demo_mode",
                message="Демо-режим: внешний отклик намеренно не отправлен.",
            )
            return ApplicationResult(
                status="demo",
                message=(
                    "Письмо и подтверждение прошли полный сценарий. "
                    "Внешний отклик не отправлен — DEMO_MODE защищает от "
                    "случайного действия."
                ),
                vacancy_id=application.vacancy_id,
                application_id=application.id,
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
            return await self._failed_result(
                application.id,
                code="authorization_expired",
                message=(
                    "Авторизация HeadHunter истекла. Письмо и выбранное резюме "
                    "сохранены — подключите аккаунт заново и продолжите отклик."
                ),
                manual_url=manual_url,
                can_retry=False,
                can_mark_applied=True,
                requires_oauth=True,
            )
        except HHRemoteError as exc:
            if (
                exc.error_type == "negotiations"
                and exc.error_value == "already_applied"
            ):
                return await self._submitted_result(
                    application,
                    external_id=None,
                    message=(
                        "Отклик уже существует на HeadHunter. Локальный статус "
                        "синхронизирован."
                    ),
                    submitted_through_bot=False,
                )
            code, message, can_retry, can_mark_applied = _application_error_details(exc)
            result_url = exc.fallback_url or manual_url
            if can_retry:
                return await self._failed_result(
                    application.id,
                    code=code,
                    message=(f"{message} Письмо и выбранное резюме сохранены."),
                    manual_url=result_url,
                    can_retry=True,
                    can_mark_applied=can_mark_applied,
                )
            return await self._manual_result(
                application.id,
                code=code,
                message=f"{message} Подготовленное письмо сохранено.",
                manual_url=result_url,
                can_mark_applied=can_mark_applied,
            )
        except HHTransportError as exc:
            if _transport_failure_is_safe_to_retry(exc):
                return await self._failed_result(
                    application.id,
                    code="hh_connection_failed",
                    message=(
                        "Не удалось соединиться с HeadHunter. Запрос не был "
                        "отправлен; письмо и выбранное резюме сохранены."
                    ),
                    manual_url=manual_url,
                    can_retry=True,
                    can_mark_applied=True,
                )
            return await self._manual_result(
                application.id,
                code="submission_result_unknown",
                message=(
                    "Соединение оборвалось во время отправки, поэтому результат "
                    "неизвестен. Письмо сохранено. Сначала проверьте отклики на "
                    "HeadHunter — автоматический повтор скрыт, чтобы не создать дубль."
                ),
                manual_url=manual_url,
                can_mark_applied=True,
                result_uncertain=True,
            )
        except HHAPIError:
            return await self._manual_result(
                application.id,
                code="hh_request_failed",
                message=(
                    "Не удалось подтвердить результат отправки. Письмо сохранено. "
                    "Проверьте отклики на HeadHunter перед новой попыткой."
                ),
                manual_url=manual_url,
                can_mark_applied=True,
                result_uncertain=True,
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
