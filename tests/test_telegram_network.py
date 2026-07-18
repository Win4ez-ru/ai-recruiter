from __future__ import annotations

import ssl
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import GetMe, GetUpdates, SendMessage, TelegramMethod
from aiogram.types import BotCommand
from aiogram.utils.backoff import BackoffConfig

from app.config import Settings
from app.network.telegram import (
    FailoverTelegramSession,
    TelegramRoute,
    build_telegram_session,
    wait_for_telegram,
)


class FakeSession(BaseSession):
    def __init__(self, *results: object) -> None:
        super().__init__()
        self.results = list(results)
        self.calls: list[str] = []
        self.closed = False

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[Any],
        timeout: int | None = None,
    ) -> Any:
        self.calls.append(method.__api_method__)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        yield b"content"

    async def close(self) -> None:
        self.closed = True


def network_error(method: TelegramMethod[Any]) -> TelegramNetworkError:
    return TelegramNetworkError(method=method, message="network unavailable")


@pytest.mark.asyncio
async def test_proxy_route_keeps_verified_tls_context() -> None:
    settings = Settings(
        telegram_bot_token="123456:TEST_TOKEN",
        telegram_user_id=42,
        telegram_direct_enabled=False,
        telegram_proxy_urls="socks5://proxy.example:1080",
        openai_api_key="test-key",
        openai_model="test-model",
    )

    session = build_telegram_session(settings)
    route_session = session._routes[0].session  # noqa: SLF001

    assert isinstance(route_session, AiohttpSession)
    assert isinstance(route_session._connector_init["ssl"], ssl.SSLContext)  # noqa: SLF001
    await session.close()


@pytest.mark.asyncio
async def test_safe_request_fails_over_to_proxy() -> None:
    method = GetMe()
    direct = FakeSession(network_error(method))
    proxy = FakeSession("proxy-result")
    session = FailoverTelegramSession(
        [TelegramRoute("direct", direct), TelegramRoute("proxy-1", proxy)],
        cooldown_seconds=60,
    )
    bot = Bot("123456:TEST_TOKEN", session=session)

    result = await session.make_request(bot, method)

    assert result == "proxy-result"
    assert direct.calls == ["getMe"]
    assert proxy.calls == ["getMe"]
    assert session.status.active_route == "proxy-1"
    await bot.session.close()


@pytest.mark.asyncio
async def test_non_idempotent_request_is_not_repeated() -> None:
    send = SendMessage(chat_id=42, text="hello")
    direct = FakeSession(network_error(send))
    proxy = FakeSession("poll-result")
    session = FailoverTelegramSession(
        [TelegramRoute("direct", direct), TelegramRoute("proxy-1", proxy)],
        cooldown_seconds=60,
    )
    bot = Bot("123456:TEST_TOKEN", session=session)

    with pytest.raises(TelegramNetworkError):
        await session.make_request(bot, send)
    result = await session.make_request(bot, GetUpdates())

    assert result == "poll-result"
    assert proxy.calls == ["getUpdates"]
    await bot.session.close()


@pytest.mark.asyncio
async def test_preferred_direct_route_is_retried_after_cooldown() -> None:
    now = [100.0]
    first = GetUpdates()
    direct = FakeSession(network_error(first), "direct-recovered")
    proxy = FakeSession("proxy-result")
    session = FailoverTelegramSession(
        [TelegramRoute("direct", direct), TelegramRoute("proxy-1", proxy)],
        cooldown_seconds=10,
        clock=lambda: now[0],
    )
    bot = Bot("123456:TEST_TOKEN", session=session)

    assert await session.make_request(bot, first) == "proxy-result"
    now[0] = 111.0
    assert await session.make_request(bot, GetUpdates()) == "direct-recovered"
    assert session.status.active_route == "direct"
    await bot.session.close()


@pytest.mark.asyncio
async def test_close_releases_every_route() -> None:
    direct = FakeSession("unused")
    proxy = FakeSession("unused")
    session = FailoverTelegramSession(
        [TelegramRoute("direct", direct), TelegramRoute("proxy-1", proxy)]
    )

    await session.close()

    assert direct.closed is True
    assert proxy.closed is True


@pytest.mark.asyncio
async def test_bootstrap_retries_transient_network_failure() -> None:
    class BootstrapBot:
        def __init__(self) -> None:
            self.attempts = 0
            self.delete_calls = 0

        async def set_my_commands(self, commands: list[BotCommand]) -> None:
            self.attempts += 1
            if self.attempts == 1:
                raise network_error(GetMe())

        async def delete_webhook(self, *, drop_pending_updates: bool) -> None:
            self.delete_calls += 1

    bot = BootstrapBot()
    delays: list[float] = []

    await wait_for_telegram(
        bot,  # type: ignore[arg-type]
        [BotCommand(command="start", description="start")],
        backoff_config=BackoffConfig(
            min_delay=1,
            max_delay=10,
            factor=2,
            jitter=0,
        ),
        sleep=lambda delay: _record_delay(delays, delay),
    )

    assert bot.attempts == 2
    assert bot.delete_calls == 1
    assert delays == [1]


async def _record_delay(delays: list[float], delay: float) -> None:
    delays.append(delay)
