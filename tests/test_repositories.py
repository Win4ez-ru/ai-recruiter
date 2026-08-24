from datetime import timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio

from app.bot.ui import UIManager
from app.database import Database
from app.models import utc_now
from app.repositories.application_repository import ApplicationRepository
from app.repositories.ui_state_repository import UIStateRepository
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
async def test_existing_vacancy_is_refreshed_and_analysis_is_upserted(
    database: Database,
) -> None:
    repository = VacancyRepository(database)
    original, created = await repository.create_if_new(vacancy_data("refresh-1"))
    refreshed_data = vacancy_data("refresh-1").model_copy(
        update={"description": "Swift, UIKit, новая зарплата", "salary_from": 120000}
    )

    refreshed, refreshed_created = await repository.create_if_new(refreshed_data)
    await repository.save_analysis(
        original.id,
        analysis(70),
        "old-model",
        provider="ollama",
        prompt_version="v1",
        input_hash="a" * 64,
    )
    await repository.save_analysis(
        original.id,
        analysis(91),
        "gpt://folder/yandexgpt-5.1",
        provider="yandex",
        prompt_version="v2",
        input_hash="b" * 64,
    )
    loaded = await repository.get_by_id(original.id)

    assert created is True
    assert refreshed_created is False
    assert refreshed.id == original.id
    assert loaded is not None
    assert loaded.description == "Swift, UIKit, новая зарплата"
    assert loaded.salary_from == 120000
    assert loaded.analysis is not None
    assert loaded.analysis.match_score == 91
    assert loaded.analysis.provider == "yandex"
    assert loaded.analysis.prompt_version == "v2"
    assert loaded.analysis.input_hash == "b" * 64


@pytest.mark.asyncio
async def test_refresh_policy_returns_missing_and_stale_ids(database: Database) -> None:
    repository = VacancyRepository(database)
    await repository.create_if_new(vacancy_data("existing"))

    fresh = await repository.external_ids_needing_refresh(
        ["existing", "missing"],
        stale_before=utc_now() - timedelta(hours=1),
    )
    stale = await repository.external_ids_needing_refresh(
        ["existing"],
        stale_before=utc_now() + timedelta(seconds=1),
    )

    assert fresh == {"missing"}
    assert stale == {"existing"}


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
async def test_manual_application_persists_source_date_and_history(
    database: Database,
) -> None:
    vacancy_repository = VacancyRepository(database)
    lifecycle_repository = ApplicationRepository(database)
    item, _ = await vacancy_repository.create_if_new(vacancy_data("manual-1"))

    applied = await lifecycle_repository.mark_applied_manual(item.id)
    repeated = await lifecycle_repository.mark_applied_manual(item.id)
    history = await lifecycle_repository.history(item.id)

    assert applied.status == "applied_manual"
    assert applied.application_source == "manual"
    assert applied.status_source == "manual"
    assert applied.applied_at is not None
    assert repeated.id == applied.id
    assert len(history) == 1
    assert history[0].from_status == "new"
    assert history[0].to_status == "applied_manual"
    assert history[0].source == "manual"


@pytest.mark.asyncio
async def test_terminal_lifecycle_statuses_are_excluded_from_recommendations(
    database: Database,
) -> None:
    vacancy_repository = VacancyRepository(database)
    lifecycle_repository = ApplicationRepository(database)
    items = []
    for external_id in [
        "manual",
        "bot",
        "interview",
        "hidden",
        "rejected",
        "saved",
    ]:
        item, _ = await vacancy_repository.create_if_new(vacancy_data(external_id))
        await vacancy_repository.save_analysis(item.id, analysis(), "test-model")
        items.append(item)

    await lifecycle_repository.mark_applied_manual(items[0].id)
    await lifecycle_repository.mark_applied_bot(items[1].id)
    await lifecycle_repository.mark_applied_manual(items[2].id)
    await lifecycle_repository.set_status(items[2].id, "interview", source="employer")
    await lifecycle_repository.hide(items[3].id)
    await lifecycle_repository.reject(items[4].id)
    await lifecycle_repository.toggle_saved(items[5].id)

    recommendations = await vacancy_repository.list_digest_candidates(
        65, 20, only_unsent=False
    )
    applied = await vacancy_repository.list_applied(limit=20)
    stats = await vacancy_repository.stats()

    assert [vacancy.id for vacancy in recommendations] == [items[5].id]
    assert {vacancy.id for vacancy in applied} == {
        items[0].id,
        items[1].id,
        items[2].id,
    }
    assert stats.applied == 3
    assert stats.saved == 1


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


@pytest.mark.asyncio
async def test_active_ui_message_is_persisted_across_process_sessions(
    database: Database,
) -> None:
    repository = UIStateRepository(database, telegram_user_id=42)

    await repository.save(
        chat_id=42,
        message_id=777,
        screen="collection",
        collection_ids=[10, 20, 30],
        collection_title="Избранное",
        collection_kind="saved",
        collection_index=1,
        expanded=True,
        pending_vacancy_id=30,
    )
    restored = await repository.get(42)

    assert restored is not None
    assert restored.message_id == 777
    assert restored.screen == "collection"
    assert restored.collection_ids == [10, 20, 30]
    assert restored.collection_title == "Избранное"
    assert restored.collection_kind == "saved"
    assert restored.collection_index == 1
    assert restored.expanded is True
    assert restored.pending_vacancy_id == 30

    class FakeBot:
        edited = 0
        sent = 0

        async def edit_message_text(self, **kwargs: object) -> SimpleNamespace:
            self.edited += 1
            return SimpleNamespace(message_id=777)

        async def send_message(self, **kwargs: object) -> SimpleNamespace:
            self.sent += 1
            return SimpleNamespace(message_id=888)

        async def delete_message(self, **kwargs: object) -> None:
            return None

    bot = FakeBot()
    ui = UIManager(repository)
    await ui.render_chat(bot, 42, "Восстановленный экран")  # type: ignore[arg-type]

    session = ui.session(42)
    assert bot.edited == 1
    assert bot.sent == 0
    assert session.collection_ids == [10, 20, 30]
    assert session.current_vacancy_id == 20
    assert session.collection_kind == "saved"
    assert session.pending_vacancy_id == 30

    await repository.delete(42)
    assert await repository.get(42) is None
