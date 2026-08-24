from __future__ import annotations

from sqlalchemy import select

from app.database import Database
from app.models import BotUISession


class UIStateRepository:
    def __init__(self, database: Database, telegram_user_id: int) -> None:
        self.database = database
        self.telegram_user_id = telegram_user_id

    async def get(self, chat_id: int) -> BotUISession | None:
        async with self.database.session_factory() as session:
            return await session.scalar(
                select(BotUISession).where(BotUISession.chat_id == chat_id)
            )

    async def save(
        self,
        chat_id: int,
        message_id: int,
        screen: str,
        *,
        collection_ids: list[int],
        collection_title: str,
        collection_kind: str = "custom",
        collection_index: int,
        expanded: bool,
        pending_vacancy_id: int | None = None,
    ) -> BotUISession:
        async with self.database.session_factory() as session:
            row = await session.scalar(
                select(BotUISession).where(BotUISession.chat_id == chat_id)
            )
            if row is None:
                row = BotUISession(
                    telegram_user_id=self.telegram_user_id,
                    chat_id=chat_id,
                    message_id=message_id,
                    screen=screen,
                    collection_ids=collection_ids,
                    collection_title=collection_title,
                    collection_kind=collection_kind,
                    collection_index=collection_index,
                    expanded=expanded,
                    pending_vacancy_id=pending_vacancy_id,
                )
                session.add(row)
            else:
                row.message_id = message_id
                row.screen = screen
                row.collection_ids = collection_ids
                row.collection_title = collection_title
                row.collection_kind = collection_kind
                row.collection_index = collection_index
                row.expanded = expanded
                row.pending_vacancy_id = pending_vacancy_id
            await session.commit()
            await session.refresh(row)
            return row

    async def delete(self, chat_id: int) -> None:
        async with self.database.session_factory() as session:
            row = await session.scalar(
                select(BotUISession).where(BotUISession.chat_id == chat_id)
            )
            if row is None:
                return
            await session.delete(row)
            await session.commit()
