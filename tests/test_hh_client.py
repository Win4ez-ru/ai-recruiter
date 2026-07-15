from __future__ import annotations

import httpx
import pytest

from app.sources.hh import HHClient, html_to_text, vacancy_from_hh


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
