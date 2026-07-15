import logging

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, url: str, *, echo: bool = False) -> None:
        self.engine: AsyncEngine = create_async_engine(url, echo=echo)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def create_tables(self) -> None:
        from app import models  # noqa: F401

        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        logger.info("Database tables are ready")

    async def close(self) -> None:
        await self.engine.dispose()
