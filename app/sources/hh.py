from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
from datetime import datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlencode

import httpx

from app.schemas import HHResumeData, VacancyCreate
from app.sources.base import VacancySource

logger = logging.getLogger(__name__)
HH_BASE_URL = "https://api.hh.ru"
SPB_AREA_ID = "2"


class HHAPIError(RuntimeError):
    """Base exception for safe HeadHunter client failures."""


class HHAuthorizationError(HHAPIError):
    """HeadHunter user authorization is absent or invalid."""


class HHTokenExpiredError(HHAuthorizationError):
    """HeadHunter access token has expired."""


class HHResumeNotFoundError(HHAPIError):
    """Requested HeadHunter resume is unavailable to the current user."""


class HHRemoteError(HHAPIError):
    def __init__(
        self,
        status_code: int,
        error_type: str,
        error_value: str | None = None,
        *,
        fallback_url: str | None = None,
    ) -> None:
        super().__init__(f"HH API error: {status_code} {error_type}")
        self.status_code = status_code
        self.error_type = error_type
        self.error_value = error_value
        self.fallback_url = fallback_url


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
        timeout: float = 15.0,
        retries: int = 3,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.retries = retries
        self.api_base_url = api_base_url.rstrip("/")
        self.auth_base_url = auth_base_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            base_url=self.api_base_url,
            timeout=httpx.Timeout(timeout, connect=5.0, read=15.0, write=15.0),
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

    async def _request(
        self, path: str, *, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                response = await self._client.get(path, params=params)
                if response.status_code == 404:
                    raise HHAPIError(f"HH resource not found: {path}")
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt >= self.retries:
                        response.raise_for_status()
                    retry_after = response.headers.get("Retry-After")
                    try:
                        delay = min(float(retry_after), 30.0) if retry_after else 2**attempt
                    except ValueError:
                        delay = 2**attempt
                    logger.warning(
                        "Temporary HH error %s for %s; retry in %.1fs",
                        response.status_code,
                        path,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                response.raise_for_status()
                if not response.content:
                    raise HHAPIError(f"Empty HH response for {path}")
                payload = response.json()
                if not isinstance(payload, dict):
                    raise HHAPIError(f"Unexpected HH response for {path}")
                return payload
            except HHAPIError:
                raise
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500 and exc.response.status_code != 429:
                    raise HHAPIError(
                        f"HH returned HTTP {exc.response.status_code} for {path}"
                    ) from exc
                last_error = exc
                if attempt >= self.retries:
                    break
                delay = 2**attempt
                logger.warning("HH request failed for %s; retry in %ss", path, delay)
                await asyncio.sleep(delay)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                if attempt >= self.retries:
                    break
                delay = 2**attempt
                logger.warning("HH request failed for %s; retry in %ss", path, delay)
                await asyncio.sleep(delay)
            except (ValueError, TypeError) as exc:
                raise HHAPIError(f"Invalid HH response for {path}") from exc
        raise HHAPIError(f"HH request failed for {path}") from last_error

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
            response = await self._client.post("/token", data=data)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
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
        )

    async def _authorized_get(
        self, path: str, access_token: str
    ) -> dict[str, Any]:
        try:
            response = await self._client.get(
                path, headers={"Authorization": f"Bearer {access_token}"}
            )
        except httpx.TimeoutException as exc:
            raise HHAPIError("HH request timed out") from exc
        except httpx.TransportError as exc:
            raise HHAPIError("HH request failed") from exc
        if response.status_code >= 400:
            error = self._remote_error(response)
            if error.error_type == "oauth" and error.error_value == "token_expired":
                raise HHTokenExpiredError("HH access token expired") from error
            if error.error_type == "oauth":
                raise HHAuthorizationError("HH authorization is no longer valid") from error
            raise error
        try:
            payload = response.json()
        except ValueError as exc:
            raise HHAPIError("HH returned an invalid response") from exc
        if not isinstance(payload, dict):
            raise HHAPIError("HH returned an unexpected response")
        return payload

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

    async def _search_scope(
        self, query: str, max_results: int, filters: dict[str, Any]
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        page = 0
        while len(results) < max_results:
            per_page = min(100, max_results - len(results))
            params: dict[str, Any] = {
                "text": query,
                "period": 7,
                "order_by": "publication_time",
                "page": page,
                "per_page": per_page,
                "host": "hh.ru",
                **filters,
            }
            payload = await self._request("/vacancies", params=params)
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
        local, remote = await asyncio.gather(
            self._search_scope(query, max_results, {"area": SPB_AREA_ID}),
            self._search_scope(query, max_results, {"schedule": "remote"}),
        )
        unique: dict[str, dict[str, Any]] = {}
        for item in [*local, *remote]:
            external_id = str(item.get("id") or "")
            if external_id:
                unique[external_id] = item
        ordered = sorted(
            unique.values(), key=lambda item: item.get("published_at") or "", reverse=True
        )
        return ordered[:max_results]

    async def get_vacancy(self, vacancy_id: str) -> dict[str, Any]:
        return await self._request(f"/vacancies/{vacancy_id}")


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
