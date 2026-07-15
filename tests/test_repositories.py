import pytest
import pytest_asyncio

from app.database import Database
from app.repositories.application_repository import ApplicationRepository
from app.repositories.vacancy_repository import VacancyRepository
from app.schemas import VacancyAnalysisResult, VacancyCreate


@pytest_asyncio.fixture
async def database() -> Database:
    db = Database("sqlite+aiosqlite:///:memory:")
    await db.create_tables()
    yield db
    await db.close()


def vacancy_data(external_id: str = "100") -> VacancyCreate:
    return VacancyCreate(
        external_id=external_id,
        title="Junior iOS Developer",
        url=f"https://hh.ru/vacancy/{external_id}",
        description="Swift, SwiftUI",
    )


def analysis(score: int = 80) -> VacancyAnalysisResult:
    return VacancyAnalysisResult(
        match_score=score,
        decision="apply",
        role_level="junior",
        matched_skills=["Swift"],
        missing_skills=[],
        blocking_requirements=[],
        advantages=["Проекты"],
        risks=[],
        resume_focus=["Pump Kitchen"],
        reason="Подходит",
    )


@pytest.mark.asyncio
async def test_duplicates_are_not_saved(database: Database) -> None:
    repository = VacancyRepository(database)
    first, first_created = await repository.create_if_new(vacancy_data())
    second, second_created = await repository.create_if_new(vacancy_data())
    stats = await repository.stats()
    assert first_created is True
    assert second_created is False
    assert first.id == second.id
    assert stats.total_vacancies == 1


@pytest.mark.asyncio
async def test_status_can_change_from_new_to_saved(database: Database) -> None:
    vacancy_repository = VacancyRepository(database)
    application_repository = ApplicationRepository(database)
    item, _ = await vacancy_repository.create_if_new(vacancy_data("101"))
    saved = await application_repository.set_status(item.id, "saved")
    assert saved.status == "saved"
    reloaded = await application_repository.get(item.id)
    assert reloaded is not None and reloaded.status == "saved"


@pytest.mark.asyncio
async def test_sent_vacancy_is_not_returned_in_new_digest(database: Database) -> None:
    repository = VacancyRepository(database)
    item, _ = await repository.create_if_new(vacancy_data("102"))
    await repository.save_analysis(item.id, analysis(), "test-model")
    first_digest = await repository.list_digest_candidates(65, 10, only_unsent=True)
    assert [vacancy.id for vacancy in first_digest] == [item.id]
    await repository.mark_sent(item.id)
    second_digest = await repository.list_digest_candidates(65, 10, only_unsent=True)
    assert second_digest == []
