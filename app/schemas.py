from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ApplicationStatus = Literal[
    "new",
    "viewed",
    "saved",
    "applied_manual",
    "applied_bot",
    "hidden",
    "interview",
    "test_task",
    "rejected",
    "offer",
    "offer_accepted",
    "archived",
]
Decision = Literal["strong_apply", "apply", "maybe", "skip"]
RoleLevel = Literal[
    "intern", "junior", "junior_plus", "middle", "senior", "lead", "unknown"
]
SearchErrorCode = Literal[
    "hh_configuration",
    "hh_forbidden",
    "hh_rate_limited",
    "hh_unavailable",
    "ai_rate_limited",
    "ai_unavailable",
    "ai_configuration",
    "ai_invalid_response",
]


class Education(BaseModel):
    university: str
    program: str
    graduation_year: int


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
    ai_requests: int = 0
    cached_analyses: int = 0
    suitable: int = 0
    errors: int = 0
    error_codes: list[SearchErrorCode] = Field(default_factory=list)
    hh_duration_seconds: float = Field(default=0.0, ge=0)
    ai_duration_seconds: float = Field(default=0.0, ge=0)


class StatsResult(BaseModel):
    total_vacancies: int
    analyzed: int
    saved: int
    applied: int
    interviews: int
    test_tasks: int
    offers: int
    accepted_offers: int
    rejected: int
    average_score: float
    common_missing_skills: list[tuple[str, int]]


HHApplicationStatus = Literal[
    "demo",
    "draft",
    "awaiting_confirmation",
    "submitting",
    "submitted",
    "failed",
    "manual_action_required",
]


class HHResumeData(BaseModel):
    local_id: int | None = None
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
    manual_submission_required: bool = False


class ApplicationResult(BaseModel):
    status: HHApplicationStatus
    message: str
    vacancy_id: int | None = None
    application_id: int | None = None
    external_id: str | None = None
    manual_url: str | None = None
    error_code: str | None = None
    can_retry: bool = False
    can_mark_applied: bool = False
    requires_oauth: bool = False
    result_uncertain: bool = False
