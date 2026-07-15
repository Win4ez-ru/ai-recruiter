from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from app.models import Vacancy
from app.repositories.vacancy_repository import VacancyRepository
from app.schemas import SearchSummary, VacancyFilterResult
from app.services.vacancy_filter import VacancyFilter
from app.services.vacancy_ranker import VacancyRanker
from app.sources.hh import HHAPIError, HHClient, vacancy_from_hh

logger = logging.getLogger(__name__)

SEARCH_QUERIES = [
    "iOS Developer",
    "iOS-разработчик",
    "Junior iOS Developer",
    "Swift Developer",
    "SwiftUI Developer",
    "стажер iOS",
    "мобильный разработчик Swift",
]
ProgressCallback = Callable[[str], Awaitable[None]]


class VacancySearchService:
    def __init__(
        self,
        hh_client: HHClient,
        repository: VacancyRepository,
        vacancy_filter: VacancyFilter,
        ranker: VacancyRanker,
        *,
        min_score: int,
    ) -> None:
        self.hh_client = hh_client
        self.repository = repository
        self.vacancy_filter = vacancy_filter
        self.ranker = ranker
        self.min_score = min_score

    async def run(self, progress: ProgressCallback | None = None) -> SearchSummary:
        summary = SearchSummary()
        unique_items: dict[str, dict] = {}
        for query in SEARCH_QUERIES:
            try:
                items = await self.hh_client.search_vacancies(query, max_results=100)
            except HHAPIError:
                summary.errors += 1
                logger.exception("HH search failed for query %r", query)
                continue
            summary.found += len(items)
            for item in items:
                external_id = str(item.get("id") or "")
                if external_id:
                    unique_items[external_id] = item

        summary.after_deduplication = len(unique_items)
        if progress:
            await progress(
                f"Найдено вакансий: {summary.found}. После удаления дубликатов: "
                f"{summary.after_deduplication}."
            )
        if not unique_items:
            return summary

        existing_ids = await self.repository.existing_external_ids(
            list(unique_items)
        )
        semaphore = asyncio.Semaphore(5)

        async def fetch_and_store(external_id: str) -> None:
            if external_id in existing_ids:
                return
            async with semaphore:
                try:
                    details = await self.hh_client.get_vacancy(external_id)
                    vacancy_data = vacancy_from_hh(details, unique_items[external_id])
                    _, created = await self.repository.create_if_new(vacancy_data)
                    if created:
                        summary.new_vacancies += 1
                except Exception:
                    summary.errors += 1
                    logger.exception("Failed to process HH vacancy %s", external_id)

        await asyncio.gather(
            *(fetch_and_store(external_id) for external_id in unique_items)
        )

        current_vacancies = await self.repository.list_by_external_ids(
            list(unique_items)
        )
        relevant: list[tuple[Vacancy, VacancyFilterResult]] = []
        for vacancy in current_vacancies:
            if vacancy.analysis is not None:
                continue
            filter_result = self.vacancy_filter.evaluate(vacancy)
            if filter_result.is_relevant:
                relevant.append((vacancy, filter_result))
            else:
                logger.debug(
                    "Vacancy %s rejected by prefilter: %s",
                    vacancy.id,
                    "; ".join(filter_result.reasons),
                )
        summary.after_prefilter = len(relevant)
        if progress:
            await progress(
                f"После предварительной фильтрации: {summary.after_prefilter}."
            )

        for vacancy, filter_result in relevant:
            try:
                analysis = await self.ranker.rank(vacancy, filter_result)
                if analysis is None:
                    summary.errors += 1
                    continue
                await self.repository.save_analysis(
                    vacancy.id, analysis, self.ranker.model_name
                )
                summary.analyzed += 1
            except Exception:
                summary.errors += 1
                logger.exception("Failed to save analysis for vacancy %s", vacancy.id)

        refreshed = await self.repository.list_by_external_ids(list(unique_items))
        summary.suitable = sum(
            1
            for vacancy in refreshed
            if vacancy.analysis is not None
            and vacancy.analysis.match_score >= self.min_score
            and (vacancy.application is None or vacancy.application.status != "skipped")
        )
        logger.info(
            "Search complete: found=%s deduplicated=%s new=%s relevant=%s analyzed=%s suitable=%s errors=%s",
            summary.found,
            summary.after_deduplication,
            summary.new_vacancies,
            summary.after_prefilter,
            summary.analyzed,
            summary.suitable,
            summary.errors,
        )
        return summary
