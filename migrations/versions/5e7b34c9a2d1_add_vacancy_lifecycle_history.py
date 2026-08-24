"""Add scalable vacancy lifecycle state and transition history.

Revision ID: 5e7b34c9a2d1
Revises: c824df1f6a91
Create Date: 2026-07-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5e7b34c9a2d1"
down_revision: str | Sequence[str] | None = "c824df1f6a91"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column(
            "status_source",
            sa.String(length=32),
            nullable=False,
            server_default="system",
        ),
    )
    op.add_column(
        "applications",
        sa.Column("application_source", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "applications",
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.execute(
        sa.text(
            "UPDATE applications "
            "SET status = 'applied_bot', application_source = 'bot', "
            "status_source = 'migration' WHERE status = 'applied'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE applications SET status = 'hidden', "
            "status_source = 'migration' WHERE status = 'skipped'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE applications SET status_changed_at = "
            "COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)"
        )
    )

    with op.batch_alter_table("applications") as batch_op:
        batch_op.alter_column(
            "status_changed_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )
        batch_op.alter_column(
            "status_source",
            existing_type=sa.String(length=32),
            server_default=None,
        )
        batch_op.create_index(
            op.f("ix_applications_status_changed_at"),
            ["status_changed_at"],
            unique=False,
        )

    op.create_table(
        "vacancy_status_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("vacancy_id", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column(
            "details",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["vacancy_id"], ["vacancies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_vacancy_status_history_vacancy_id"),
        "vacancy_status_history",
        ["vacancy_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_vacancy_status_history_to_status"),
        "vacancy_status_history",
        ["to_status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_vacancy_status_history_source"),
        "vacancy_status_history",
        ["source"],
        unique=False,
    )
    op.create_index(
        op.f("ix_vacancy_status_history_changed_at"),
        "vacancy_status_history",
        ["changed_at"],
        unique=False,
    )

    op.execute(
        sa.text(
            "INSERT INTO vacancy_status_history "
            "(vacancy_id, from_status, to_status, source, reason, changed_at) "
            "SELECT vacancy_id, NULL, status, 'migration', "
            "'Initial lifecycle state migrated from applications', "
            "status_changed_at FROM applications"
        )
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_vacancy_status_history_changed_at"),
        table_name="vacancy_status_history",
    )
    op.drop_index(
        op.f("ix_vacancy_status_history_source"),
        table_name="vacancy_status_history",
    )
    op.drop_index(
        op.f("ix_vacancy_status_history_to_status"),
        table_name="vacancy_status_history",
    )
    op.drop_index(
        op.f("ix_vacancy_status_history_vacancy_id"),
        table_name="vacancy_status_history",
    )
    op.drop_table("vacancy_status_history")

    op.execute(
        sa.text(
            "UPDATE applications SET status = CASE "
            "WHEN status IN ('applied_manual', 'applied_bot') THEN 'applied' "
            "WHEN status = 'hidden' THEN 'skipped' "
            "WHEN status = 'viewed' THEN 'new' "
            "WHEN status = 'offer_accepted' THEN 'offer' "
            "WHEN status = 'archived' THEN 'skipped' "
            "ELSE status END"
        )
    )

    with op.batch_alter_table("applications") as batch_op:
        batch_op.drop_index(op.f("ix_applications_status_changed_at"))
        batch_op.drop_column("status_changed_at")
        batch_op.drop_column("application_source")
        batch_op.drop_column("status_source")
