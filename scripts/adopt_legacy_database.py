from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from dotenv import load_dotenv
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app import models  # noqa: F401
from app.database import Base, normalize_database_url

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _compare_schema(connection: Connection) -> tuple[bool, list[object]]:
    inspector = inspect(connection)
    if "alembic_version" in inspector.get_table_names():
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one_or_none()
        if revision:
            raise RuntimeError(f"База уже управляется Alembic (revision {revision}).")
    context = MigrationContext.configure(
        connection,
        opts={"compare_type": True, "compare_server_default": False},
    )
    differences = list(compare_metadata(context, Base.metadata))
    return not differences, differences


async def _check(database_url: str) -> tuple[bool, list[object]]:
    engine = create_async_engine(normalize_database_url(database_url))
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(_compare_schema)
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Проверить legacy-базу, созданную через create_all, и при точном "
            "совпадении пометить её текущей revision Alembic."
        )
    )
    parser.add_argument(
        "--stamp",
        action="store_true",
        help="после успешной проверки выполнить `alembic stamp head`",
    )
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")
    database_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./job_agent.db")
    try:
        equivalent, differences = asyncio.run(_check(database_url))
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    if not equivalent:
        print("Схема legacy-базы не совпадает с текущими моделями:")
        for difference in differences[:20]:
            print(f"- {difference!r}")
        raise SystemExit(
            "Stamp отменён. Создайте резервную копию и перенесите данные "
            "в новую базу через поддерживаемые миграции."
        )

    print("Схема точно совпадает с текущими SQLAlchemy-моделями.")
    if not args.stamp:
        print("Проверка read-only. Для принятия схемы повторите с --stamp.")
        return

    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    command.stamp(config, "head")
    print("База принята Alembic на revision head.")


if __name__ == "__main__":
    main()
