import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import Database
from app.models import Application
from app.schemas import ApplicationStatus

logger = logging.getLogger(__name__)
VALID_STATUSES = {
    "new",
    "saved",
    "applied",
    "interview",
    "test_task",
    "rejected",
    "offer",
    "skipped",
}


class ApplicationRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def get(self, vacancy_id: int) -> Application | None:
        async with self.database.session_factory() as session:
            return await session.scalar(
                select(Application).where(Application.vacancy_id == vacancy_id)
            )

    async def set_status(
        self, vacancy_id: int, status: ApplicationStatus
    ) -> Application:
        if status not in VALID_STATUSES:
            raise ValueError(f"Unsupported application status: {status}")
        async with self.database.session_factory() as session:
            application = await session.scalar(
                select(Application).where(Application.vacancy_id == vacancy_id)
            )
            if application is None:
                application = Application(vacancy_id=vacancy_id)
                session.add(application)
            application.status = status
            application.applied_at = (
                datetime.now(timezone.utc) if status == "applied" else application.applied_at
            )
            await session.commit()
            await session.refresh(application)
            logger.info("Vacancy %s status changed to %s", vacancy_id, status)
            return application

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
