from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ApplicationStatus = Literal[
    "new",
    "saved",
    "applied",
    "interview",
    "test_task",
    "rejected",
    "offer",
    "skipped",
]
Decision = Literal["strong_apply", "apply", "maybe", "skip"]
RoleLevel = Literal[
    "intern", "junior", "junior_plus", "middle", "senior", "lead", "unknown"
]


class Education(BaseModel):
    university: str
    program: str
    year: int


class ExperienceProfile(BaseModel):
    commercial_ios_experience: bool
    personal_projects: bool
    team_experience: bool


class CandidateProject(BaseModel):
    name: str
    description: str


class CandidateProfile(BaseModel):
    candidate_name: str
    target_roles: list[str]
    location: str
    remote_allowed: bool
    relocation_allowed: bool
    minimum_salary_rub: int
    preferred_salary_rub: int
    education: Education
    experience: ExperienceProfile
    strong_skills: list[str]
    basic_skills: list[str]
    projects: list[CandidateProject]
    hard_rejections: list[str]


class VacancyCreate(BaseModel):
    source: str = "hh"
    external_id: str
    title: str
    company: str = "Не указана"
    url: str
    description: str = ""
    requirements: str = ""
    responsibilities: str = ""
    key_skills: list[str] = Field(default_factory=list)
    salary_from: int | None = None
    salary_to: int | None = None
    salary_currency: str | None = None
    salary_gross: bool | None = None
    location: str = "Не указана"
    work_format: str = "Не указан"
    experience: str = "Не указан"
    employment: str = "Не указана"
    published_at: datetime | None = None


class VacancyFilterResult(BaseModel):
    is_relevant: bool
    reasons: list[str] = Field(default_factory=list)
    detected_positive_keywords: list[str] = Field(default_factory=list)
    detected_negative_keywords: list[str] = Field(default_factory=list)


class VacancyAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    match_score: int = Field(ge=0, le=100)
    decision: Decision
    role_level: RoleLevel
    matched_skills: list[str]
    missing_skills: list[str]
    blocking_requirements: list[str]
    advantages: list[str]
    risks: list[str]
    resume_focus: list[str]
    reason: str


class SearchSummary(BaseModel):
    found: int = 0
    after_deduplication: int = 0
    new_vacancies: int = 0
    after_prefilter: int = 0
    analyzed: int = 0
    suitable: int = 0
    errors: int = 0


class StatsResult(BaseModel):
    total_vacancies: int
    analyzed: int
    saved: int
    applied: int
    interviews: int
    rejected: int
    average_score: float
    common_missing_skills: list[tuple[str, int]]


HHApplicationStatus = Literal[
    "draft",
    "awaiting_confirmation",
    "submitting",
    "submitted",
    "failed",
    "manual_action_required",
]


class HHResumeData(BaseModel):
    external_id: str
    title: str
    status: str | None = None
    url: str | None = None
    updated_at: datetime | None = None
    is_default: bool = False


class PreparedApplication(BaseModel):
    draft_id: int
    vacancy_id: int
    vacancy_title: str
    company: str
    vacancy_url: str
    resume: HHResumeData
    resumes: list[HHResumeData]
    cover_letter: str


class ApplicationResult(BaseModel):
    status: HHApplicationStatus
    message: str
    external_id: str | None = None
    manual_url: str | None = None
