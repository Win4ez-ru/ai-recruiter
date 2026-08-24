from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.filters.state import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.bot.context import BotContext
from app.bot.views import (
    run_search,
    show_collection_kind,
    show_help,
    show_hh,
    show_main,
    show_profile,
    show_stats,
)


def build_handlers_router(context: BotContext) -> Router:
    router = Router(name="commands")

    async def authorized(message: Message) -> bool:
        if (
            message.from_user
            and message.from_user.id == context.settings.telegram_user_id
        ):
            return True
        await message.answer("Этот бот является приватным.")
        return False

    async def begin(message: Message, state: FSMContext) -> bool:
        if not await authorized(message):
            return False
        await context.ui.restore(message.chat.id)
        context.ui.cancel_operation(message.chat.id)
        context.ui.set_screen(message.chat.id, "navigation")
        await state.clear()
        return True

    @router.message(Command("start"))
    async def start_handler(message: Message, state: FSMContext) -> None:
        if await begin(message, state):
            await show_main(context, message)

    @router.message(Command("help"))
    async def help_handler(message: Message, state: FSMContext) -> None:
        if await begin(message, state):
            await show_help(context, message)

    @router.message(Command("search"))
    async def search_handler(message: Message, state: FSMContext) -> None:
        if await begin(message, state):
            await run_search(context, message)

    @router.message(Command("new"))
    async def new_handler(message: Message, state: FSMContext) -> None:
        if await begin(message, state):
            await show_collection_kind(context, message, "new")

    @router.message(Command("top"))
    async def top_handler(message: Message, state: FSMContext) -> None:
        if await begin(message, state):
            await show_collection_kind(context, message, "top")

    @router.message(Command("saved"))
    async def saved_handler(message: Message, state: FSMContext) -> None:
        if await begin(message, state):
            await show_collection_kind(context, message, "saved")

    @router.message(Command("applied"))
    async def applied_handler(message: Message, state: FSMContext) -> None:
        if await begin(message, state):
            await show_collection_kind(context, message, "applied")

    @router.message(Command("stats"))
    async def stats_handler(message: Message, state: FSMContext) -> None:
        if await begin(message, state):
            await show_stats(context, message)

    @router.message(Command("profile"))
    async def profile_handler(message: Message, state: FSMContext) -> None:
        if await begin(message, state):
            await show_profile(context, message)

    @router.message(Command("hh"))
    async def hh_handler(message: Message, state: FSMContext) -> None:
        if await begin(message, state):
            await show_hh(context, message)

    @router.message(StateFilter(None))
    async def private_fallback(message: Message, state: FSMContext) -> None:
        if await begin(message, state):
            await show_main(
                context,
                message,
                notice="Не распознал сообщение — выберите действие кнопкой.",
            )

    return router
