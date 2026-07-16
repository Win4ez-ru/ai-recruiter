from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlalchemy import select, update

from app.database import Database
from app.models import HHResume, OAuthState, UserIntegration, utc_now
from app.schemas import HHResumeData


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class HHIntegrationRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def create_oauth_state(
        self,
        *,
        telegram_user_id: int,
        raw_state: str,
        code_verifier: str,
        expires_at: datetime,
    ) -> None:
        async with self.database.session_factory() as session:
            session.add(
                OAuthState(
                    telegram_user_id=telegram_user_id,
                    provider="hh",
                    state_hash=token_hash(raw_state),
                    code_verifier=code_verifier,
                    expires_at=expires_at,
                )
            )
            await session.commit()

    async def consume_oauth_state(
        self, *, telegram_user_id: int, raw_state: str
    ) -> OAuthState | None:
        now = utc_now()
        async with self.database.session_factory() as session:
            state = await session.scalar(
                select(OAuthState).where(
                    OAuthState.state_hash == token_hash(raw_state),
                    OAuthState.provider == "hh",
                )
            )
            if (
                state is None
                or state.telegram_user_id != telegram_user_id
                or state.used_at is not None
                or _aware(state.expires_at) <= now
            ):
                return None
            result = await session.execute(
                update(OAuthState)
                .where(OAuthState.id == state.id, OAuthState.used_at.is_(None))
                .values(used_at=now)
            )
            if result.rowcount != 1:
                await session.rollback()
                return None
            await session.commit()
            return state

    async def get_integration(self, telegram_user_id: int) -> UserIntegration | None:
        async with self.database.session_factory() as session:
            return await session.scalar(
                select(UserIntegration).where(
                    UserIntegration.telegram_user_id == telegram_user_id,
                    UserIntegration.provider == "hh",
                )
            )

    async def save_integration(
        self,
        *,
        telegram_user_id: int,
        access_token: str,
        refresh_token: str | None,
        expires_at: datetime | None,
        scope: str | None,
        external_user_id: str | None,
    ) -> UserIntegration:
        async with self.database.session_factory() as session:
            integration = await session.scalar(
                select(UserIntegration).where(
                    UserIntegration.telegram_user_id == telegram_user_id,
                    UserIntegration.provider == "hh",
                )
            )
            if integration is None:
                integration = UserIntegration(
                    telegram_user_id=telegram_user_id,
                    provider="hh",
                    access_token=access_token,
                )
                session.add(integration)
            integration.access_token = access_token
            integration.refresh_token = refresh_token
            integration.expires_at = expires_at
            integration.scope = scope
            integration.external_user_id = external_user_id
            await session.commit()
            await session.refresh(integration)
            return integration

    async def save_resumes(
        self, telegram_user_id: int, resumes: list[HHResumeData]
    ) -> list[HHResume]:
        async with self.database.session_factory() as session:
            existing = list(
                (
                    await session.scalars(
                        select(HHResume).where(
                            HHResume.telegram_user_id == telegram_user_id
                        )
                    )
                ).all()
            )
            by_external_id = {item.external_id: item for item in existing}
            incoming_ids = {item.external_id for item in resumes}
            default_id = next(
                (
                    item.external_id
                    for item in existing
                    if item.is_default and item.external_id in incoming_ids
                ),
                None,
            )
            if default_id is None and len(resumes) == 1:
                default_id = resumes[0].external_id
            now = utc_now()
            for data in resumes:
                row = by_external_id.get(data.external_id)
                if row is None:
                    row = HHResume(
                        telegram_user_id=telegram_user_id,
                        external_id=data.external_id,
                        title=data.title,
                    )
                    session.add(row)
                row.title = data.title
                row.status = data.status
                row.url = data.url
                row.external_updated_at = data.updated_at
                row.synced_at = now
                row.is_default = data.external_id == default_id
            for row in existing:
                if row.external_id not in incoming_ids:
                    row.is_default = False
            await session.commit()
            return list(
                (
                    await session.scalars(
                        select(HHResume)
                        .where(
                            HHResume.telegram_user_id == telegram_user_id,
                            HHResume.external_id.in_(incoming_ids),
                        )
                        .order_by(HHResume.external_updated_at.desc())
                    )
                ).all()
            )

    async def list_resumes(self, telegram_user_id: int) -> list[HHResume]:
        async with self.database.session_factory() as session:
            return list(
                (
                    await session.scalars(
                        select(HHResume)
                        .where(HHResume.telegram_user_id == telegram_user_id)
                        .order_by(HHResume.is_default.desc(), HHResume.updated_at.desc())
                    )
                ).all()
            )

    async def set_default_resume(
        self, *, telegram_user_id: int, external_id: str
    ) -> HHResume | None:
        async with self.database.session_factory() as session:
            resume = await session.scalar(
                select(HHResume).where(
                    HHResume.telegram_user_id == telegram_user_id,
                    HHResume.external_id == external_id,
                )
            )
            if resume is None:
                return None
            await session.execute(
                update(HHResume)
                .where(HHResume.telegram_user_id == telegram_user_id)
                .values(is_default=False)
            )
            resume.is_default = True
            await session.commit()
            await session.refresh(resume)
            return resume
