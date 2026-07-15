from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from html.parser import HTMLParser
from typing import Any

import httpx

from app.schemas import VacancyCreate
from app.sources.base import VacancySource

logger = logging.getLogger(__name__)
HH_BASE_URL = "https://api.hh.ru"
SPB_AREA_ID = "2"


class HHAPIError(RuntimeError):
    pass


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
        timeout: float = 15.0,
        retries: int = 3,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.retries = retries
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            base_url=HH_BASE_URL,
            timeout=httpx.Timeout(timeout),
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
