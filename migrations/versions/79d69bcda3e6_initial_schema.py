"""Create the initial application schema.

Revision ID: 79d69bcda3e6
Revises:
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "79d69bcda3e6"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "hh_resumes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_user_id", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=True),
        sa.Column("url", sa.String(length=1000), nullable=True),
        sa.Column("external_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "telegram_user_id",
            "external_id",
            name="uq_hh_resume_user_external",
        ),
    )
    op.create_index(
        op.f("ix_hh_resumes_is_default"),
        "hh_resumes",
        ["is_default"],
        unique=False,
    )
    op.create_index(
        op.f("ix_hh_resumes_telegram_user_id"),
        "hh_resumes",
        ["telegram_user_id"],
        unique=False,
    )
    op.create_table(
        "oauth_states",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("code_verifier", sa.String(length=160), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_oauth_states_expires_at"),
        "oauth_states",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_oauth_states_state_hash"),
        "oauth_states",
        ["state_hash"],
        unique=True,
    )
    op.create_index(
        op.f("ix_oauth_states_telegram_user_id"),
        "oauth_states",
        ["telegram_user_id"],
        unique=False,
    )
    op.create_table(
        "user_integrations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scope", sa.Text(), nullable=True),
        sa.Column("external_user_id", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "telegram_user_id",
            "provider",
            name="uq_user_integration_provider",
        ),
    )
    op.create_index(
        op.f("ix_user_integrations_telegram_user_id"),
        "user_integrations",
        ["telegram_user_id"],
        unique=False,
    )
    op.create_table(
        "vacancies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("company", sa.String(length=500), nullable=False),
        sa.Column("url", sa.String(length=1000), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("requirements", sa.Text(), nullable=False),
        sa.Column("responsibilities", sa.Text(), nullable=False),
        sa.Column("key_skills", sa.JSON(), nullable=False),
        sa.Column("salary_from", sa.Integer(), nullable=True),
        sa.Column("salary_to", sa.Integer(), nullable=True),
        sa.Column("salary_currency", sa.String(length=16), nullable=True),
        sa.Column("salary_gross", sa.Boolean(), nullable=True),
        sa.Column("location", sa.String(length=500), nullable=False),
        sa.Column("work_format", sa.String(length=250), nullable=False),
        sa.Column("experience", sa.String(length=250), nullable=False),
        sa.Column("employment", sa.String(length=250), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_sent", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source",
            "external_id",
            name="uq_vacancy_source_external",
        ),
    )
    op.create_index(
        op.f("ix_vacancies_external_id"),
        "vacancies",
        ["external_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_vacancies_is_sent"),
        "vacancies",
        ["is_sent"],
        unique=False,
    )
    op.create_index(
        op.f("ix_vacancies_source"),
        "vacancies",
        ["source"],
        unique=False,
    )
    op.create_table(
        "applications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("vacancy_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("cover_letter", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["vacancy_id"],
            ["vacancies.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_applications_status"),
        "applications",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_applications_vacancy_id"),
        "applications",
        ["vacancy_id"],
        unique=True,
    )
    op.create_table(
        "hh_applications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_user_id", sa.Integer(), nullable=False),
        sa.Column("vacancy_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("vacancy_external_id", sa.String(length=128), nullable=False),
        sa.Column("resume_external_id", sa.String(length=128), nullable=False),
        sa.Column("cover_letter", sa.Text(), nullable=False),
        sa.Column("process_status", sa.String(length=32), nullable=False),
        sa.Column("api_status", sa.String(length=32), nullable=False),
        sa.Column("external_application_id", sa.String(length=128), nullable=True),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitting_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["vacancy_id"],
            ["vacancies.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "telegram_user_id",
            "source",
            "vacancy_external_id",
            "resume_external_id",
            name="uq_hh_application_identity",
        ),
    )
    op.create_index(
        op.f("ix_hh_applications_api_status"),
        "hh_applications",
        ["api_status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_hh_applications_process_status"),
        "hh_applications",
        ["process_status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_hh_applications_telegram_user_id"),
        "hh_applications",
        ["telegram_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_hh_applications_vacancy_id"),
        "hh_applications",
        ["vacancy_id"],
        unique=False,
    )
    op.create_table(
        "vacancy_analyses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("vacancy_id", sa.Integer(), nullable=False),
        sa.Column("match_score", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("role_level", sa.String(length=32), nullable=False),
        sa.Column("matched_skills", sa.JSON(), nullable=False),
        sa.Column("missing_skills", sa.JSON(), nullable=False),
        sa.Column("blocking_requirements", sa.JSON(), nullable=False),
        sa.Column("advantages", sa.JSON(), nullable=False),
        sa.Column("risks", sa.JSON(), nullable=False),
        sa.Column("resume_focus", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("model_name", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["vacancy_id"],
            ["vacancies.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_vacancy_analyses_match_score"),
        "vacancy_analyses",
        ["match_score"],
        unique=False,
    )
    op.create_index(
        op.f("ix_vacancy_analyses_vacancy_id"),
        "vacancy_analyses",
        ["vacancy_id"],
        unique=True,
    )
    op.create_table(
        "application_confirmations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_user_id", sa.Integer(), nullable=False),
        sa.Column("vacancy_id", sa.Integer(), nullable=False),
        sa.Column("resume_external_id", sa.String(length=128), nullable=False),
        sa.Column("application_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["hh_applications.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["vacancy_id"],
            ["vacancies.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_application_confirmations_application_id"),
        "application_confirmations",
        ["application_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_application_confirmations_expires_at"),
        "application_confirmations",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_application_confirmations_telegram_user_id"),
        "application_confirmations",
        ["telegram_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_application_confirmations_token_hash"),
        "application_confirmations",
        ["token_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_application_confirmations_token_hash"),
        table_name="application_confirmations",
    )
    op.drop_index(
        op.f("ix_application_confirmations_telegram_user_id"),
        table_name="application_confirmations",
    )
    op.drop_index(
        op.f("ix_application_confirmations_expires_at"),
        table_name="application_confirmations",
    )
    op.drop_index(
        op.f("ix_application_confirmations_application_id"),
        table_name="application_confirmations",
    )
    op.drop_table("application_confirmations")
    op.drop_index(
        op.f("ix_vacancy_analyses_vacancy_id"),
        table_name="vacancy_analyses",
    )
    op.drop_index(
        op.f("ix_vacancy_analyses_match_score"),
        table_name="vacancy_analyses",
    )
    op.drop_table("vacancy_analyses")
    op.drop_index(
        op.f("ix_hh_applications_vacancy_id"),
        table_name="hh_applications",
    )
    op.drop_index(
        op.f("ix_hh_applications_telegram_user_id"),
        table_name="hh_applications",
    )
    op.drop_index(
        op.f("ix_hh_applications_process_status"),
        table_name="hh_applications",
    )
    op.drop_index(
        op.f("ix_hh_applications_api_status"),
        table_name="hh_applications",
    )
    op.drop_table("hh_applications")
    op.drop_index(
        op.f("ix_applications_vacancy_id"),
        table_name="applications",
    )
    op.drop_index(
        op.f("ix_applications_status"),
        table_name="applications",
    )
    op.drop_table("applications")
    op.drop_index(op.f("ix_vacancies_source"), table_name="vacancies")
    op.drop_index(op.f("ix_vacancies_is_sent"), table_name="vacancies")
    op.drop_index(op.f("ix_vacancies_external_id"), table_name="vacancies")
    op.drop_table("vacancies")
    op.drop_index(
        op.f("ix_user_integrations_telegram_user_id"),
        table_name="user_integrations",
    )
    op.drop_table("user_integrations")
    op.drop_index(
        op.f("ix_oauth_states_telegram_user_id"),
        table_name="oauth_states",
    )
    op.drop_index(
        op.f("ix_oauth_states_state_hash"),
        table_name="oauth_states",
    )
    op.drop_index(
        op.f("ix_oauth_states_expires_at"),
        table_name="oauth_states",
    )
    op.drop_table("oauth_states")
    op.drop_index(
        op.f("ix_hh_resumes_telegram_user_id"),
        table_name="hh_resumes",
    )
    op.drop_index(
        op.f("ix_hh_resumes_is_default"),
        table_name="hh_resumes",
    )
    op.drop_table("hh_resumes")
