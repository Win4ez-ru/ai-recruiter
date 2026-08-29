from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from time import monotonic

from app.models import Vacancy, utc_now
from app.repositories.vacancy_repository import VacancyRepository
from app.schemas import SearchSummary, VacancyFilterResult
from app.services.ai_errors import AIResponseValidationError, AIServiceError
from app.services.vacancy_filter import VacancyFilter
from app.services.vacancy_ranker import VacancyRanker
from app.sources.hh import (
    HHAPIError,
    HHApplicationAuthorizationError,
    HHClient,
    HHRemoteError,
    vacancy_from_hh,
)
from app.vacancy_status import is_excluded_from_recommendations

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
        refresh_ttl_hours: int = 24,
        max_analyses_per_search: int = 25,
        ranking_concurrency: int = 1,
        search_queries: list[str] | None = None,
    ) -> None:
        self.hh_client = hh_client
        self.repository = repository
        self.vacancy_filter = vacancy_filter
        self.ranker = ranker
        self.min_score = min_score
        self.refresh_ttl_hours = refresh_ttl_hours
        self.max_analyses_per_search = max_analyses_per_search
        self.ranking_concurrency = max(1, ranking_concurrency)
        self.search_queries = list(search_queries or SEARCH_QUERIES)

    @property
    def analysis_scope(self) -> dict[str, str]:
        """Metadata that identifies analyses safe to expose as current results."""

        return {
            "provider": self.ranker.provider_name,
            "model_name": self.ranker.model_name,
            "prompt_version": self.ranker.prompt_version,
        }

    async def run(self, progress: ProgressCallback | None = None) -> SearchSummary:
        summary = SearchSummary()
        hh_started_at = monotonic()
        unique_items: dict[str, dict] = {}
        for query_index, query in enumerate(self.search_queries, start=1):
            if progress:
                await progress(
                    f"Запрашиваю HeadHunter: {query_index} из {len(self.search_queries)}."
                )
            try:
                items = await self.hh_client.search_vacancies(query, max_results=100)
            except HHApplicationAuthorizationError as exc:
                summary.errors += 1
                if "hh_configuration" not in summary.error_codes:
                    summary.error_codes.append("hh_configuration")
                logger.warning(
                    "HeadHunter application authorization failed",
                    extra={
                        "event": "hh_application_authorization_failed",
                        "query": query,
                        "error_type": type(exc).__name__,
                    },
                )
                break
            except HHRemoteError as exc:
                summary.errors += 1
                error_code = (
                    "hh_forbidden"
                    if exc.status_code == 403
                    else "hh_rate_limited"
                    if exc.status_code == 429
                    else "hh_unavailable"
                )
                if error_code not in summary.error_codes:
                    summary.error_codes.append(error_code)
                logger.warning(
                    "HeadHunter search request was rejected",
                    extra={
                        "event": "hh_search_rejected",
                        "query": query,
                        "status_code": exc.status_code,
                        "error_type": exc.error_type,
                        "error_value": exc.error_value,
                        "request_id": exc.request_id,
                    },
                )
                break
            except HHAPIError as exc:
                summary.errors += 1
                if "hh_unavailable" not in summary.error_codes:
                    summary.error_codes.append("hh_unavailable")
                logger.warning(
                    "HeadHunter search is unavailable",
                    extra={
                        "event": "hh_search_unavailable",
                        "query": query,
                        "error_type": type(exc).__name__,
                    },
                )
                break
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
            summary.hh_duration_seconds = monotonic() - hh_started_at
            return summary

        refresh_ids = await self.repository.external_ids_needing_refresh(
            list(unique_items),
            stale_before=utc_now() - timedelta(hours=self.refresh_ttl_hours),
        )
        if progress:
            await progress(
                f"Загружаю детали новых и обновлённых вакансий: {len(refresh_ids)}."
            )
        semaphore = asyncio.Semaphore(5)

        async def fetch_and_store(external_id: str) -> None:
            if external_id not in refresh_ids:
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
        summary.hh_duration_seconds = monotonic() - hh_started_at
        relevant_count = 0
        relevant: list[tuple[Vacancy, VacancyFilterResult]] = []
        for vacancy in current_vacancies:
            filter_result = self.vacancy_filter.evaluate(vacancy)
            if filter_result.is_relevant:
                relevant_count += 1
                if not self.ranker.analysis_is_current(vacancy, filter_result):
                    relevant.append((vacancy, filter_result))
            else:
                logger.debug(
                    "Vacancy %s rejected by prefilter: %s",
                    vacancy.id,
                    "; ".join(filter_result.reasons),
                )
        summary.after_prefilter = relevant_count
        relevant.sort(key=lambda item: item[0].id, reverse=True)
        summary.cached_analyses = relevant_count - len(relevant)
        relevant = relevant[: self.max_analyses_per_search]
        if progress:
            await progress(
                f"Подходят после фильтрации: {summary.after_prefilter}. "
                f"Из кэша: {summary.cached_analyses}. "
                f"Требуют AI-анализа: {len(relevant)}."
            )

        ai_started_at = monotonic()
        next_index = 0
        completed = 0
        state_lock = asyncio.Lock()
        progress_lock = asyncio.Lock()
        stop_analysis = asyncio.Event()

        async def next_candidate() -> tuple[Vacancy, VacancyFilterResult] | None:
            nonlocal next_index
            async with state_lock:
                if stop_analysis.is_set() or next_index >= len(relevant):
                    return None
                candidate = relevant[next_index]
                next_index += 1
                return candidate

        async def report_completion() -> None:
            nonlocal completed
            async with progress_lock:
                async with state_lock:
                    completed += 1
                    current = completed
                if progress:
                    await progress(
                        f"🤖 Анализирую лучшие варианты… {current}/{len(relevant)}"
                    )

        async def analyze_candidates() -> None:
            while candidate := await next_candidate():
                vacancy, filter_result = candidate
                summary.ai_requests += 1
                try:
                    analysis = await self.ranker.rank(vacancy, filter_result)
                    if analysis is None:
                        summary.errors += 1
                        continue
                    await self.repository.save_analysis(
                        vacancy.id,
                        analysis,
                        self.ranker.model_name,
                        provider=self.ranker.provider_name,
                        prompt_version=self.ranker.prompt_version,
                        input_hash=self.ranker.input_hash(vacancy, filter_result),
                    )
                    summary.analyzed += 1
                except AIResponseValidationError as exc:
                    summary.errors += 1
                    if exc.code not in summary.error_codes:
                        summary.error_codes.append(exc.code)
                    logger.warning(
                        "Skipping malformed AI analysis and continuing",
                        extra={
                            "event": "ai_analysis_invalid",
                            "error_code": exc.code,
                            "vacancy_id": vacancy.id,
                        },
                    )
                except AIServiceError as exc:
                    summary.errors += 1
                    if exc.code not in summary.error_codes:
                        summary.error_codes.append(exc.code)
                    stop_analysis.set()
                    logger.warning(
                        "Stopping new AI analysis requests for this search run",
                        extra={
                            "event": "ai_analysis_stopped",
                            "error_code": exc.code,
                            "vacancy_id": vacancy.id,
                        },
                    )
                except Exception:
                    summary.errors += 1
                    logger.exception(
                        "Failed to save analysis for vacancy %s", vacancy.id
                    )
                finally:
                    await report_completion()

        if relevant and progress:
            await progress(f"🤖 Анализирую лучшие варианты… 0/{len(relevant)}")
        workers = min(self.ranking_concurrency, len(relevant))
        if workers:
            await asyncio.gather(*(analyze_candidates() for _ in range(workers)))
        summary.ai_duration_seconds = monotonic() - ai_started_at

        refreshed = await self.repository.list_by_external_ids(list(unique_items))
        summary.suitable = sum(
            1
            for vacancy in refreshed
            if vacancy.analysis is not None
            and self.ranker.analysis_is_current(
                vacancy, self.vacancy_filter.evaluate(vacancy)
            )
            and vacancy.analysis.match_score >= self.min_score
            and (
                vacancy.application is None
                or not is_excluded_from_recommendations(vacancy.application.status)
            )
        )
        logger.info(
            "Search complete: found=%s deduplicated=%s new=%s relevant=%s "
            "analyzed=%s suitable=%s errors=%s",
            summary.found,
            summary.after_deduplication,
            summary.new_vacancies,
            summary.after_prefilter,
            summary.analyzed,
            summary.suitable,
            summary.errors,
            extra={
                "event": "vacancy_search_completed",
                "hh_duration_ms": round(summary.hh_duration_seconds * 1000),
                "ai_duration_ms": round(summary.ai_duration_seconds * 1000),
                "ai_requests": summary.ai_requests,
                "cache_hits": summary.cached_analyses,
                "ranking_concurrency": workers,
            },
        )
        return summary
