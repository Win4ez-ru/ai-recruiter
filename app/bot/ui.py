from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from app.repositories.ui_state_repository import UIStateRepository

logger = logging.getLogger(__name__)
REPLACEABLE_EDIT_ERRORS = (
    "message to edit not found",
    "message can't be edited",
    "message can not be edited",
    "message_id_invalid",
)


@dataclass(slots=True)
class UISession:
    message_id: int | None = None
    screen: str = "menu"
    collection_ids: list[int] = field(default_factory=list)
    collection_title: str = "Вакансии"
    collection_kind: str = "custom"
    collection_index: int = 0
    expanded: bool = False
    pending_vacancy_id: int | None = None
    navigation_initialized: bool = False
    operation_generation: int = 0

    @property
    def current_vacancy_id(self) -> int | None:
        if not self.collection_ids:
            return None
        self.collection_index %= len(self.collection_ids)
        return self.collection_ids[self.collection_index]


class UIManager:
    """Owns the single app-like Telegram message and lightweight UI state."""

    def __init__(self, repository: UIStateRepository | None = None) -> None:
        self.repository = repository
        self._sessions: dict[int, UISession] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._hydrate_locks: dict[int, asyncio.Lock] = {}
        self._hydrated: set[int] = set()

    def session(self, chat_id: int) -> UISession:
        return self._sessions.setdefault(chat_id, UISession())

    async def restore(self, chat_id: int) -> UISession:
        await self._hydrate(chat_id)
        return self.session(chat_id)

    def set_screen(self, chat_id: int, screen: str) -> UISession:
        session = self.session(chat_id)
        session.screen = screen
        return session

    def set_collection(
        self,
        chat_id: int,
        vacancy_ids: list[int],
        *,
        title: str,
        kind: str = "custom",
        start_vacancy_id: int | None = None,
    ) -> UISession:
        session = self.session(chat_id)
        session.collection_ids = list(dict.fromkeys(vacancy_ids))
        session.collection_title = title
        session.collection_kind = kind
        session.collection_index = 0
        if start_vacancy_id in session.collection_ids:
            session.collection_index = session.collection_ids.index(start_vacancy_id)
        session.expanded = False
        session.navigation_initialized = True
        session.screen = "collection"
        return session

    def start_operation(self, chat_id: int) -> int:
        session = self.session(chat_id)
        session.operation_generation += 1
        return session.operation_generation

    def cancel_operation(self, chat_id: int) -> None:
        self.session(chat_id).operation_generation += 1

    def operation_is_current(self, chat_id: int, generation: int) -> bool:
        return self.session(chat_id).operation_generation == generation

    def set_pending_vacancy(self, chat_id: int, vacancy_id: int | None) -> None:
        self.session(chat_id).pending_vacancy_id = vacancy_id

    def focus_vacancy(self, chat_id: int, vacancy_id: int) -> UISession:
        session = self.session(chat_id)
        if vacancy_id not in session.collection_ids:
            return self.set_collection(
                chat_id,
                [vacancy_id],
                title="Вакансия",
                kind="custom",
            )
        session.collection_index = session.collection_ids.index(vacancy_id)
        session.expanded = False
        session.navigation_initialized = True
        return session

    def move(self, chat_id: int, delta: int) -> UISession:
        session = self.session(chat_id)
        session.navigation_initialized = True
        if session.collection_ids:
            session.collection_index = (session.collection_index + delta) % len(
                session.collection_ids
            )
        session.expanded = False
        session.screen = "collection"
        return session

    def toggle_expanded(self, chat_id: int) -> UISession:
        session = self.session(chat_id)
        session.navigation_initialized = True
        session.expanded = not session.expanded
        session.screen = "collection"
        return session

    def remove_from_collection(self, chat_id: int, vacancy_id: int) -> UISession:
        session = self.session(chat_id)
        session.navigation_initialized = True
        if vacancy_id not in session.collection_ids:
            return session
        index = session.collection_ids.index(vacancy_id)
        session.collection_ids.pop(index)
        if session.collection_ids:
            session.collection_index = min(index, len(session.collection_ids) - 1)
        else:
            session.collection_index = 0
        session.expanded = False
        return session

    async def render(
        self,
        target: Message | CallbackQuery,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
        *,
        screen: str | None = None,
    ) -> Message | None:
        if isinstance(target, CallbackQuery):
            if not isinstance(target.message, Message):
                return None
            message = target.message
            chat_id = message.chat.id
            await self._hydrate(chat_id)
            self.session(chat_id).message_id = message.message_id
            if screen:
                self.set_screen(chat_id, screen)
            return await self._edit_or_replace(
                bot=target.bot,
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                preferred=message,
            )

        chat_id = target.chat.id
        await self._hydrate(chat_id)
        if screen:
            self.set_screen(chat_id, screen)
        rendered = await self.render_chat(
            target.bot,
            chat_id,
            text,
            reply_markup,
        )
        if not (target.from_user and target.from_user.is_bot):
            await self._safe_delete(target)
        return rendered

    async def render_chat(
        self,
        bot: Bot,
        chat_id: int,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
        *,
        screen: str | None = None,
    ) -> Message | None:
        await self._hydrate(chat_id)
        if screen:
            self.set_screen(chat_id, screen)
        return await self._edit_or_replace(
            bot=bot,
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
        )

    async def close(self, callback: CallbackQuery) -> None:
        if not isinstance(callback.message, Message):
            return
        chat_id = callback.message.chat.id
        await self._safe_delete(callback.message)
        if self.repository is not None:
            try:
                await self.repository.delete(chat_id)
            except Exception:
                logger.exception("Could not delete persisted UI session")
        self._sessions.pop(chat_id, None)
        self._locks.pop(chat_id, None)
        self._hydrate_locks.pop(chat_id, None)
        self._hydrated.discard(chat_id)

    async def _edit_or_replace(
        self,
        *,
        bot: Bot,
        chat_id: int,
        text: str,
        reply_markup: InlineKeyboardMarkup | None,
        preferred: Message | None = None,
    ) -> Message | None:
        lock = self._locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            await self._hydrate(chat_id)
            session = self.session(chat_id)
            message_id = preferred.message_id if preferred else session.message_id
            if message_id is not None:
                try:
                    edited = await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=text,
                        reply_markup=reply_markup,
                    )
                    session.message_id = message_id
                    await self._persist(chat_id)
                    return edited if isinstance(edited, Message) else preferred
                except TelegramBadRequest as exc:
                    error_text = str(exc).lower()
                    if "message is not modified" in error_text:
                        session.message_id = message_id
                        await self._persist(chat_id)
                        return preferred
                    if not any(
                        marker in error_text for marker in REPLACEABLE_EDIT_ERRORS
                    ):
                        logger.warning(
                            "Telegram rejected UI edit; preserving existing message",
                            extra={
                                "event": "telegram_ui_edit_rejected",
                                "error_type": type(exc).__name__,
                            },
                        )
                        return None
                    logger.debug(
                        "Could not edit UI message %s: %s",
                        message_id,
                        type(exc).__name__,
                    )
                except TelegramAPIError as exc:
                    logger.warning(
                        "Transient Telegram error while editing UI; preserving message",
                        extra={
                            "event": "telegram_ui_edit_transient_error",
                            "error_type": type(exc).__name__,
                        },
                    )
                    return None

                try:
                    await bot.delete_message(chat_id=chat_id, message_id=message_id)
                except TelegramAPIError:
                    logger.debug("Could not remove stale UI message %s", message_id)

            try:
                sent = await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=reply_markup,
                )
            except TelegramAPIError:
                logger.exception("Could not render Telegram UI screen")
                return None
            session.message_id = sent.message_id
            await self._persist(chat_id)
            return sent

    async def _hydrate(self, chat_id: int) -> None:
        if chat_id in self._hydrated:
            return
        lock = self._hydrate_locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            if chat_id in self._hydrated:
                return
            self._hydrated.add(chat_id)
            if self.repository is None:
                return
            try:
                row = await self.repository.get(chat_id)
            except Exception:
                logger.exception("Could not restore persisted UI session")
                return
            if row is None:
                return
            session = self.session(chat_id)
            session.message_id = row.message_id
            if session.screen == "menu":
                session.screen = row.screen
            if not session.navigation_initialized:
                session.collection_ids = list(row.collection_ids or [])
                session.collection_title = row.collection_title
                session.collection_kind = row.collection_kind
                session.collection_index = row.collection_index
                if session.collection_ids:
                    session.collection_index %= len(session.collection_ids)
                else:
                    session.collection_index = 0
                session.expanded = row.expanded
                session.navigation_initialized = True
            session.pending_vacancy_id = row.pending_vacancy_id

    async def _persist(self, chat_id: int) -> None:
        if self.repository is None:
            return
        session = self.session(chat_id)
        if session.message_id is None:
            return
        try:
            await self.repository.save(
                chat_id,
                session.message_id,
                session.screen,
                collection_ids=session.collection_ids,
                collection_title=session.collection_title,
                collection_kind=session.collection_kind,
                collection_index=session.collection_index,
                expanded=session.expanded,
                pending_vacancy_id=session.pending_vacancy_id,
            )
        except Exception:
            logger.exception("Could not persist active UI message")

    @staticmethod
    async def _safe_delete(message: Message) -> None:
        try:
            await message.delete()
        except TelegramAPIError:
            logger.debug("Could not delete obsolete Telegram message")
