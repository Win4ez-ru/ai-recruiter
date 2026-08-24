import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

logger = logging.getLogger(__name__)


def normalize_database_url(url: str) -> str:
    """Normalize common PaaS PostgreSQL URLs for SQLAlchemy asyncpg."""

    if url.startswith("postgres://"):
        return f"postgresql+asyncpg://{url.removeprefix('postgres://')}"
    if url.startswith("postgresql://"):
        return f"postgresql+asyncpg://{url.removeprefix('postgresql://')}"
    return url


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(
        self,
        url: str,
        *,
        echo: bool = False,
        connect_timeout_seconds: float = 10.0,
        pool_size: int = 5,
        max_overflow: int = 10,
        pool_recycle_seconds: int = 1800,
    ) -> None:
        normalized_url = normalize_database_url(url)
        engine_options: dict[str, object] = {
            "echo": echo,
            "pool_pre_ping": True,
            "pool_recycle": pool_recycle_seconds,
        }
        if normalized_url.startswith("postgresql+asyncpg://"):
            engine_options.update(
                {
                    "connect_args": {"timeout": connect_timeout_seconds},
                    "pool_size": pool_size,
                    "max_overflow": max_overflow,
                }
            )
        elif normalized_url.startswith("sqlite+aiosqlite://"):
            engine_options["connect_args"] = {"timeout": connect_timeout_seconds}
        self.engine: AsyncEngine = create_async_engine(
            normalized_url,
            **engine_options,
        )
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def create_tables(self) -> None:
        from app import models  # noqa: F401

        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        logger.info("Database tables are ready")

    async def check_connection(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        logger.info("Database connection is ready")

    async def close(self) -> None:
        await self.engine.dispose()
