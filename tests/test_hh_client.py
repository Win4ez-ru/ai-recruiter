from __future__ import annotations

import httpx
import pytest

from app.network.retry import RetryPolicy
from app.sources.hh import (
    HHAuthorizationError,
    HHClient,
    HHRemoteError,
    HHTransportError,
    HHTokenExpiredError,
    html_to_text,
    vacancy_from_hh,
)


@pytest.mark.asyncio
async def test_hh_client_search_deduplicates_scopes_and_gets_details() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/vacancies":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "42",
                            "name": "Junior iOS Developer",
                            "published_at": "2026-01-02T10:00:00+0300",
                        }
                    ],
                    "pages": 1,
                },
            )
        if request.url.path == "/vacancies/42":
            return httpx.Response(
                200,
                json={
                    "id": "42",
                    "name": "Junior iOS Developer",
                    "alternate_url": "https://hh.ru/vacancy/42",
                    "description": "<p>Swift &amp; <b>SwiftUI</b></p>",
                    "employer": {"name": "Acme"},
                },
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://api.hh.ru"
    ) as http_client:
        client = HHClient("TestAgent/1.0", http_client=http_client, retries=0)
        items = await client.search_vacancies("iOS", max_results=100)
        details = await client.get_vacancy("42")

    assert len(items) == 1
    assert details["id"] == "42"
    assert any(request.headers["HH-User-Agent"] == "TestAgent/1.0" for request in requests)
    search_request = next(request for request in requests if request.url.path == "/vacancies")
    assert search_request.url.params["period"] == "7"
    assert search_request.url.params["order_by"] == "publication_time"


def test_hh_html_and_mapping_handle_missing_optional_fields() -> None:
    assert html_to_text("<p>Hello<br>Swift &amp; iOS</p>") == "Hello\nSwift & iOS"
    mapped = vacancy_from_hh(
        {
            "id": "5",
            "name": "iOS Developer",
            "alternate_url": "https://hh.ru/vacancy/5",
            "description": "",
        }
    )
    assert mapped.company == "Не указана"
    assert mapped.salary_from is None


@pytest.mark.asyncio
async def test_hh_client_gets_current_users_resumes() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/me":
            return httpx.Response(
                200,
                json={"id": "7", "resumes_url": "https://api.hh.ru/resumes/mine"},
            )
        if request.url.path == "/resumes/mine":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "resume-1",
                            "title": "iOS Developer",
                            "status": {"id": "published"},
                            "alternate_url": "https://hh.ru/resume/resume-1",
                            "updated_at": "2026-07-15T12:00:00+0300",
                        }
                    ]
                },
            )
        return httpx.Response(404)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.hh.ru"
    ) as http_client:
        client = HHClient("TestAgent/1.0", http_client=http_client, retries=0)
        resumes = await client.get_my_resumes("secret-token")

    assert [item.external_id for item in resumes] == ["resume-1"]
    assert resumes[0].status == "published"
    assert all(request.headers["Authorization"] == "Bearer secret-token" for request in requests)


@pytest.mark.asyncio
async def test_hh_client_exchanges_oauth_code_with_pkce() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_in": 3600,
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.hh.ru"
    ) as http_client:
        client = HHClient(
            "TestAgent/1.0",
            http_client=http_client,
            client_id="client",
            client_secret="secret",
            redirect_uri="https://example.test/oauth/hh/callback",
        )
        payload = await client.exchange_code(
            authorization_code="code", code_verifier="verifier"
        )

    assert payload["access_token"] == "access"
    body = captured[0].content.decode()
    assert "grant_type=authorization_code" in body
    assert "code_verifier=verifier" in body


@pytest.mark.asyncio
async def test_hh_client_refresh_uses_current_official_parameter_set() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "access_token": "new-access",
                "refresh_token": "new-refresh",
                "expires_in": 3600,
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.hh.ru"
    ) as http_client:
        client = HHClient(
            "TestAgent/1.0",
            http_client=http_client,
            client_id="must-not-be-sent",
            client_secret="must-not-be-sent",
        )
        await client.refresh_access_token("refresh")

    body = captured[0].content.decode()
    assert "grant_type=refresh_token" in body
    assert "refresh_token=refresh" in body
    assert "client_id" not in body
    assert "client_secret" not in body


@pytest.mark.asyncio
async def test_hh_client_normalizes_expired_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, json={"errors": [{"type": "oauth", "value": "token_expired"}]}
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.hh.ru"
    ) as http_client:
        client = HHClient("TestAgent/1.0", http_client=http_client)
        with pytest.raises(HHTokenExpiredError):
            await client.get_current_user("expired")


@pytest.mark.asyncio
async def test_hh_client_rejects_non_applicant_account() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "manager"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.hh.ru"
    ) as http_client:
        client = HHClient("TestAgent/1.0", http_client=http_client)
        with pytest.raises(HHAuthorizationError):
            await client.get_my_resumes("token")


@pytest.mark.asyncio
async def test_hh_client_retries_idempotent_5xx_with_backoff() -> None:
    attempts = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(503, json={"errors": [{"type": "unavailable"}]})
        return httpx.Response(200, json={"id": "42"})

    async def sleep(delay: float) -> None:
        delays.append(delay)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.hh.ru"
    ) as http_client:
        client = HHClient(
            "TestAgent/1.0",
            http_client=http_client,
            retry_policy=RetryPolicy(
                max_attempts=3,
                base_delay_seconds=1,
                max_delay_seconds=10,
                jitter_ratio=0,
            ),
            sleep=sleep,
        )
        result = await client.get_vacancy("42")

    assert result["id"] == "42"
    assert attempts == 3
    assert delays == [1, 2]


@pytest.mark.asyncio
async def test_hh_client_respects_retry_after_and_preserves_request_id() -> None:
    attempts = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            429,
            headers={"Retry-After": "7", "X-Request-ID": "request-123"},
            json={"errors": [{"type": "rate_limit"}]},
        )

    async def sleep(delay: float) -> None:
        delays.append(delay)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.hh.ru"
    ) as http_client:
        client = HHClient(
            "TestAgent/1.0",
            http_client=http_client,
            retry_policy=RetryPolicy(
                max_attempts=2,
                base_delay_seconds=1,
                max_delay_seconds=10,
                jitter_ratio=0,
            ),
            sleep=sleep,
        )
        with pytest.raises(HHRemoteError) as error:
            await client.get_vacancy("42")

    assert attempts == 2
    assert delays == [7]
    assert error.value.status_code == 429
    assert error.value.request_id == "request-123"
    assert error.value.retry_after == "7"


@pytest.mark.asyncio
async def test_hh_client_retries_read_timeout_only_for_idempotent_requests() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("timed out", request=request)

    async def no_sleep(delay: float) -> None:
        return None

    policy = RetryPolicy(
        max_attempts=2,
        base_delay_seconds=1,
        max_delay_seconds=2,
        jitter_ratio=0,
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.hh.ru"
    ) as http_client:
        client = HHClient(
            "TestAgent/1.0",
            http_client=http_client,
            retry_policy=policy,
            client_id="client",
            client_secret="secret",
            sleep=no_sleep,
        )
        with pytest.raises(HHTransportError):
            await client.get_vacancy("42")
        assert attempts == 2

        attempts = 0
        with pytest.raises(HHAuthorizationError):
            await client.exchange_code(
                authorization_code="code",
                code_verifier="verifier",
            )
        assert attempts == 1
