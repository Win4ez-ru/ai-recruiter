"""Run a bounded real HH + AI smoke test without Telegram or applications."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from app.config import Settings
from app.database import Database
from app.network.retry import RetryPolicy
from app.repositories.vacancy_repository import VacancyRepository
from app.schemas import CandidateProfile
from app.services.ai_provider import AIProvider, build_ai_provider
from app.services.vacancy_filter import VacancyFilter
from app.services.vacancy_ranker import VacancyRanker
from app.services.vacancy_search import VacancySearchService
from app.sources.hh import HHClient

SYNTHETIC_PROFILE = CandidateProfile.model_validate(
    {
        "candidate_name": "Тестовый кандидат",
        "target_roles": ["Junior iOS Developer", "Swift Developer"],
        "location": "Россия",
        "remote_allowed": True,
        "relocation_allowed": False,
        "minimum_salary_rub": 80_000,
        "preferred_salary_rub": 120_000,
        "education": {
            "university": "Тестовый университет",
            "program": "Программная инженерия",
            "graduation_year": 2026,
        },
        "experience": {
            "commercial_ios_experience": False,
            "personal_projects": True,
            "team_experience": True,
        },
        "strong_skills": ["Swift", "SwiftUI", "Git", "REST API"],
        "basic_skills": ["UIKit", "Combine", "SQL"],
        "projects": [
            {
                "name": "Учебное iOS-приложение",
                "description": "SwiftUI, REST API, локальное хранение и тесты",
            }
        ],
        "hard_rejections": ["обязательная релокация", "только Objective-C"],
    }
)
SYNTHETIC_RESUME = """Учебное синтетическое резюме для интеграционного теста.
Навыки: Swift, SwiftUI, UIKit, REST API, Git, unit-тесты.
Опыт: личный iOS-проект и учебная командная разработка.
Коммерческий опыт отсутствует. Рассматривается удалённая работа без релокации.
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Safe real-network smoke test: searches HH and ranks a small number "
            "of vacancies in a temporary database using a synthetic candidate. "
            "It never reads local profile files or submits applications."
        )
    )
    parser.add_argument(
        "--ai-limit",
        type=int,
        choices=range(1, 6),
        default=2,
        metavar="1..5",
        help="maximum real AI ranking calls (default: 2)",
    )
    parser.add_argument(
        "--query",
        help="one HH search query; defaults to the first HH_SEARCH_QUERIES value",
    )
    return parser.parse_args()


async def run_smoke(args: argparse.Namespace) -> None:
    settings = Settings()  # type: ignore[call-arg]
    ai_provider: AIProvider | None = None
    hh_client: HHClient | None = None
    database: Database | None = None

    with TemporaryDirectory(prefix="ai-recruiter-smoke-") as directory:
        try:
            database_path = Path(directory) / "smoke.db"
            database = Database(f"sqlite+aiosqlite:///{database_path}")
            await database.create_tables()
            ai_provider, model_name = build_ai_provider(settings)
            hh_client = HHClient(
                settings.hh_user_agent,
                api_base_url=settings.hh_api_base_url,
                auth_base_url=settings.hh_auth_base_url,
                client_id=settings.hh_client_id,
                client_secret=settings.hh_client_secret_value,
                redirect_uri=settings.hh_redirect_uri,
                timeout=httpx.Timeout(
                    connect=settings.hh_connect_timeout_seconds,
                    read=settings.hh_read_timeout_seconds,
                    write=settings.hh_write_timeout_seconds,
                    pool=settings.hh_pool_timeout_seconds,
                ),
                retry_policy=RetryPolicy(
                    max_attempts=settings.hh_retry_attempts,
                    base_delay_seconds=settings.hh_retry_base_delay_seconds,
                    max_delay_seconds=settings.hh_retry_max_delay_seconds,
                    jitter_ratio=settings.hh_retry_jitter_ratio,
                ),
                proxy_url=settings.hh_proxy_value,
                trust_env=settings.hh_trust_env,
                search_area_id=settings.hh_search_area_id,
                search_period_days=settings.hh_search_period_days,
                search_remote=settings.hh_search_remote,
            )
            ranker = VacancyRanker(
                ai_provider,
                model_name,
                SYNTHETIC_PROFILE,
                SYNTHETIC_RESUME,
            )
            service = VacancySearchService(
                hh_client,
                VacancyRepository(database),
                VacancyFilter(),
                ranker,
                min_score=settings.min_score_to_send,
                refresh_ttl_hours=settings.vacancy_refresh_ttl_hours,
                max_analyses_per_search=args.ai_limit,
                ranking_concurrency=(
                    1
                    if settings.ai_provider == "ollama"
                    else min(settings.ai_ranking_concurrency, args.ai_limit)
                ),
                search_queries=[args.query or settings.hh_search_queries[0]],
            )

            async def progress(stage: str) -> None:
                print(stage)

            summary = await service.run(progress=progress)
            print(summary.model_dump_json(indent=2))
        finally:
            if hh_client is not None:
                await hh_client.close()
            if ai_provider is not None:
                await ai_provider.close()
            if database is not None:
                await database.close()


def main() -> None:
    asyncio.run(run_smoke(parse_args()))


if __name__ == "__main__":
    main()
