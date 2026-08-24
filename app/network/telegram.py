from __future__ import annotations

import asyncio
import logging
import ssl
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

import certifi
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.session.base import BaseSession
from aiogram.exceptions import (
    ClientDecodeError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.methods import TelegramMethod
from aiogram.types import BotCommand
from aiogram.utils.backoff import Backoff, BackoffConfig
from aiohttp import ClientError

from app.config import Settings

logger = logging.getLogger(__name__)
TelegramResult = TypeVar("TelegramResult")

SAFE_RETRY_METHODS = frozenset(
    {
        "deleteMyCommands",
        "deleteWebhook",
        "getMe",
        "getMyCommands",
        "getUpdates",
        "getWebhookInfo",
        "setMyCommands",
    }
)
ROUTE_ERRORS = (TelegramNetworkError, ClientDecodeError)
STREAM_ERRORS = (TelegramNetworkError, ClientDecodeError, ClientError, TimeoutError)
BOOTSTRAP_ERRORS = (TelegramNetworkError, ClientDecodeError, TelegramServerError)


class TelegramBootstrapClient(Protocol):
    async def set_my_commands(self, commands: Sequence[BotCommand]) -> Any: ...

    async def delete_webhook(self, *, drop_pending_updates: bool) -> Any: ...


@dataclass(slots=True)
class TelegramRoute:
    name: str
    session: BaseSession
    unavailable_until: float = 0.0


@dataclass(frozen=True, slots=True)
class TelegramTransportStatus:
    active_route: str
    configured_routes: int
    routes_in_cooldown: int


class FailoverTelegramSession(BaseSession):
    """Aiogram session that isolates and fails over Telegram network routes."""

    def __init__(
        self,
        routes: Sequence[TelegramRoute],
        *,
        timeout: float = 30.0,
        cooldown_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        status_callback: Callable[[TelegramTransportStatus], None] | None = None,
    ) -> None:
        if not routes:
            raise ValueError("at least one Telegram route is required")
        super().__init__(timeout=timeout)
        self._routes = list(routes)
        self._cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._status_callback = status_callback
        self._active_index = 0
        self._state_lock = asyncio.Lock()

    @property
    def status(self) -> TelegramTransportStatus:
        now = self._clock()
        return TelegramTransportStatus(
            active_route=self._routes[self._active_index].name,
            configured_routes=len(self._routes),
            routes_in_cooldown=sum(
                route.unavailable_until > now for route in self._routes
            ),
        )

    def _route_order(self) -> list[int]:
        now = self._clock()
        healthy = [
            index
            for index, route in enumerate(self._routes)
            if route.unavailable_until <= now
        ]
        if healthy:
            return healthy
        return sorted(
            range(len(self._routes)),
            key=lambda index: self._routes[index].unavailable_until,
        )

    async def _mark_success(self, index: int) -> None:
        async with self._state_lock:
            previous = self._active_index
            self._active_index = index
            self._routes[index].unavailable_until = 0.0
        if previous != index:
            logger.info(
                "Telegram route switched",
                extra={
                    "event": "telegram_route_switched",
                    "route": self._routes[index].name,
                },
            )
        self._emit_status()

    async def _mark_failure(self, index: int, exc: BaseException) -> None:
        async with self._state_lock:
            self._routes[index].unavailable_until = (
                self._clock() + self._cooldown_seconds
            )
            next_routes = self._route_order()
            if next_routes:
                self._active_index = next_routes[0]
        logger.warning(
            "Telegram route failed",
            extra={
                "event": "telegram_route_failed",
                "route": self._routes[index].name,
                "error_type": type(exc).__name__,
            },
        )
        self._emit_status()

    def _emit_status(self) -> None:
        if self._status_callback is not None:
            try:
                self._status_callback(self.status)
            except Exception:
                logger.exception("Telegram status callback failed")

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[TelegramResult],
        timeout: int | None = None,
    ) -> TelegramResult:
        method_name = method.__api_method__
        route_order = self._route_order()
        last_error: BaseException | None = None
        for position, index in enumerate(route_order):
            route = self._routes[index]
            try:
                result = await route.session.make_request(bot, method, timeout)
            except ROUTE_ERRORS as exc:
                last_error = exc
                await self._mark_failure(index, exc)
                may_retry = method_name in SAFE_RETRY_METHODS
                if not may_retry or position == len(route_order) - 1:
                    raise
                continue
            await self._mark_success(index)
            return result
        if last_error is not None:
            raise last_error
        raise RuntimeError("Telegram route selection failed")

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        route_order = self._route_order()
        for position, index in enumerate(route_order):
            route = self._routes[index]
            emitted = False
            try:
                async for chunk in route.session.stream_content(
                    url,
                    headers=headers,
                    timeout=timeout,
                    chunk_size=chunk_size,
                    raise_for_status=raise_for_status,
                ):
                    emitted = True
                    yield chunk
            except STREAM_ERRORS as exc:
                await self._mark_failure(index, exc)
                if emitted or position == len(route_order) - 1:
                    raise
                continue
            await self._mark_success(index)
            return

    async def close(self) -> None:
        results = await asyncio.gather(
            *(route.session.close() for route in self._routes),
            return_exceptions=True,
        )
        for route, result in zip(self._routes, results, strict=True):
            if isinstance(result, BaseException):
                logger.warning(
                    "Failed to close Telegram route",
                    extra={
                        "event": "telegram_route_close_failed",
                        "route": route.name,
                        "error_type": type(result).__name__,
                    },
                )


def build_telegram_session(
    settings: Settings,
    *,
    status_callback: Callable[[TelegramTransportStatus], None] | None = None,
) -> FailoverTelegramSession:
    routes: list[TelegramRoute] = []
    timeout = settings.telegram_request_timeout_seconds
    if settings.telegram_direct_enabled:
        routes.append(TelegramRoute("direct", _build_route_session(timeout=timeout)))
    for index, proxy_url in enumerate(settings.telegram_proxy_values, start=1):
        routes.append(
            TelegramRoute(
                f"proxy-{index}",
                _build_route_session(proxy_url=proxy_url, timeout=timeout),
            )
        )
    return FailoverTelegramSession(
        routes,
        timeout=timeout,
        cooldown_seconds=settings.telegram_route_cooldown_seconds,
        status_callback=status_callback,
    )


def _build_route_session(
    *,
    timeout: float,
    proxy_url: str | None = None,
) -> AiohttpSession:
    session = AiohttpSession(proxy=proxy_url, timeout=timeout)
    # AiohttpSession replaces its connector settings when a proxy is enabled.
    # Restore the verified CA context so aiohttp-socks never falls back to an
    # incomplete interpreter-level certificate store.
    session._connector_init["ssl"] = ssl.create_default_context(  # noqa: SLF001
        cafile=certifi.where()
    )
    return session


def telegram_backoff_config(settings: Settings) -> BackoffConfig:
    return BackoffConfig(
        min_delay=settings.telegram_polling_backoff_min_seconds,
        max_delay=settings.telegram_polling_backoff_max_seconds,
        factor=settings.telegram_polling_backoff_factor,
        jitter=settings.telegram_polling_backoff_jitter,
    )


async def wait_for_telegram(
    bot: TelegramBootstrapClient,
    commands: Sequence[BotCommand],
    *,
    backoff_config: BackoffConfig,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Wait for Telegram startup without terminating on transient failures."""

    backoff = Backoff(backoff_config)
    while True:
        try:
            await bot.set_my_commands(commands)
            await bot.delete_webhook(drop_pending_updates=False)
        except TelegramRetryAfter as exc:
            delay = min(float(exc.retry_after), backoff_config.max_delay)
            error_type = type(exc).__name__
        except BOOTSTRAP_ERRORS as exc:
            delay = next(backoff)
            error_type = type(exc).__name__
        else:
            logger.info(
                "Telegram bootstrap completed",
                extra={"event": "telegram_bootstrap_ready"},
            )
            return
        logger.warning(
            "Telegram bootstrap is unavailable; retry scheduled",
            extra={
                "event": "telegram_bootstrap_retry",
                "error_type": error_type,
                "retry_delay_seconds": round(delay, 3),
            },
        )
        await sleep(delay)
