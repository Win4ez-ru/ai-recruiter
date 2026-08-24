from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.database import Database
from app.models import Application, VacancyStatusHistory
from app.schemas import ApplicationStatus
from app.vacancy_status import (
    VacancyStatus,
    VacancyStatusSource,
    normalize_status,
    validate_transition,
)

logger = logging.getLogger(__name__)
VALID_STATUSES = {status.value for status in VacancyStatus}


class ApplicationRepository:
    """Persists the current vacancy lifecycle state and its audit trail.

    The historical name is retained for compatibility with existing services.
    All status changes must go through ``transition`` or its intent-specific
    helpers so current state and history are committed atomically.
    """

    def __init__(self, database: Database) -> None:
        self.database = database

    async def get(self, vacancy_id: int) -> Application | None:
        async with self.database.session_factory() as session:
            return await session.scalar(
                select(Application).where(Application.vacancy_id == vacancy_id)
            )

    async def transition(
        self,
        vacancy_id: int,
        status: ApplicationStatus | VacancyStatus | str,
        *,
        source: VacancyStatusSource | str,
        reason: str | None = None,
        details: dict[str, Any] | None = None,
        only_from: set[VacancyStatus] | None = None,
    ) -> Application:
        target = normalize_status(status)
        source_value = (
            source.value if isinstance(source, VacancyStatusSource) else source
        )
        now = datetime.now(timezone.utc)

        async with self.database.session_factory() as session:
            application = await session.scalar(
                select(Application)
                .where(Application.vacancy_id == vacancy_id)
                .with_for_update()
            )
            current = normalize_status(
                application.status if application is not None else VacancyStatus.NEW
            )
            if only_from is not None and current not in only_from:
                if application is None:
                    raise RuntimeError("Lifecycle precondition failed without a row")
                return application
            current, target = validate_transition(current, target)

            if application is not None and current == target:
                return application

            if application is None:
                application = Application(vacancy_id=vacancy_id, status=current.value)
                session.add(application)

            application.status = target.value
            application.status_source = source_value
            application.status_changed_at = now
            if target == VacancyStatus.APPLIED_MANUAL:
                application.application_source = VacancyStatusSource.MANUAL.value
                application.applied_at = application.applied_at or now
            elif target == VacancyStatus.APPLIED_BOT:
                application.application_source = VacancyStatusSource.BOT.value
                application.applied_at = application.applied_at or now

            session.add(
                VacancyStatusHistory(
                    vacancy_id=vacancy_id,
                    from_status=current.value,
                    to_status=target.value,
                    source=source_value,
                    reason=reason,
                    details=details or {},
                    changed_at=now,
                )
            )
            await session.commit()
            await session.refresh(application)
            logger.info(
                "Vacancy %s lifecycle changed from %s to %s by %s",
                vacancy_id,
                current.value,
                target.value,
                source_value,
            )
            return application

    async def set_status(
        self,
        vacancy_id: int,
        status: ApplicationStatus | VacancyStatus | str,
        *,
        source: VacancyStatusSource | str | None = None,
        reason: str | None = None,
    ) -> Application:
        """Compatibility wrapper; new code should express the transition intent."""

        target = normalize_status(status)
        if source is None:
            if target == VacancyStatus.APPLIED_BOT:
                source = VacancyStatusSource.BOT
            elif target == VacancyStatus.APPLIED_MANUAL:
                source = VacancyStatusSource.MANUAL
            else:
                source = VacancyStatusSource.USER
        return await self.transition(
            vacancy_id,
            target,
            source=source,
            reason=reason,
        )

    async def mark_viewed(self, vacancy_id: int) -> Application | None:
        return await self.transition(
            vacancy_id,
            VacancyStatus.VIEWED,
            source=VacancyStatusSource.SYSTEM,
            reason="Vacancy card displayed",
            only_from={VacancyStatus.NEW},
        )

    async def toggle_saved(self, vacancy_id: int) -> Application:
        current = await self.get(vacancy_id)
        current_status = normalize_status(
            current.status if current is not None else VacancyStatus.NEW
        )
        target = (
            VacancyStatus.VIEWED
            if current_status == VacancyStatus.SAVED
            else VacancyStatus.SAVED
        )
        return await self.transition(
            vacancy_id,
            target,
            source=VacancyStatusSource.USER,
            reason="Favorite toggled",
        )

    async def mark_applied_manual(self, vacancy_id: int) -> Application:
        return await self.transition(
            vacancy_id,
            VacancyStatus.APPLIED_MANUAL,
            source=VacancyStatusSource.MANUAL,
            reason="User confirmed an external application",
        )

    async def mark_applied_bot(self, vacancy_id: int) -> Application:
        return await self.transition(
            vacancy_id,
            VacancyStatus.APPLIED_BOT,
            source=VacancyStatusSource.BOT,
            reason="Application submitted through the bot",
        )

    async def mark_applied_external(self, vacancy_id: int) -> Application:
        return await self.transition(
            vacancy_id,
            VacancyStatus.APPLIED_MANUAL,
            source=VacancyStatusSource.IMPORT,
            reason="Existing external application detected",
        )

    async def hide(self, vacancy_id: int) -> Application:
        return await self.transition(
            vacancy_id,
            VacancyStatus.HIDDEN,
            source=VacancyStatusSource.USER,
            reason="Vacancy hidden by user",
        )

    async def reject(self, vacancy_id: int) -> Application:
        return await self.transition(
            vacancy_id,
            VacancyStatus.REJECTED,
            source=VacancyStatusSource.USER,
            reason="Vacancy rejected by user",
        )

    async def history(self, vacancy_id: int) -> list[VacancyStatusHistory]:
        async with self.database.session_factory() as session:
            rows = await session.scalars(
                select(VacancyStatusHistory)
                .where(VacancyStatusHistory.vacancy_id == vacancy_id)
                .order_by(VacancyStatusHistory.changed_at, VacancyStatusHistory.id)
            )
            return list(rows.all())

    async def save_cover_letter(self, vacancy_id: int, text: str) -> Application:
        async with self.database.session_factory() as session:
            application = await session.scalar(
                select(Application).where(Application.vacancy_id == vacancy_id)
            )
            if application is None:
                application = Application(vacancy_id=vacancy_id, status="new")
                session.add(application)
            application.cover_letter = text
            await session.commit()
            await session.refresh(application)
            return application
