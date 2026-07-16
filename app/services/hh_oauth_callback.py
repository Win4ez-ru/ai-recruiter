from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiohttp import web

from app.config import Settings
from app.services.hh_oauth import HHOAuthService
from app.sources.hh import HHAPIError, HHAuthorizationError

logger = logging.getLogger(__name__)


class HHOAuthCallbackServer:
    def __init__(
        self, *, settings: Settings, oauth_service: HHOAuthService, bot: Bot
    ) -> None:
        self.settings = settings
        self.oauth_service = oauth_service
        self.bot = bot
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        if not self.settings.hh_oauth_configured:
            logger.info("HH OAuth callback server is disabled: credentials are absent")
            return
        app = web.Application()
        app.router.add_get(self.settings.hh_callback_path, self._callback)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(
            self._runner,
            host=self.settings.hh_callback_host,
            port=self.settings.hh_callback_port,
        )
        await site.start()
        logger.info(
            "HH OAuth callback server started on %s:%s",
            self.settings.hh_callback_host,
            self.settings.hh_callback_port,
        )

    async def close(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def _callback(self, request: web.Request) -> web.Response:
        state = request.query.get("state")
        code = request.query.get("code")
        error = request.query.get("error")
        if error:
            return web.Response(
                text="Доступ HeadHunter не предоставлен. Можно закрыть эту страницу.",
                content_type="text/plain",
                charset="utf-8",
                status=400,
            )
        if not state or not code:
            return web.Response(
                text="Некорректный OAuth callback.",
                content_type="text/plain",
                charset="utf-8",
                status=400,
            )
        try:
            await self.oauth_service.complete_authorization(
                telegram_user_id=self.settings.telegram_user_id,
                state=state,
                code=code,
            )
        except HHAuthorizationError:
            logger.warning("HH OAuth callback was rejected")
            return web.Response(
                text="Ссылка авторизации недействительна или устарела.",
                content_type="text/plain",
                charset="utf-8",
                status=400,
            )
        except HHAPIError:
            logger.warning("HH OAuth callback failed because HH is unavailable")
            return web.Response(
                text="HeadHunter временно недоступен. Попробуйте подключение позже.",
                content_type="text/plain",
                charset="utf-8",
                status=503,
            )
        try:
            await self.bot.send_message(
                self.settings.telegram_user_id,
                "HeadHunter успешно подключен. Теперь можно подготовить отклик.",
            )
        except TelegramAPIError:
            logger.warning("HH OAuth succeeded, but Telegram notification failed")
        return web.Response(
            text="HeadHunter успешно подключен. Можно вернуться в Telegram.",
            content_type="text/plain",
            charset="utf-8",
        )
