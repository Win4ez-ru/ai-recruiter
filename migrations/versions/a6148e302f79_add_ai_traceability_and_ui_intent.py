"""Add AI traceability and durable Telegram UI intent.

Revision ID: a6148e302f79
Revises: 5e7b34c9a2d1
Create Date: 2026-08-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a6148e302f79"
down_revision: str | Sequence[str] | None = "5e7b34c9a2d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _column_names(table_name: str) -> set[str]:
    return {
        str(column["name"])
        for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def _index_names(table_name: str) -> set[str]:
    return {
        str(index["name"])
        for index in sa.inspect(op.get_bind()).get_indexes(table_name)
        if index.get("name")
    }


def upgrade() -> None:
    analysis_columns = _column_names("vacancy_analyses")
    if "provider" not in analysis_columns:
        op.add_column(
            "vacancy_analyses",
            sa.Column(
                "provider",
                sa.String(length=32),
                server_default="legacy",
                nullable=False,
            ),
        )
    if "prompt_version" not in analysis_columns:
        op.add_column(
            "vacancy_analyses",
            sa.Column(
                "prompt_version",
                sa.String(length=64),
                server_default="legacy",
                nullable=False,
            ),
        )
    if "input_hash" not in analysis_columns:
        op.add_column(
            "vacancy_analyses",
            sa.Column("input_hash", sa.String(length=64), nullable=True),
        )
    if "updated_at" not in analysis_columns:
        op.add_column(
            "vacancy_analyses",
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.execute(
            sa.text(
                "UPDATE vacancy_analyses "
                "SET updated_at = created_at WHERE updated_at IS NULL"
            )
        )
        with op.batch_alter_table("vacancy_analyses") as batch_op:
            batch_op.alter_column(
                "updated_at",
                existing_type=sa.DateTime(timezone=True),
                nullable=False,
            )

    analysis_indexes = _index_names("vacancy_analyses")
    provider_index = op.f("ix_vacancy_analyses_provider")
    input_hash_index = op.f("ix_vacancy_analyses_input_hash")
    if provider_index not in analysis_indexes:
        op.create_index(
            provider_index,
            "vacancy_analyses",
            ["provider"],
            unique=False,
        )
    if input_hash_index not in analysis_indexes:
        op.create_index(
            input_hash_index,
            "vacancy_analyses",
            ["input_hash"],
            unique=False,
        )

    ui_columns = _column_names("bot_ui_sessions")
    if "collection_kind" not in ui_columns:
        op.add_column(
            "bot_ui_sessions",
            sa.Column(
                "collection_kind",
                sa.String(length=32),
                server_default="custom",
                nullable=False,
            ),
        )
    if "pending_vacancy_id" not in ui_columns:
        op.add_column(
            "bot_ui_sessions",
            sa.Column("pending_vacancy_id", sa.Integer(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("bot_ui_sessions", "pending_vacancy_id")
    op.drop_column("bot_ui_sessions", "collection_kind")
    op.drop_index(
        op.f("ix_vacancy_analyses_input_hash"),
        table_name="vacancy_analyses",
    )
    op.drop_index(
        op.f("ix_vacancy_analyses_provider"),
        table_name="vacancy_analyses",
    )
    op.drop_column("vacancy_analyses", "updated_at")
    op.drop_column("vacancy_analyses", "input_hash")
    op.drop_column("vacancy_analyses", "prompt_version")
    op.drop_column("vacancy_analyses", "provider")
