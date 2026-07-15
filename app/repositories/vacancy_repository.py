import logging
from collections import Counter
from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.database import Database
from app.models import Application, Vacancy, VacancyAnalysis
from app.schemas import StatsResult, VacancyAnalysisResult, VacancyCreate

logger = logging.getLogger(__name__)


class VacancyRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _with_relations():
        return selectinload(Vacancy.analysis), selectinload(Vacancy.application)

    async def create_if_new(self, data: VacancyCreate) -> tuple[Vacancy, bool]:
        async with self.database.session_factory() as session:
            existing = await session.scalar(
                select(Vacancy).where(
                    Vacancy.source == data.source,
                    Vacancy.external_id == data.external_id,
                )
            )
            if existing is not None:
                return existing, False

            vacancy = Vacancy(**data.model_dump())
            session.add(vacancy)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = await session.scalar(
                    select(Vacancy).where(
                        Vacancy.source == data.source,
                        Vacancy.external_id == data.external_id,
                    )
                )
                if existing is None:
                    raise
                return existing, False
            await session.refresh(vacancy)
            logger.debug("Saved vacancy %s:%s", data.source, data.external_id)
            return vacancy, True

    async def get_by_id(self, vacancy_id: int) -> Vacancy | None:
        async with self.database.session_factory() as session:
            return await session.scalar(
                select(Vacancy)
                .options(*self._with_relations())
                .where(Vacancy.id == vacancy_id)
            )

    async def get_by_external_id(
        self, external_id: str, source: str = "hh"
    ) -> Vacancy | None:
        async with self.database.session_factory() as session:
            return await session.scalar(
                select(Vacancy)
                .options(*self._with_relations())
                .where(Vacancy.source == source, Vacancy.external_id == external_id)
            )

    async def existing_external_ids(
        self, external_ids: Sequence[str], source: str = "hh"
    ) -> set[str]:
        if not external_ids:
            return set()
        async with self.database.session_factory() as session:
            result = await session.scalars(
                select(Vacancy.external_id).where(
                    Vacancy.source == source,
                    Vacancy.external_id.in_(external_ids),
                )
            )
            return set(result.all())

    async def list_by_external_ids(
        self, external_ids: Sequence[str], source: str = "hh"
    ) -> list[Vacancy]:
        if not external_ids:
            return []
        async with self.database.session_factory() as session:
            result = await session.scalars(
                select(Vacancy)
                .options(*self._with_relations())
                .where(
                    Vacancy.source == source,
                    Vacancy.external_id.in_(external_ids),
                )
            )
            return list(result.all())

    async def save_analysis(
        self,
        vacancy_id: int,
        analysis: VacancyAnalysisResult,
        model_name: str,
    ) -> VacancyAnalysis:
        async with self.database.session_factory() as session:
            existing = await session.scalar(
                select(VacancyAnalysis).where(VacancyAnalysis.vacancy_id == vacancy_id)
            )
            if existing is not None:
                return existing
            row = VacancyAnalysis(
                vacancy_id=vacancy_id,
                model_name=model_name,
                **analysis.model_dump(),
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def mark_sent(self, vacancy_id: int) -> None:
        async with self.database.session_factory() as session:
            vacancy = await session.get(Vacancy, vacancy_id)
            if vacancy is None:
                return
            vacancy.is_sent = True
            await session.commit()

    async def list_digest_candidates(
        self, min_score: int, limit: int, *, only_unsent: bool = True
    ) -> list[Vacancy]:
        query = (
            select(Vacancy)
            .join(VacancyAnalysis)
            .outerjoin(Application)
            .options(*self._with_relations())
            .where(
                VacancyAnalysis.match_score >= min_score,
                or_(Application.id.is_(None), Application.status != "skipped"),
            )
            .order_by(VacancyAnalysis.match_score.desc(), Vacancy.published_at.desc())
            .limit(limit)
        )
        if only_unsent:
            query = query.where(Vacancy.is_sent.is_(False))
        async with self.database.session_factory() as session:
            return list((await session.scalars(query)).all())

    async def list_by_application_status(
        self, status: str, limit: int = 20
    ) -> list[Vacancy]:
        async with self.database.session_factory() as session:
            result = await session.scalars(
                select(Vacancy)
                .join(Application)
                .options(*self._with_relations())
                .where(Application.status == status)
                .order_by(Application.updated_at.desc())
                .limit(limit)
            )
            return list(result.all())

    async def stats(self) -> StatsResult:
        async with self.database.session_factory() as session:
            total = int(await session.scalar(select(func.count(Vacancy.id))) or 0)
            analyzed = int(
                await session.scalar(select(func.count(VacancyAnalysis.id))) or 0
            )
            average = float(
                await session.scalar(select(func.avg(VacancyAnalysis.match_score))) or 0
            )
            status_rows = await session.execute(
                select(Application.status, func.count(Application.id)).group_by(
                    Application.status
                )
            )
            status_counts = {status: count for status, count in status_rows.all()}
            missing_rows = await session.scalars(
                select(VacancyAnalysis.missing_skills)
            )
            counter: Counter[str] = Counter()
            for skills in missing_rows.all():
                counter.update(skill.strip() for skill in (skills or []) if skill.strip())
            return StatsResult(
                total_vacancies=total,
                analyzed=analyzed,
                saved=int(status_counts.get("saved", 0)),
                applied=int(status_counts.get("applied", 0)),
                interviews=int(status_counts.get("interview", 0)),
                rejected=int(status_counts.get("rejected", 0)),
                average_score=round(average, 1),
                common_missing_skills=counter.most_common(5),
            )
