"""Persist the active Telegram UI screen and collection state.

Revision ID: c824df1f6a91
Revises: 79d69bcda3e6
Create Date: 2026-07-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c824df1f6a91"
down_revision: str | Sequence[str] | None = "79d69bcda3e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bot_ui_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_user_id", sa.Integer(), nullable=False),
        sa.Column("chat_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("screen", sa.String(length=64), nullable=False),
        sa.Column("collection_ids", sa.JSON(), nullable=False),
        sa.Column("collection_title", sa.String(length=128), nullable=False),
        sa.Column("collection_index", sa.Integer(), nullable=False),
        sa.Column("expanded", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_bot_ui_sessions_chat_id"),
        "bot_ui_sessions",
        ["chat_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_bot_ui_sessions_telegram_user_id"),
        "bot_ui_sessions",
        ["telegram_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_bot_ui_sessions_telegram_user_id"),
        table_name="bot_ui_sessions",
    )
    op.drop_index(op.f("ix_bot_ui_sessions_chat_id"), table_name="bot_ui_sessions")
    op.drop_table("bot_ui_sessions")
