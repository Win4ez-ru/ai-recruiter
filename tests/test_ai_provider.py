from __future__ import annotations

import json

import httpx
import pytest
from openai import AsyncOpenAI
from pydantic import BaseModel

from app.services.ai_errors import AIConfigurationError, AIServiceError
from app.services.ai_provider import OllamaProvider, YandexProvider


class RankedResult(BaseModel):
    score: int
    reason: str


@pytest.mark.asyncio
async def test_ollama_provider_generates_and_validates_structured_output() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "message": {
                    "role": "assistant",
                    "content": '{"score":87,"reason":"Хорошее совпадение"}',
                }
            },
        )

    http_client = httpx.AsyncClient(
        base_url="http://ollama.test",
        transport=httpx.MockTransport(handler),
    )
    provider = OllamaProvider(
        base_url="http://ollama.test",
        timeout_seconds=30,
        max_retries=0,
        http_client=http_client,
    )

    result = await provider.generate_structured(
        model="qwen3:4b-instruct",
        messages=[{"role": "user", "content": "Оцени вакансию"}],
        response_model=RankedResult,
    )

    assert result == RankedResult(score=87, reason="Хорошее совпадение")
    assert captured["model"] == "qwen3:4b-instruct"
    assert captured["stream"] is False
    assert captured["format"]["required"] == ["score", "reason"]
    assert captured["options"]["temperature"] == 0
    await http_client.aclose()


@pytest.mark.asyncio
async def test_ollama_provider_generates_plain_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": "  Письмо  "}},
        )

    http_client = httpx.AsyncClient(
        base_url="http://ollama.test",
        transport=httpx.MockTransport(handler),
    )
    provider = OllamaProvider(
        base_url="http://ollama.test",
        timeout_seconds=30,
        max_retries=0,
        http_client=http_client,
    )

    assert (
        await provider.generate_text(model="qwen3:4b-instruct", prompt="Напиши письмо")
        == "Письмо"
    )
    await http_client.aclose()


@pytest.mark.asyncio
async def test_ollama_provider_normalizes_missing_model() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model not found"})

    http_client = httpx.AsyncClient(
        base_url="http://ollama.test",
        transport=httpx.MockTransport(handler),
    )
    provider = OllamaProvider(
        base_url="http://ollama.test",
        timeout_seconds=30,
        max_retries=0,
        http_client=http_client,
    )

    with pytest.raises(AIConfigurationError):
        await provider.generate_text(model="missing", prompt="test")
    await http_client.aclose()


@pytest.mark.asyncio
async def test_ollama_provider_rejects_invalid_structured_output() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": "not-json"}},
        )

    http_client = httpx.AsyncClient(
        base_url="http://ollama.test",
        transport=httpx.MockTransport(handler),
    )
    provider = OllamaProvider(
        base_url="http://ollama.test",
        timeout_seconds=30,
        max_retries=0,
        http_client=http_client,
    )

    with pytest.raises(AIServiceError):
        await provider.generate_structured(
            model="qwen3:4b-instruct",
            messages=[{"role": "user", "content": "test"}],
            response_model=RankedResult,
        )
    await http_client.aclose()


def _chat_response(content: str, *, finish_reason: str = "stop") -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1,
        "model": "gpt://folder-42/yandexgpt-5.1",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        },
    }


@pytest.mark.asyncio
async def test_yandex_provider_uses_private_structured_chat_completion() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["authorization"] = request.headers.get("Authorization")
        captured["project"] = request.headers.get("OpenAI-Project")
        captured["data_logging"] = request.headers.get("x-data-logging-enabled")
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json=_chat_response('{"score":91,"reason":"Сильное совпадение"}'),
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AsyncOpenAI(
        api_key="test-yandex-key",
        base_url="https://ai.api.cloud.yandex.net/v1",
        project="folder-42",
        default_headers={
            "Authorization": "Api-Key test-yandex-key",
            "x-data-logging-enabled": "false",
        },
        http_client=http_client,
    )
    provider = YandexProvider(client)

    result = await provider.generate_structured(
        model="gpt://folder-42/yandexgpt-5.1",
        messages=[{"role": "user", "content": "Оцени вакансию"}],
        response_model=RankedResult,
    )

    assert result == RankedResult(score=91, reason="Сильное совпадение")
    assert captured["path"] == "/v1/chat/completions"
    assert captured["authorization"] == "Api-Key test-yandex-key"
    assert captured["project"] == "folder-42"
    assert captured["data_logging"] == "false"
    assert captured["body"]["response_format"]["type"] == "json_schema"
    assert captured["body"]["response_format"]["json_schema"]["strict"] is True
    assert captured["body"]["temperature"] == 0
    await provider.close()


@pytest.mark.asyncio
async def test_yandex_provider_rejects_truncated_structured_output() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chat_response("{}", finish_reason="length"))

    client = AsyncOpenAI(
        api_key="test",
        base_url="https://yandex.test/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    provider = YandexProvider(client)

    with pytest.raises(AIServiceError, match="token limit"):
        await provider.generate_structured(
            model="gpt://folder/model",
            messages=[{"role": "user", "content": "test"}],
            response_model=RankedResult,
        )
    await provider.close()
