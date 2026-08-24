from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

from app.database import Database
from app.models import (
    Application,
    ApplicationConfirmation,
    HHApplication,
    VacancyStatusHistory,
    utc_now,
)
from app.repositories.hh_integration_repository import token_hash
from app.vacancy_status import (
    VacancyStatus,
    VacancyStatusSource,
    VacancyStatusTransitionError,
    has_registered_application,
    normalize_status,
    validate_transition,
)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


@dataclass(slots=True)
class SubmissionLease:
    outcome: str
    application: HHApplication | None = None


class HHApplicationRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def save_draft(
        self,
        *,
        telegram_user_id: int,
        vacancy_id: int,
        vacancy_external_id: str,
        resume_external_id: str,
        cover_letter: str,
    ) -> HHApplication:
        async with self.database.session_factory() as session:
            row = await session.scalar(
                select(HHApplication).where(
                    HHApplication.telegram_user_id == telegram_user_id,
                    HHApplication.source == "hh",
                    HHApplication.vacancy_external_id == vacancy_external_id,
                    HHApplication.resume_external_id == resume_external_id,
                )
            )
            if row is None:
                row = HHApplication(
                    telegram_user_id=telegram_user_id,
                    vacancy_id=vacancy_id,
                    source="hh",
                    vacancy_external_id=vacancy_external_id,
                    resume_external_id=resume_external_id,
                    cover_letter=cover_letter,
                )
                session.add(row)
            elif row.api_status != "submitted":
                row.cover_letter = cover_letter
                row.process_status = "draft"
                row.api_status = "draft"
                row.error_code = None
                row.error_message = None
                row.prepared_at = utc_now()
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                row = await session.scalar(
                    select(HHApplication).where(
                        HHApplication.telegram_user_id == telegram_user_id,
                        HHApplication.source == "hh",
                        HHApplication.vacancy_external_id == vacancy_external_id,
                        HHApplication.resume_external_id == resume_external_id,
                    )
                )
                if row is None:
                    raise
            await session.refresh(row)
            return row

    async def find_by_identity(
        self,
        *,
        telegram_user_id: int,
        vacancy_external_id: str,
        resume_external_id: str,
    ) -> HHApplication | None:
        async with self.database.session_factory() as session:
            return await session.scalar(
                select(HHApplication).where(
                    HHApplication.telegram_user_id == telegram_user_id,
                    HHApplication.source == "hh",
                    HHApplication.vacancy_external_id == vacancy_external_id,
                    HHApplication.resume_external_id == resume_external_id,
                )
            )

    async def get_owned(
        self, application_id: int, telegram_user_id: int
    ) -> HHApplication | None:
        async with self.database.session_factory() as session:
            return await session.scalar(
                select(HHApplication).where(
                    HHApplication.id == application_id,
                    HHApplication.telegram_user_id == telegram_user_id,
                )
            )

    async def update_cover_letter(
        self, *, application_id: int, telegram_user_id: int, cover_letter: str
    ) -> HHApplication | None:
        async with self.database.session_factory() as session:
            row = await session.scalar(
                select(HHApplication).where(
                    HHApplication.id == application_id,
                    HHApplication.telegram_user_id == telegram_user_id,
                    HHApplication.api_status != "submitted",
                )
            )
            if row is None:
                return None
            row.cover_letter = cover_letter
            row.process_status = "draft"
            await session.commit()
            await session.refresh(row)
            return row

    async def create_confirmation(
        self,
        *,
        application_id: int,
        telegram_user_id: int,
        ttl_seconds: int,
    ) -> str | None:
        raw_token = secrets.token_urlsafe(18)
        now = utc_now()
        async with self.database.session_factory() as session:
            row = await session.scalar(
                select(HHApplication).where(
                    HHApplication.id == application_id,
                    HHApplication.telegram_user_id == telegram_user_id,
                )
            )
            if row is None or row.api_status == "submitted":
                return None
            row.process_status = "awaiting_confirmation"
            row.api_status = "awaiting_confirmation"
            session.add(
                ApplicationConfirmation(
                    telegram_user_id=telegram_user_id,
                    vacancy_id=row.vacancy_id,
                    resume_external_id=row.resume_external_id,
                    application_id=row.id,
                    token_hash=token_hash(raw_token),
                    expires_at=now + timedelta(seconds=ttl_seconds),
                )
            )
            await session.commit()
        return raw_token

    async def acquire_submission(
        self, *, raw_token: str, telegram_user_id: int
    ) -> SubmissionLease:
        now = utc_now()
        async with self.database.session_factory() as session:
            confirmation = await session.scalar(
                select(ApplicationConfirmation).where(
                    ApplicationConfirmation.token_hash == token_hash(raw_token)
                )
            )
            if confirmation is None:
                return SubmissionLease("invalid")
            if confirmation.telegram_user_id != telegram_user_id:
                return SubmissionLease("forbidden")
            if confirmation.used_at is not None:
                application = await session.get(
                    HHApplication, confirmation.application_id
                )
                outcome = (
                    "submitted"
                    if application and application.api_status == "submitted"
                    else "used"
                )
                return SubmissionLease(outcome, application)
            if _aware(confirmation.expires_at) <= now:
                return SubmissionLease("expired")

            consumed = await session.execute(
                update(ApplicationConfirmation)
                .where(
                    ApplicationConfirmation.id == confirmation.id,
                    ApplicationConfirmation.used_at.is_(None),
                )
                .values(used_at=now)
            )
            if consumed.rowcount != 1:
                await session.rollback()
                return SubmissionLease("used")
            application = await session.get(HHApplication, confirmation.application_id)
            if application is None:
                await session.rollback()
                return SubmissionLease("invalid")
            if application.api_status == "submitted":
                await session.commit()
                return SubmissionLease("submitted", application)
            if application.api_status == "submitting":
                await session.commit()
                return SubmissionLease("submitting", application)
            application.process_status = "submitting"
            application.api_status = "submitting"
            application.confirmed_at = now
            application.submitting_at = now
            application.attempts += 1
            await session.commit()
            await session.refresh(application)
            return SubmissionLease("acquired", application)

    async def mark_manual_action(
        self, application_id: int, *, code: str, message: str
    ) -> HHApplication:
        async with self.database.session_factory() as session:
            row = await session.get(HHApplication, application_id)
            if row is None:
                raise LookupError("Application draft not found")
            row.process_status = "manual_action_required"
            row.api_status = "manual_action_required"
            row.error_code = code
            row.error_message = message
            await session.commit()
            await session.refresh(row)
            return row

    async def mark_submitted(
        self, application_id: int, *, external_id: str | None
    ) -> HHApplication:
        async with self.database.session_factory() as session:
            row = await session.get(HHApplication, application_id)
            if row is None:
                raise LookupError("Application draft not found")
            row.process_status = "submitted"
            row.api_status = "submitted"
            row.external_application_id = external_id
            row.submitted_at = utc_now()
            row.error_code = None
            row.error_message = None
            await session.commit()
            await session.refresh(row)
            return row

    async def finalize_submitted(
        self,
        application_id: int,
        *,
        external_id: str | None,
        submitted_through_bot: bool,
    ) -> HHApplication:
        """Commit the HH result and vacancy lifecycle in one local transaction."""

        now = utc_now()
        target = (
            VacancyStatus.APPLIED_BOT
            if submitted_through_bot
            else VacancyStatus.APPLIED_MANUAL
        )
        source = (
            VacancyStatusSource.BOT
            if submitted_through_bot
            else VacancyStatusSource.IMPORT
        )
        application_source = (
            VacancyStatusSource.BOT
            if submitted_through_bot
            else VacancyStatusSource.MANUAL
        )
        async with self.database.session_factory() as session:
            row = await session.scalar(
                select(HHApplication)
                .where(HHApplication.id == application_id)
                .with_for_update()
            )
            if row is None:
                raise LookupError("Application draft not found")

            lifecycle = await session.scalar(
                select(Application)
                .where(Application.vacancy_id == row.vacancy_id)
                .with_for_update()
            )
            current = normalize_status(
                lifecycle.status if lifecycle is not None else VacancyStatus.NEW
            )
            preserve_advanced_status = has_registered_application(current) or bool(
                lifecycle is not None
                and lifecycle.application_source
                and current in {VacancyStatus.REJECTED, VacancyStatus.ARCHIVED}
            )
            forced_transition = False
            if preserve_advanced_status:
                validated_target = current
            else:
                try:
                    current, validated_target = validate_transition(current, target)
                except VacancyStatusTransitionError:
                    # An accepted external submission is authoritative. A concurrent
                    # local hide/reject/archive must not leave lifecycle contradictory.
                    validated_target = target
                    forced_transition = True

            if lifecycle is None:
                lifecycle = Application(
                    vacancy_id=row.vacancy_id,
                    status=current.value,
                )
                session.add(lifecycle)
            if current != validated_target:
                lifecycle.status = validated_target.value
                lifecycle.status_source = source.value
                lifecycle.status_changed_at = now
                session.add(
                    VacancyStatusHistory(
                        vacancy_id=row.vacancy_id,
                        from_status=current.value,
                        to_status=validated_target.value,
                        source=source.value,
                        reason="HeadHunter submission finalized",
                        details={
                            "hh_application_id": row.id,
                            "forced_transition": forced_transition,
                        },
                        changed_at=now,
                    )
                )
            if lifecycle.application_source is None:
                lifecycle.application_source = (
                    VacancyStatusSource.MANUAL.value
                    if current == VacancyStatus.APPLIED_MANUAL
                    else VacancyStatusSource.BOT.value
                    if current == VacancyStatus.APPLIED_BOT
                    else application_source.value
                )
            lifecycle.applied_at = lifecycle.applied_at or now

            row.process_status = "submitted"
            row.api_status = "submitted"
            if external_id is not None:
                row.external_application_id = external_id
            row.submitted_at = row.submitted_at or now
            row.error_code = None
            row.error_message = None
            await session.commit()
            await session.refresh(row)
            return row

    async def reconcile_incomplete_finalizations(
        self, *, stale_before: datetime
    ) -> tuple[int, int]:
        """Repair legacy submitted rows and quarantine abandoned submissions."""

        repaired = 0
        async with self.database.session_factory() as session:
            submitted_ids = list(
                (
                    await session.scalars(
                        select(HHApplication.id)
                        .outerjoin(
                            Application,
                            Application.vacancy_id == HHApplication.vacancy_id,
                        )
                        .where(
                            HHApplication.api_status == "submitted",
                            or_(
                                Application.id.is_(None),
                                Application.application_source.is_(None),
                            ),
                        )
                    )
                ).all()
            )
        for application_id in submitted_ids:
            await self.finalize_submitted(
                application_id,
                external_id=None,
                submitted_through_bot=True,
            )
            repaired += 1

        cutoff = (
            stale_before.replace(tzinfo=timezone.utc)
            if stale_before.tzinfo is None
            else stale_before
        )
        recovered = 0
        async with self.database.session_factory() as session:
            rows = list(
                (
                    await session.scalars(
                        select(HHApplication).where(
                            HHApplication.api_status == "submitting"
                        )
                    )
                ).all()
            )
            for row in rows:
                submitting_at = row.submitting_at or row.updated_at
                aware_submitting_at = (
                    submitting_at.replace(tzinfo=timezone.utc)
                    if submitting_at.tzinfo is None
                    else submitting_at
                )
                if aware_submitting_at > cutoff:
                    continue
                row.process_status = "manual_action_required"
                row.api_status = "manual_action_required"
                row.error_code = "submission_result_unknown"
                row.error_message = (
                    "Отправка была прервана. Проверьте отклики на HeadHunter."
                )
                recovered += 1
            if recovered:
                await session.commit()
        return repaired, recovered

    async def count_for_identity(
        self,
        *,
        telegram_user_id: int,
        vacancy_external_id: str,
        resume_external_id: str,
    ) -> int:
        async with self.database.session_factory() as session:
            rows = await session.scalars(
                select(HHApplication.id).where(
                    HHApplication.telegram_user_id == telegram_user_id,
                    HHApplication.source == "hh",
                    HHApplication.vacancy_external_id == vacancy_external_id,
                    HHApplication.resume_external_id == resume_external_id,
                )
            )
            return len(rows.all())
