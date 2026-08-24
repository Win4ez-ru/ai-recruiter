from __future__ import annotations

from app.models import Vacancy, VacancyAnalysis
from app.schemas import (
    CandidateProfile,
    CandidateProject,
    Education,
    ExperienceProfile,
    VacancyFilterResult,
)
from app.services.vacancy_ranker import VacancyRanker


class FakeProvider:
    def __init__(self, name: str) -> None:
        self.name = name


def profile() -> CandidateProfile:
    return CandidateProfile(
        candidate_name="Demo Candidate",
        target_roles=["Junior iOS Developer"],
        location="Санкт-Петербург",
        remote_allowed=True,
        relocation_allowed=False,
        minimum_salary_rub=90_000,
        preferred_salary_rub=130_000,
        education=Education(
            university="Технический университет",
            program="Программная инженерия",
            graduation_year=2026,
        ),
        experience=ExperienceProfile(
            commercial_ios_experience=False,
            personal_projects=True,
            team_experience=True,
        ),
        strong_skills=["Swift", "SwiftUI"],
        basic_skills=["UIKit"],
        projects=[CandidateProject(name="Portfolio", description="iOS app")],
        hard_rejections=["обязательный опыт 4+ года"],
    )


def vacancy() -> Vacancy:
    return Vacancy(
        id=1,
        external_id="hh-1",
        title="Junior iOS Developer",
        company="Acme",
        url="https://hh.ru/vacancy/1",
        description="Swift и SwiftUI",
        requirements="Знание Swift",
        responsibilities="Разработка приложения",
        key_skills=["Swift"],
        location="Санкт-Петербург",
        work_format="Удалённо",
        experience="1–3 года",
        employment="Полная занятость",
    )


def filter_result() -> VacancyFilterResult:
    return VacancyFilterResult(
        is_relevant=True,
        reasons=["iOS/Swift"],
        detected_positive_keywords=["Swift"],
    )


def test_analysis_fingerprint_changes_with_inputs_and_provider() -> None:
    item = vacancy()
    prefilter = filter_result()
    ranker = VacancyRanker(
        FakeProvider("ollama"),  # type: ignore[arg-type]
        "qwen3:4b-instruct",
        profile(),
        "Резюме кандидата",
    )
    fingerprint = ranker.input_hash(item, prefilter)
    item.analysis = VacancyAnalysis(
        vacancy_id=item.id,
        match_score=80,
        decision="apply",
        role_level="junior",
        matched_skills=["Swift"],
        missing_skills=[],
        blocking_requirements=[],
        advantages=[],
        risks=[],
        resume_focus=[],
        reason="Подходит",
        model_name=ranker.model_name,
        provider=ranker.provider_name,
        prompt_version=ranker.prompt_version,
        input_hash=fingerprint,
    )

    assert len(fingerprint) == 64
    assert ranker.analysis_is_current(item, prefilter) is True

    item.description = "Swift, SwiftUI и обязательный UIKit"
    assert ranker.analysis_is_current(item, prefilter) is False

    yandex_ranker = VacancyRanker(
        FakeProvider("yandex"),  # type: ignore[arg-type]
        "gpt://folder/yandexgpt-5.1",
        profile(),
        "Резюме кандидата",
    )
    assert yandex_ranker.analysis_is_current(item, prefilter) is False
