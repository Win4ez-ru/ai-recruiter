from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.config import Settings
from app.database import Database
from app.models import OAuthState, utc_now
from app.repositories.hh_integration_repository import HHIntegrationRepository
from app.services.hh_oauth import HHOAuthService
from app.schemas import HHResumeData
from app.sources.hh import HHAuthorizationError


@pytest_asyncio.fixture
async def database() -> Database:
    db = Database("sqlite+aiosqlite:///:memory:")
    await db.create_tables()
    yield db
    await db.close()


def settings() -> Settings:
    return Settings(
        telegram_bot_token="123456:TEST_TOKEN",
        telegram_user_id=42,
        openai_api_key="test-key",
        openai_model="test-model",
        hh_client_id="client-id",
        hh_client_secret="client-secret",
        hh_redirect_uri="http://127.0.0.1:8080/oauth/hh/callback",
    )


class FakeOAuthClient:
    def __init__(self) -> None:
        self.state = ""
        self.verifier = ""
        self.refresh_calls = 0

    def authorization_url(self, *, state: str, code_verifier: str) -> str:
        self.state = state
        self.verifier = code_verifier
        return f"https://hh.ru/oauth/authorize?state={state}"

    async def exchange_code(self, **kwargs: str) -> dict:
        assert kwargs["code_verifier"] == self.verifier
        return {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "expires_in": 3600,
        }

    async def get_current_user(self, access_token: str) -> dict:
        assert access_token == "access-token"
        return {"id": "hh-user-7"}

    async def get_my_resumes(self, access_token: str) -> list[HHResumeData]:
        assert access_token == "access-token"
        return [HHResumeData(external_id="resume-1", title="iOS Developer")]

    async def refresh_access_token(self, refresh_token: str) -> dict:
        self.refresh_calls += 1
        assert refresh_token == "old-refresh"
        return {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 3600,
        }


@pytest.mark.asyncio
async def test_oauth_state_is_hashed_bound_and_one_time(database: Database) -> None:
    repository = HHIntegrationRepository(database)
    client = FakeOAuthClient()
    service = HHOAuthService(client, repository, settings())  # type: ignore[arg-type]

    url = await service.create_authorization_url(42)
    assert client.state in url
    async with database.session_factory() as session:
        stored = await session.scalar(select(OAuthState))
    assert stored is not None
    assert stored.state_hash != client.state
    assert stored.telegram_user_id == 42

    integration = await service.complete_authorization(
        telegram_user_id=42, state=client.state, code="authorization-code"
    )
    assert integration.external_user_id == "hh-user-7"
    assert integration.access_token == "access-token"
    resumes = await repository.list_resumes(42)
    assert [item.external_id for item in resumes] == ["resume-1"]

    with pytest.raises(HHAuthorizationError):
        await service.complete_authorization(
            telegram_user_id=42, state=client.state, code="authorization-code"
        )


@pytest.mark.asyncio
async def test_oauth_state_rejects_other_user(database: Database) -> None:
    repository = HHIntegrationRepository(database)
    client = FakeOAuthClient()
    service = HHOAuthService(client, repository, settings())  # type: ignore[arg-type]
    await service.create_authorization_url(42)

    with pytest.raises(HHAuthorizationError):
        await service.complete_authorization(
            telegram_user_id=99, state=client.state, code="authorization-code"
        )


@pytest.mark.asyncio
async def test_expired_access_token_is_refreshed_once(database: Database) -> None:
    repository = HHIntegrationRepository(database)
    client = FakeOAuthClient()
    service = HHOAuthService(client, repository, settings())  # type: ignore[arg-type]
    await repository.save_integration(
        telegram_user_id=42,
        access_token="old-access",
        refresh_token="old-refresh",
        expires_at=utc_now() - timedelta(seconds=1),
        scope=None,
        external_user_id="hh-user-7",
    )

    token = await service.get_access_token(42)

    assert token == "new-access"
    assert client.refresh_calls == 1
    stored = await repository.get_integration(42)
    assert stored is not None and stored.refresh_token == "new-refresh"


@pytest.mark.asyncio
async def test_concurrent_token_reads_use_refresh_token_once(database: Database) -> None:
    repository = HHIntegrationRepository(database)
    client = FakeOAuthClient()
    service = HHOAuthService(client, repository, settings())  # type: ignore[arg-type]
    await repository.save_integration(
        telegram_user_id=42,
        access_token="old-access",
        refresh_token="old-refresh",
        expires_at=utc_now() - timedelta(seconds=1),
        scope=None,
        external_user_id="hh-user-7",
    )

    tokens = await asyncio.gather(
        service.get_access_token(42), service.get_access_token(42)
    )

    assert tokens == ["new-access", "new-access"]
    assert client.refresh_calls == 1
