from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.schemas import (
    CandidateProfile,
    CandidateProject,
    Education,
    ExperienceProfile,
)
from app.services.candidate_profile import (
    CandidateProfileError,
    load_candidate_profile,
    load_resume,
)
from app.services.cover_letter import (
    MAX_DESCRIPTION_CHARS,
    MAX_REQUIREMENTS_CHARS,
    MAX_RESUME_CHARS,
    CoverLetterService,
)


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


def test_profile_and_resume_loaders_validate_local_files(tmp_path) -> None:
    profile_path = tmp_path / "profile.json"
    resume_path = tmp_path / "resume.txt"
    profile_path.write_text(profile().model_dump_json(), encoding="utf-8")
    resume_path.write_text("  Резюме кандидата  ", encoding="utf-8")

    loaded_profile = load_candidate_profile(profile_path)
    loaded_resume = load_resume(resume_path)

    assert loaded_profile.candidate_name == "Demo Candidate"
    assert loaded_resume == "Резюме кандидата"


@pytest.mark.parametrize("kind", ["missing", "invalid", "empty_resume"])
def test_profile_and_resume_loaders_report_safe_errors(tmp_path, kind: str) -> None:
    path = tmp_path / "candidate-data"
    if kind == "invalid":
        path.write_text("not-json", encoding="utf-8")
    elif kind == "empty_resume":
        path.write_text("  ", encoding="utf-8")

    with pytest.raises(CandidateProfileError):
        if kind == "empty_resume":
            load_resume(path)
        else:
            load_candidate_profile(path)


@pytest.mark.asyncio
async def test_cover_letter_uses_provider_neutral_client_and_vacancy_context() -> None:
    class Provider:
        name = "yandex"

        def __init__(self) -> None:
            self.prompt = ""

        async def generate_text(self, *, model: str, prompt: str) -> str:
            self.prompt = prompt
            return "  Готовое персональное письмо.  "

    provider = Provider()
    vacancy = SimpleNamespace(
        id=7,
        title="Junior iOS-разработчик",
        company="Acme",
        description="Разработка на SwiftUI" + "D" * MAX_DESCRIPTION_CHARS + "DESC_TAIL",
        requirements="Swift и REST API" + "Q" * MAX_REQUIREMENTS_CHARS + "REQ_TAIL",
        key_skills=["Swift", "SwiftUI"],
        analysis=None,
    )
    service = CoverLetterService(
        provider,  # type: ignore[arg-type]
        "gpt://folder/yandexgpt-5.1",
        profile(),
        "Резюме кандидата" + "R" * MAX_RESUME_CHARS + "RESUME_TAIL",
    )

    letter = await service.generate(vacancy)  # type: ignore[arg-type]

    assert letter == "Готовое персональное письмо."
    assert "Язык: русский" in provider.prompt
    assert "Junior iOS-разработчик" in provider.prompt
    assert "Резюме кандидата" in provider.prompt
    assert "DESC_TAIL" not in provider.prompt
    assert "REQ_TAIL" not in provider.prompt
    assert "RESUME_TAIL" not in provider.prompt
