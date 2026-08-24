from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import Settings
from app.models import UserIntegration, utc_now
from app.repositories.hh_integration_repository import HHIntegrationRepository
from app.sources.hh import HHAPIError, HHAuthorizationError, HHClient, HHRemoteError

logger = logging.getLogger(__name__)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class HHOAuthService:
    def __init__(
        self,
        client: HHClient,
        repository: HHIntegrationRepository,
        settings: Settings,
    ) -> None:
        self.client = client
        self.repository = repository
        self.settings = settings
        self._refresh_lock = asyncio.Lock()

    async def create_authorization_url(self, telegram_user_id: int) -> str:
        if not self.settings.hh_oauth_configured:
            raise HHAuthorizationError("HH OAuth is not configured")
        state = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(64)
        await self.repository.create_oauth_state(
            telegram_user_id=telegram_user_id,
            raw_state=state,
            code_verifier=code_verifier,
            expires_at=utc_now()
            + timedelta(seconds=self.settings.hh_oauth_state_ttl_seconds),
        )
        return self.client.authorization_url(state=state, code_verifier=code_verifier)

    async def complete_authorization(
        self, *, telegram_user_id: int, state: str, code: str
    ) -> UserIntegration:
        oauth_state = await self.repository.consume_oauth_state(
            telegram_user_id=telegram_user_id, raw_state=state
        )
        if oauth_state is None:
            raise HHAuthorizationError(
                "OAuth state is invalid, expired, or already used"
            )
        payload = await self.client.exchange_code(
            authorization_code=code,
            code_verifier=oauth_state.code_verifier,
        )
        access_token = str(payload["access_token"])
        me = await self.client.get_current_user(access_token)
        integration = await self._save_token_payload(
            telegram_user_id=telegram_user_id,
            payload=payload,
            external_user_id=str(me.get("id")) if me.get("id") is not None else None,
        )
        try:
            resumes = await self.client.get_my_resumes(access_token)
            await self.repository.save_resumes(telegram_user_id, resumes)
        except HHRemoteError as exc:
            logger.warning(
                "HH OAuth completed, but initial resume sync was rejected",
                extra={
                    "event": "hh_resume_sync_rejected",
                    "status_code": exc.status_code,
                    "error_type": exc.error_type,
                    "error_value": exc.error_value,
                    "request_id": exc.request_id,
                },
            )
        except HHAPIError as exc:
            logger.warning(
                "HH OAuth completed, but initial resume sync failed",
                extra={
                    "event": "hh_resume_sync_failed",
                    "error_type": type(exc).__name__,
                },
            )
        return integration

    async def _save_token_payload(
        self,
        *,
        telegram_user_id: int,
        payload: dict[str, Any],
        external_user_id: str | None,
    ) -> UserIntegration:
        expires_in = payload.get("expires_in")
        expires_at = (
            utc_now() + timedelta(seconds=max(0, int(expires_in)))
            if expires_in is not None
            else None
        )
        scope_value = payload.get("scope")
        scope = (
            " ".join(str(item) for item in scope_value)
            if isinstance(scope_value, list)
            else str(scope_value)
            if scope_value
            else None
        )
        return await self.repository.save_integration(
            telegram_user_id=telegram_user_id,
            access_token=str(payload["access_token"]),
            refresh_token=(
                str(payload["refresh_token"]) if payload.get("refresh_token") else None
            ),
            expires_at=expires_at,
            scope=scope,
            external_user_id=external_user_id,
        )

    async def get_access_token(self, telegram_user_id: int) -> str:
        integration = await self.repository.get_integration(telegram_user_id)
        if integration is None:
            raise HHAuthorizationError("HH account is not connected")
        if integration.expires_at is None or _aware(integration.expires_at) > utc_now():
            return integration.access_token
        async with self._refresh_lock:
            integration = await self.repository.get_integration(telegram_user_id)
            if integration is None:
                raise HHAuthorizationError("HH account is not connected")
            if (
                integration.expires_at is None
                or _aware(integration.expires_at) > utc_now()
            ):
                return integration.access_token
            if not integration.refresh_token:
                raise HHAuthorizationError("HH authorization expired")
            payload = await self.client.refresh_access_token(integration.refresh_token)
            refreshed = await self._save_token_payload(
                telegram_user_id=telegram_user_id,
                payload=payload,
                external_user_id=integration.external_user_id,
            )
            return refreshed.access_token
