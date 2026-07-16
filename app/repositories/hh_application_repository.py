from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.database import Database
from app.models import ApplicationConfirmation, HHApplication, utc_now
from app.repositories.hh_integration_repository import token_hash


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
                application = await session.get(HHApplication, confirmation.application_id)
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
