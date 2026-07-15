from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Vacancy(Base):
    __tablename__ = "vacancies"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_vacancy_source_external"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32), default="hh", index=True)
    external_id: Mapped[str] = mapped_column(String(128), index=True)
    title: Mapped[str] = mapped_column(String(500))
    company: Mapped[str] = mapped_column(String(500), default="Не указана")
    url: Mapped[str] = mapped_column(String(1000))
    description: Mapped[str] = mapped_column(Text, default="")
    requirements: Mapped[str] = mapped_column(Text, default="")
    responsibilities: Mapped[str] = mapped_column(Text, default="")
    key_skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    salary_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    salary_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    salary_currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    salary_gross: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    location: Mapped[str] = mapped_column(String(500), default="Не указана")
    work_format: Mapped[str] = mapped_column(String(250), default="Не указан")
    experience: Mapped[str] = mapped_column(String(250), default="Не указан")
    employment: Mapped[str] = mapped_column(String(250), default="Не указана")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    is_sent: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    analysis: Mapped[VacancyAnalysis | None] = relationship(
        back_populates="vacancy", cascade="all, delete-orphan", uselist=False
    )
    application: Mapped[Application | None] = relationship(
        back_populates="vacancy", cascade="all, delete-orphan", uselist=False
    )


class VacancyAnalysis(Base):
    __tablename__ = "vacancy_analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    vacancy_id: Mapped[int] = mapped_column(
        ForeignKey("vacancies.id", ondelete="CASCADE"), unique=True, index=True
    )
    match_score: Mapped[int] = mapped_column(Integer, index=True)
    decision: Mapped[str] = mapped_column(String(32))
    role_level: Mapped[str] = mapped_column(String(32))
    matched_skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    missing_skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    blocking_requirements: Mapped[list[str]] = mapped_column(JSON, default=list)
    advantages: Mapped[list[str]] = mapped_column(JSON, default=list)
    risks: Mapped[list[str]] = mapped_column(JSON, default=list)
    resume_focus: Mapped[list[str]] = mapped_column(JSON, default=list)
    reason: Mapped[str] = mapped_column(Text)
    model_name: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    vacancy: Mapped[Vacancy] = relationship(back_populates="analysis")


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    vacancy_id: Mapped[int] = mapped_column(
        ForeignKey("vacancies.id", ondelete="CASCADE"), unique=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="new", index=True)
    cover_letter: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    vacancy: Mapped[Vacancy] = relationship(back_populates="application")


JsonDict = dict[str, Any]
