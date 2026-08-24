from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiohttp import web

from app.bot.keyboards import hh_connected_keyboard
from app.bot.ui import UIManager
from app.config import Settings
from app.health import HealthRegistry
from app.services.hh_oauth import HHOAuthService
from app.sources.hh import HHAPIError, HHAuthorizationError

logger = logging.getLogger(__name__)


class ApplicationHTTPServer:
    def __init__(
        self,
        *,
        settings: Settings,
        oauth_service: HHOAuthService,
        bot: Bot,
        health: HealthRegistry,
        ui: UIManager | None = None,
    ) -> None:
        self.settings = settings
        self.oauth_service = oauth_service
        self.bot = bot
        self.health = health
        self.ui = ui
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        if (
            not self.settings.hh_oauth_configured
            and not self.settings.healthcheck_enabled
        ):
            logger.info("Application HTTP server is disabled")
            return
        app = web.Application()
        if self.settings.healthcheck_enabled:
            app.router.add_get(self.settings.health_live_path, self._live)
            app.router.add_get(self.settings.health_ready_path, self._ready)
        if self.settings.hh_oauth_configured:
            app.router.add_get(self.settings.hh_callback_path, self._callback)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(
            self._runner,
            host=self.settings.http_bind_host,
            port=self.settings.http_bind_port,
        )
        await site.start()
        logger.info(
            "Application HTTP server started on %s:%s",
            self.settings.http_bind_host,
            self.settings.http_bind_port,
        )

    async def close(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def _live(self, _request: web.Request) -> web.Response:
        return web.json_response(self.health.snapshot(), status=200)

    async def _ready(self, _request: web.Request) -> web.Response:
        return web.json_response(
            self.health.snapshot(),
            status=200 if self.health.ready else 503,
        )

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
            text = (
                "<b>✅ HeadHunter подключён</b>\n\n"
                "Теперь можно готовить и отправлять отклики из карточек вакансий."
            )
            if self.ui is not None:
                session = await self.ui.restore(self.settings.telegram_user_id)
                await self.ui.render_chat(
                    self.bot,
                    self.settings.telegram_user_id,
                    text,
                    hh_connected_keyboard(session.pending_vacancy_id),
                    screen="hh_connected",
                )
            else:
                await self.bot.send_message(self.settings.telegram_user_id, text)
        except TelegramAPIError:
            logger.warning("HH OAuth succeeded, but Telegram notification failed")
        return web.Response(
            text="HeadHunter успешно подключен. Можно вернуться в Telegram.",
            content_type="text/plain",
            charset="utf-8",
        )


HHOAuthCallbackServer = ApplicationHTTPServer
