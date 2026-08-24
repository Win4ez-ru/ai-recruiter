from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlencode

import httpx

from app.network.retry import RetryPolicy
from app.schemas import HHResumeData, VacancyCreate
from app.sources.base import VacancySource

logger = logging.getLogger(__name__)
HH_BASE_URL = "https://api.hh.ru"
SPB_AREA_ID = "2"


class HHAPIError(RuntimeError):
    """Base exception for safe HeadHunter client failures."""


class HHAuthorizationError(HHAPIError):
    """HeadHunter user authorization is absent or invalid."""


class HHApplicationAuthorizationError(HHAuthorizationError):
    """HeadHunter application credentials are absent or invalid."""


class HHTokenExpiredError(HHAuthorizationError):
    """HeadHunter access token has expired."""


class HHResumeNotFoundError(HHAPIError):
    """Requested HeadHunter resume is unavailable to the current user."""


class HHTransportError(HHAPIError):
    """HeadHunter could not be reached within the configured retry budget."""


class HHRemoteError(HHAPIError):
    def __init__(
        self,
        status_code: int,
        error_type: str,
        error_value: str | None = None,
        *,
        fallback_url: str | None = None,
        request_id: str | None = None,
        retry_after: str | None = None,
    ) -> None:
        super().__init__(f"HH API error: {status_code} {error_type}")
        self.status_code = status_code
        self.error_type = error_type
        self.error_value = error_value
        self.fallback_url = fallback_url
        self.request_id = request_id
        self.retry_after = retry_after


class _TextExtractor(HTMLParser):
    BLOCK_TAGS = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_to_text(value: str | None) -> str:
    if not value:
        return ""
    parser = _TextExtractor()
    parser.feed(value)
    lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    return "\n".join(line for line in lines if line).strip()


class HHClient(VacancySource):
    """Asynchronous client for the official public HeadHunter API."""

    def __init__(
        self,
        user_agent: str,
        *,
        api_base_url: str = HH_BASE_URL,
        auth_base_url: str = "https://hh.ru",
        client_id: str = "",
        client_secret: str = "",
        redirect_uri: str = "",
        timeout: float | httpx.Timeout = 15.0,
        retries: int | None = None,
        retry_policy: RetryPolicy | None = None,
        proxy_url: str | None = None,
        trust_env: bool = False,
        search_area_id: str = SPB_AREA_ID,
        search_period_days: int = 7,
        search_remote: bool = True,
        http_client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if retries is not None and retry_policy is not None:
            raise ValueError("use either retries or retry_policy")
        self.retry_policy = retry_policy or RetryPolicy(
            max_attempts=(retries + 1) if retries is not None else 4
        )
        self._sleep = sleep
        self.api_base_url = api_base_url.rstrip("/")
        self.auth_base_url = auth_base_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.search_area_id = search_area_id.strip()
        self.search_period_days = search_period_days
        self.search_remote = search_remote
        self._application_access_token: str | None = None
        self._application_token_lock = asyncio.Lock()
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            base_url=self.api_base_url,
            timeout=timeout,
            proxy=proxy_url,
            trust_env=trust_env,
            headers={
                "User-Agent": user_agent,
                "HH-User-Agent": user_agent,
                "Accept": "application/json",
            },
        )
        if http_client is not None:
            self._client.headers.update(
                {
                    "User-Agent": user_agent,
                    "HH-User-Agent": user_agent,
                    "Accept": "application/json",
                }
            )

    async def __aenter__(self) -> HHClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        idempotent: bool,
    ) -> httpx.Response:
        for attempt in range(1, self.retry_policy.max_attempts + 1):
            try:
                response = await self._client.request(
                    method,
                    path,
                    params=params,
                    data=data,
                    headers=headers,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                can_retry = idempotent or isinstance(
                    exc,
                    (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout),
                )
                if not can_retry or attempt >= self.retry_policy.max_attempts:
                    raise HHTransportError(
                        f"HH {type(exc).__name__} for {path}"
                    ) from exc
                await self._wait_before_retry(
                    path=path,
                    attempt=attempt,
                    error_type=type(exc).__name__,
                )
                continue

            retryable_status = response.status_code == 429 or (
                idempotent and response.status_code >= 500
            )
            if retryable_status and attempt < self.retry_policy.max_attempts:
                await self._wait_before_retry(
                    path=path,
                    attempt=attempt,
                    status_code=response.status_code,
                    retry_after=response.headers.get("Retry-After"),
                    request_id=self._request_id(response),
                )
                continue
            return response
        raise RuntimeError("HH retry loop exited unexpectedly")

    async def _wait_before_retry(
        self,
        *,
        path: str,
        attempt: int,
        status_code: int | None = None,
        error_type: str | None = None,
        retry_after: str | None = None,
        request_id: str | None = None,
    ) -> None:
        delay = self.retry_policy.delay_seconds(
            attempt,
            retry_after=retry_after,
        )
        logger.warning(
            "HeadHunter request retry scheduled",
            extra={
                "event": "hh_request_retry",
                "path": path,
                "attempt": attempt,
                "max_attempts": self.retry_policy.max_attempts,
                "status_code": status_code,
                "error_type": error_type,
                "request_id": request_id,
                "retry_delay_seconds": round(delay, 3),
            },
        )
        await self._sleep(delay)

    async def _request(
        self, path: str, *, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        response = await self._send("GET", path, params=params, idempotent=True)
        if response.status_code >= 400:
            raise self._remote_error(response)
        return self._json_object(response, path)

    @property
    def _application_auth_configured(self) -> bool:
        return bool(self.client_id.strip() and self.client_secret.strip())

    async def _get_application_access_token(self) -> str:
        if not self._application_auth_configured:
            raise HHApplicationAuthorizationError(
                "HH application authorization is not configured"
            )
        if self._application_access_token:
            return self._application_access_token

        async with self._application_token_lock:
            if self._application_access_token:
                return self._application_access_token
            payload = await self._application_token_request()
            self._application_access_token = str(payload["access_token"])
            logger.info(
                "HeadHunter application authorization completed",
                extra={"event": "hh_application_authorized"},
            )
            return self._application_access_token

    async def _application_get(
        self, path: str, *, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if not self._application_auth_configured:
            return await self._request(path, params=params)

        access_token = await self._get_application_access_token()
        for authorization_attempt in range(2):
            response = await self._send(
                "GET",
                path,
                params=params,
                headers={"Authorization": f"Bearer {access_token}"},
                idempotent=True,
            )
            if response.status_code < 400:
                return self._json_object(response, path)

            error = self._remote_error(response)
            if error.error_type != "oauth" or authorization_attempt > 0:
                raise error

            if self._application_access_token == access_token:
                self._application_access_token = None
            logger.warning(
                "HeadHunter application authorization is no longer valid",
                extra={
                    "event": "hh_application_token_invalidated",
                    "error_value": error.error_value,
                    "request_id": error.request_id,
                },
            )
            access_token = await self._get_application_access_token()

        raise RuntimeError("HH application authorization loop exited unexpectedly")

    async def _application_token_request(self) -> dict[str, Any]:
        response = await self._send(
            "POST",
            "/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            idempotent=False,
        )
        if response.status_code >= 400:
            raise HHApplicationAuthorizationError(
                "HH rejected application authorization"
            ) from self._remote_error(response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise HHAPIError("HH returned an invalid application token") from exc
        if not isinstance(payload, dict) or not payload.get("access_token"):
            raise HHAPIError("HH returned an incomplete application token")
        return payload

    @staticmethod
    def code_challenge(code_verifier: str) -> str:
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    def authorization_url(self, *, state: str, code_verifier: str) -> str:
        if not self.client_id or not self.redirect_uri:
            raise HHAuthorizationError("HH OAuth is not configured")
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.client_id,
                "state": state,
                "redirect_uri": self.redirect_uri,
                "code_challenge": self.code_challenge(code_verifier),
                "code_challenge_method": "S256",
            }
        )
        return f"{self.auth_base_url}/oauth/authorize?{query}"

    async def _token_request(self, data: dict[str, str]) -> dict[str, Any]:
        try:
            response = await self._send("POST", "/token", data=data, idempotent=False)
        except HHTransportError as exc:
            raise HHAuthorizationError("HH token request failed") from exc
        if response.status_code >= 400:
            raise HHAuthorizationError("HH rejected OAuth token request")
        try:
            payload = response.json()
        except ValueError as exc:
            raise HHAuthorizationError("HH returned an invalid OAuth response") from exc
        if not isinstance(payload, dict) or not payload.get("access_token"):
            raise HHAuthorizationError("HH returned an incomplete OAuth response")
        return payload

    async def exchange_code(
        self, *, authorization_code: str, code_verifier: str
    ) -> dict[str, Any]:
        return await self._token_request(
            {
                "grant_type": "authorization_code",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": authorization_code,
                "redirect_uri": self.redirect_uri,
                "code_verifier": code_verifier,
            }
        )

    async def refresh_access_token(self, refresh_token: str) -> dict[str, Any]:
        return await self._token_request(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            }
        )

    @staticmethod
    def _remote_error(response: httpx.Response) -> HHRemoteError:
        error_type = "unknown"
        error_value: str | None = None
        fallback_url: str | None = None
        try:
            payload = response.json()
            errors = payload.get("errors") if isinstance(payload, dict) else None
            first = errors[0] if isinstance(errors, list) and errors else {}
            if isinstance(first, dict):
                error_type = str(first.get("type") or error_type)
                error_value = (
                    str(first["value"]) if first.get("value") is not None else None
                )
                fallback_url = first.get("fallback_url")
        except (ValueError, TypeError):
            logger.debug("HH error response did not contain a JSON error payload")
        return HHRemoteError(
            response.status_code,
            error_type,
            error_value,
            fallback_url=fallback_url,
            request_id=HHClient._request_id(response),
            retry_after=response.headers.get("Retry-After"),
        )

    @staticmethod
    def _request_id(response: httpx.Response) -> str | None:
        request_id = response.headers.get("X-Request-ID")
        if request_id:
            return request_id
        try:
            payload = response.json()
        except ValueError:
            return None
        if isinstance(payload, dict) and payload.get("request_id"):
            return str(payload["request_id"])
        return None

    @staticmethod
    def _json_object(response: httpx.Response, path: str) -> dict[str, Any]:
        if not response.content:
            raise HHAPIError(f"Empty HH response for {path}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise HHAPIError(f"Invalid HH response for {path}") from exc
        if not isinstance(payload, dict):
            raise HHAPIError(f"Unexpected HH response for {path}")
        return payload

    async def _authorized_get(self, path: str, access_token: str) -> dict[str, Any]:
        response = await self._send(
            "GET",
            path,
            headers={"Authorization": f"Bearer {access_token}"},
            idempotent=True,
        )
        if response.status_code >= 400:
            error = self._remote_error(response)
            if error.error_type == "oauth" and error.error_value == "token_expired":
                raise HHTokenExpiredError("HH access token expired") from error
            if error.error_type == "oauth":
                raise HHAuthorizationError(
                    "HH authorization is no longer valid"
                ) from error
            raise error
        return self._json_object(response, path)

    async def get_current_user(self, access_token: str) -> dict[str, Any]:
        return await self._authorized_get("/me", access_token)

    async def get_my_resumes(self, access_token: str) -> list[HHResumeData]:
        me = await self.get_current_user(access_token)
        resumes_url = me.get("resumes_url")
        if not isinstance(resumes_url, str) or not resumes_url:
            raise HHAuthorizationError("HH account is not an applicant account")
        payload = await self._authorized_get(resumes_url, access_token)
        items = payload.get("items")
        if not isinstance(items, list):
            raise HHAPIError("HH returned an invalid resume list")
        result: list[HHResumeData] = []
        for item in items:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            status = item.get("status")
            result.append(
                HHResumeData(
                    external_id=str(item["id"]),
                    title=str(item.get("title") or "Резюме без названия"),
                    status=(
                        str(status.get("id"))
                        if isinstance(status, dict) and status.get("id")
                        else None
                    ),
                    url=item.get("alternate_url") or item.get("url"),
                    updated_at=_parse_datetime(item.get("updated_at")),
                )
            )
        return result

    async def get_owned_resume(
        self, access_token: str, resume_id: str
    ) -> dict[str, Any]:
        try:
            return await self._authorized_get(f"/resumes/{resume_id}", access_token)
        except HHRemoteError as exc:
            if exc.status_code in {400, 404}:
                raise HHResumeNotFoundError("HH resume is unavailable") from exc
            raise

    async def apply_to_vacancy(
        self,
        access_token: str,
        *,
        resume_id: str,
        vacancy_id: str,
        message: str,
    ) -> str | None:
        path = "/negotiations/response"
        response = await self._send(
            "POST",
            path,
            data={
                "resume_id": resume_id,
                "vacancy_id": vacancy_id,
                "message": message,
            },
            headers={"Authorization": f"Bearer {access_token}"},
            idempotent=False,
        )
        if response.status_code >= 400:
            error = self._remote_error(response)
            if error.error_type == "oauth" and error.error_value == "token_expired":
                raise HHTokenExpiredError("HH access token expired") from error
            if error.error_type == "oauth":
                raise HHAuthorizationError(
                    "HH authorization is no longer valid"
                ) from error
            raise error
        if response.status_code != 201:
            raise HHAPIError(
                f"Unexpected HH application response: {response.status_code}"
            )
        if not response.content:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        if isinstance(payload, dict) and payload.get("id") is not None:
            return str(payload["id"])
        return None

    async def _search_scope(
        self, query: str, max_results: int, filters: dict[str, Any]
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        page = 0
        while len(results) < max_results:
            per_page = min(100, max_results - len(results))
            params: dict[str, Any] = {
                "text": query,
                "period": self.search_period_days,
                "order_by": "publication_time",
                "page": page,
                "per_page": per_page,
                "host": "hh.ru",
                **filters,
            }
            payload = await self._application_get("/vacancies", params=params)
            items = payload.get("items") or []
            if not isinstance(items, list):
                raise HHAPIError("HH returned an invalid vacancy list")
            results.extend(item for item in items if isinstance(item, dict))
            pages = int(payload.get("pages") or 0)
            page += 1
            if not items or page >= pages:
                break
        return results[:max_results]

    async def search_vacancies(
        self, query: str, *, max_results: int = 100
    ) -> list[dict[str, Any]]:
        logger.info("Searching HH vacancies for query %r", query)
        scopes: list[dict[str, Any]] = []
        if self.search_area_id:
            scopes.append({"area": self.search_area_id})
        if self.search_remote:
            scopes.append({"schedule": "remote"})
        if not scopes:
            scopes.append({})
        scoped_results = await asyncio.gather(
            *(self._search_scope(query, max_results, scope) for scope in scopes)
        )
        unique: dict[str, dict[str, Any]] = {}
        for items in scoped_results:
            for item in items:
                external_id = str(item.get("id") or "")
                if external_id:
                    unique[external_id] = item
        ordered = sorted(
            unique.values(),
            key=lambda item: item.get("published_at") or "",
            reverse=True,
        )
        return ordered[:max_results]

    async def get_vacancy(self, vacancy_id: str) -> dict[str, Any]:
        return await self._application_get(f"/vacancies/{vacancy_id}")


def _name(value: Any, default: str) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or default)
    return default


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def vacancy_from_hh(
    details: dict[str, Any], search_item: dict[str, Any] | None = None
) -> VacancyCreate:
    search_item = search_item or {}
    snippet = search_item.get("snippet") or {}
    salary = details.get("salary") or search_item.get("salary") or {}
    key_skills = [
        str(item.get("name"))
        for item in (details.get("key_skills") or [])
        if isinstance(item, dict) and item.get("name")
    ]
    work_formats = details.get("work_format") or []
    format_names = [
        str(item.get("name"))
        for item in work_formats
        if isinstance(item, dict) and item.get("name")
    ]
    work_format = ", ".join(format_names) or _name(
        details.get("schedule") or search_item.get("schedule"), "Не указан"
    )
    return VacancyCreate(
        source="hh",
        external_id=str(details.get("id") or search_item.get("id") or ""),
        title=str(details.get("name") or search_item.get("name") or "Без названия"),
        company=_name(
            details.get("employer") or search_item.get("employer"), "Не указана"
        ),
        url=str(
            details.get("alternate_url")
            or search_item.get("alternate_url")
            or details.get("url")
            or "https://hh.ru"
        ),
        description=html_to_text(details.get("description")),
        requirements=html_to_text(snippet.get("requirement")),
        responsibilities=html_to_text(snippet.get("responsibility")),
        key_skills=key_skills,
        salary_from=int(salary["from"]) if salary.get("from") is not None else None,
        salary_to=int(salary["to"]) if salary.get("to") is not None else None,
        salary_currency=salary.get("currency"),
        salary_gross=salary.get("gross"),
        location=_name(details.get("area") or search_item.get("area"), "Не указана"),
        work_format=work_format,
        experience=_name(
            details.get("experience") or search_item.get("experience"), "Не указан"
        ),
        employment=_name(
            details.get("employment") or search_item.get("employment"), "Не указана"
        ),
        published_at=_parse_datetime(
            details.get("published_at") or search_item.get("published_at")
        ),
    )
