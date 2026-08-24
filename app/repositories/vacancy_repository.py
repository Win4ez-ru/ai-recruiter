import logging
from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timezone

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.database import Database
from app.models import Application, Vacancy, VacancyAnalysis, VacancyStatusHistory
from app.schemas import StatsResult, VacancyAnalysisResult, VacancyCreate
from app.vacancy_status import (
    APPLICATION_RECORDED_STATUSES,
    EXCLUDED_FROM_RECOMMENDATIONS,
    VacancyStatus,
)

logger = logging.getLogger(__name__)


class VacancyRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _with_relations():
        return selectinload(Vacancy.analysis), selectinload(Vacancy.application)

    @staticmethod
    def _recommendable_status_clause():
        excluded = {*EXCLUDED_FROM_RECOMMENDATIONS, "applied", "skipped"}
        return or_(
            Application.id.is_(None),
            and_(
                Application.application_source.is_(None),
                Application.status.not_in(excluded),
            ),
        )

    async def create_if_new(self, data: VacancyCreate) -> tuple[Vacancy, bool]:
        async with self.database.session_factory() as session:
            existing = await session.scalar(
                select(Vacancy).where(
                    Vacancy.source == data.source,
                    Vacancy.external_id == data.external_id,
                )
            )
            if existing is not None:
                changed = False
                for field, value in data.model_dump().items():
                    if getattr(existing, field) != value:
                        setattr(existing, field, value)
                        changed = True
                if changed:
                    await session.commit()
                    await session.refresh(existing)
                    logger.debug(
                        "Refreshed vacancy %s:%s", data.source, data.external_id
                    )
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

    async def external_ids_needing_refresh(
        self,
        external_ids: Sequence[str],
        *,
        stale_before: datetime,
        source: str = "hh",
    ) -> set[str]:
        if not external_ids:
            return set()
        cutoff = (
            stale_before.replace(tzinfo=timezone.utc)
            if stale_before.tzinfo is None
            else stale_before
        )
        async with self.database.session_factory() as session:
            rows = await session.execute(
                select(Vacancy.external_id, Vacancy.updated_at).where(
                    Vacancy.source == source,
                    Vacancy.external_id.in_(external_ids),
                )
            )
            existing: set[str] = set()
            stale: set[str] = set()
            for external_id, updated_at in rows.all():
                existing.add(external_id)
                aware_updated = (
                    updated_at.replace(tzinfo=timezone.utc)
                    if updated_at.tzinfo is None
                    else updated_at
                )
                if aware_updated <= cutoff:
                    stale.add(external_id)
        return set(external_ids) - existing | stale

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
        *,
        provider: str = "legacy",
        prompt_version: str = "legacy",
        input_hash: str | None = None,
    ) -> VacancyAnalysis:
        async with self.database.session_factory() as session:
            existing = await session.scalar(
                select(VacancyAnalysis).where(VacancyAnalysis.vacancy_id == vacancy_id)
            )
            if existing is not None:
                for field, value in analysis.model_dump().items():
                    setattr(existing, field, value)
                existing.model_name = model_name
                existing.provider = provider
                existing.prompt_version = prompt_version
                existing.input_hash = input_hash
                await session.commit()
                await session.refresh(existing)
                return existing
            row = VacancyAnalysis(
                vacancy_id=vacancy_id,
                model_name=model_name,
                provider=provider,
                prompt_version=prompt_version,
                input_hash=input_hash,
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

    async def mark_many_sent(self, vacancy_ids: Sequence[int]) -> None:
        if not vacancy_ids:
            return
        async with self.database.session_factory() as session:
            await session.execute(
                update(Vacancy).where(Vacancy.id.in_(vacancy_ids)).values(is_sent=True)
            )
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
                self._recommendable_status_clause(),
            )
            .order_by(VacancyAnalysis.match_score.desc(), Vacancy.published_at.desc())
            .limit(limit)
        )
        if only_unsent:
            query = query.where(Vacancy.is_sent.is_(False))
        async with self.database.session_factory() as session:
            return list((await session.scalars(query)).all())

    async def count_new_candidates(self, min_score: int) -> int:
        query = (
            select(func.count(Vacancy.id))
            .join(VacancyAnalysis)
            .outerjoin(Application)
            .where(
                VacancyAnalysis.match_score >= min_score,
                Vacancy.is_sent.is_(False),
                self._recommendable_status_clause(),
            )
        )
        async with self.database.session_factory() as session:
            return int(await session.scalar(query) or 0)

    async def list_by_application_status(
        self, status: str, limit: int = 20
    ) -> list[Vacancy]:
        return await self.list_by_application_statuses([status], limit=limit)

    async def list_by_application_statuses(
        self, statuses: Sequence[str], limit: int = 20
    ) -> list[Vacancy]:
        if not statuses:
            return []
        async with self.database.session_factory() as session:
            result = await session.scalars(
                select(Vacancy)
                .join(Application)
                .options(*self._with_relations())
                .where(Application.status.in_(statuses))
                .order_by(Application.status_changed_at.desc())
                .limit(limit)
            )
            return list(result.all())

    async def list_applied(self, limit: int = 20) -> list[Vacancy]:
        lifecycle_statuses = set(APPLICATION_RECORDED_STATUSES)
        async with self.database.session_factory() as session:
            result = await session.scalars(
                select(Vacancy)
                .join(Application)
                .options(*self._with_relations())
                .where(
                    or_(
                        Application.status.in_(lifecycle_statuses),
                        and_(
                            Application.status.in_(
                                {
                                    VacancyStatus.REJECTED.value,
                                    VacancyStatus.ARCHIVED.value,
                                }
                            ),
                            Application.application_source.is_not(None),
                        ),
                    )
                )
                .order_by(Application.status_changed_at.desc())
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
            history_rows = await session.execute(
                select(
                    VacancyStatusHistory.to_status,
                    func.count(func.distinct(VacancyStatusHistory.vacancy_id)),
                ).group_by(VacancyStatusHistory.to_status)
            )
            history_counts = {
                status: int(count) for status, count in history_rows.all()
            }
            missing_rows = await session.scalars(select(VacancyAnalysis.missing_skills))
            counter: Counter[str] = Counter()
            for skills in missing_rows.all():
                counter.update(
                    skill.strip() for skill in (skills or []) if skill.strip()
                )
            return StatsResult(
                total_vacancies=total,
                analyzed=analyzed,
                saved=int(status_counts.get("saved", 0)),
                applied=int(
                    await session.scalar(
                        select(func.count(Application.id)).where(
                            Application.application_source.is_not(None)
                        )
                    )
                    or 0
                ),
                interviews=max(
                    int(status_counts.get("interview", 0)),
                    history_counts.get("interview", 0),
                ),
                test_tasks=max(
                    int(status_counts.get("test_task", 0)),
                    history_counts.get("test_task", 0),
                ),
                offers=max(
                    int(status_counts.get("offer", 0))
                    + int(status_counts.get("offer_accepted", 0)),
                    history_counts.get("offer", 0),
                ),
                accepted_offers=max(
                    int(status_counts.get("offer_accepted", 0)),
                    history_counts.get("offer_accepted", 0),
                ),
                rejected=max(
                    int(status_counts.get("rejected", 0)),
                    history_counts.get("rejected", 0),
                ),
                average_score=round(average, 1),
                common_missing_skills=counter.most_common(5),
            )
