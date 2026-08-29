from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

import httpx
from openai import AsyncOpenAI, OpenAIError
from pydantic import BaseModel, ValidationError

from app.services.ai_errors import (
    AIResponseValidationError,
    AIServiceError,
    normalize_ollama_error,
    normalize_openai_error,
)

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger(__name__)
SchemaT = TypeVar("SchemaT", bound=BaseModel)
ChatMessage = Mapping[str, str]
TEXT_MAX_OUTPUT_TOKENS = 1_200
STRUCTURED_MAX_OUTPUT_TOKENS = 1_200


class AIProvider(Protocol):
    name: str

    async def generate_text(self, *, model: str, prompt: str) -> str: ...

    async def generate_structured(
        self,
        *,
        model: str,
        messages: Sequence[ChatMessage],
        response_model: type[SchemaT],
    ) -> SchemaT: ...

    async def close(self) -> None: ...


class OpenAIProvider:
    name = "openai"

    def __init__(self, client: AsyncOpenAI) -> None:
        self._client = client

    async def generate_text(self, *, model: str, prompt: str) -> str:
        try:
            response = await self._client.responses.create(
                model=model,
                input=prompt,
                max_output_tokens=TEXT_MAX_OUTPUT_TOKENS,
            )
        except OpenAIError as exc:
            raise normalize_openai_error(exc) from exc
        return (response.output_text or "").strip()

    async def generate_structured(
        self,
        *,
        model: str,
        messages: Sequence[ChatMessage],
        response_model: type[SchemaT],
    ) -> SchemaT:
        try:
            response = await self._client.responses.parse(
                model=model,
                input=list(messages),
                text_format=response_model,
                max_output_tokens=STRUCTURED_MAX_OUTPUT_TOKENS,
            )
        except OpenAIError as exc:
            raise normalize_openai_error(exc) from exc
        if response.output_parsed is None:
            raise AIResponseValidationError("AI provider returned no structured output")
        try:
            return response_model.model_validate(response.output_parsed)
        except ValidationError as exc:
            raise AIResponseValidationError(
                "AI provider returned invalid structured output"
            ) from exc

    async def close(self) -> None:
        await self._client.close()


class OllamaProvider:
    name = "ollama"

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        max_retries: int,
        context_length: int = 16_384,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._max_retries = max_retries
        self._context_length = context_length
        self._owns_http_client = http_client is None
        self._http_client = http_client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds),
            trust_env=False,
        )

    async def generate_text(self, *, model: str, prompt: str) -> str:
        payload = await self._chat(
            {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "think": False,
                "options": {
                    "temperature": 0.3,
                    "num_ctx": self._context_length,
                    "num_predict": TEXT_MAX_OUTPUT_TOKENS,
                },
            }
        )
        return _ollama_message_content(payload).strip()

    async def generate_structured(
        self,
        *,
        model: str,
        messages: Sequence[ChatMessage],
        response_model: type[SchemaT],
    ) -> SchemaT:
        schema = response_model.model_json_schema()
        grounded_messages = [
            {
                "role": "system",
                "content": (
                    "Ответ должен строго соответствовать этой JSON Schema: "
                    f"{json.dumps(schema, ensure_ascii=False)}"
                ),
            },
            *(dict(message) for message in messages),
        ]
        payload = await self._chat(
            {
                "model": model,
                "messages": grounded_messages,
                "stream": False,
                "think": False,
                "format": schema,
                "options": {
                    "temperature": 0,
                    "num_ctx": self._context_length,
                    "num_predict": STRUCTURED_MAX_OUTPUT_TOKENS,
                },
            }
        )
        try:
            return response_model.model_validate_json(_ollama_message_content(payload))
        except (ValidationError, ValueError) as exc:
            raise AIResponseValidationError(
                "Ollama returned invalid structured output"
            ) from exc

    async def _chat(self, body: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._http_client.post("/api/chat", json=body)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("Ollama response is not an object")
                return payload
            except (httpx.HTTPError, ValueError) as exc:
                retryable = _ollama_retryable(exc)
                if retryable and attempt < self._max_retries:
                    await asyncio.sleep(min(0.5 * (2**attempt), 2.0))
                    continue
                logger.warning(
                    "Ollama request failed",
                    extra={
                        "event": "ollama_request_failed",
                        "error_type": type(exc).__name__,
                    },
                )
                raise normalize_ollama_error(exc) from exc
        raise AIServiceError("Ollama request failed")

    async def close(self) -> None:
        if self._owns_http_client:
            await self._http_client.aclose()


class YandexProvider:
    """Yandex AI Studio provider over its OpenAI-compatible Chat API."""

    name = "yandex"

    def __init__(self, client: AsyncOpenAI) -> None:
        self._client = client

    async def generate_text(self, *, model: str, prompt: str) -> str:
        try:
            response = await self._client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=TEXT_MAX_OUTPUT_TOKENS,
            )
        except OpenAIError as exc:
            raise normalize_openai_error(exc) from exc
        return _chat_completion_content(response).strip()

    async def generate_structured(
        self,
        *,
        model: str,
        messages: Sequence[ChatMessage],
        response_model: type[SchemaT],
    ) -> SchemaT:
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": response_model.__name__,
                "schema": response_model.model_json_schema(),
                "strict": True,
            },
        }
        try:
            response = await self._client.chat.completions.create(
                model=model,
                messages=[dict(message) for message in messages],  # type: ignore[arg-type]
                temperature=0,
                max_tokens=STRUCTURED_MAX_OUTPUT_TOKENS,
                response_format=response_format,  # type: ignore[arg-type]
            )
        except OpenAIError as exc:
            raise normalize_openai_error(exc) from exc

        if response.choices and response.choices[0].finish_reason == "length":
            raise AIResponseValidationError(
                "Yandex structured output exceeded the token limit"
            )
        content = _chat_completion_content(response)
        try:
            return response_model.model_validate_json(content)
        except (ValidationError, ValueError) as exc:
            raise AIResponseValidationError(
                "Yandex returned invalid structured output"
            ) from exc

    async def close(self) -> None:
        await self._client.close()


def build_ai_provider(settings: Settings) -> tuple[AIProvider, str]:
    if settings.ai_provider == "ollama":
        return (
            OllamaProvider(
                base_url=settings.ollama_base_url,
                timeout_seconds=settings.ollama_timeout_seconds,
                max_retries=settings.ollama_max_retries,
                context_length=settings.ollama_context_length,
            ),
            settings.ollama_model,
        )

    if settings.ai_provider == "yandex":
        api_key = settings.yandex_api_key_value
        http_client = httpx.AsyncClient(
            proxy=settings.yandex_proxy_value,
            timeout=httpx.Timeout(settings.yandex_timeout_seconds),
            trust_env=settings.yandex_trust_env,
        )
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=settings.yandex_base_url,
            project=settings.yandex_folder_id,
            default_headers={
                "Authorization": f"Api-Key {api_key}",
                "x-data-logging-enabled": str(
                    settings.yandex_data_logging_enabled
                ).lower(),
            },
            max_retries=settings.yandex_max_retries,
            timeout=settings.yandex_timeout_seconds,
            http_client=http_client,
        )
        return YandexProvider(client), settings.yandex_model_uri

    http_client = httpx.AsyncClient(
        proxy=settings.openai_proxy_value,
        timeout=httpx.Timeout(settings.openai_timeout_seconds),
        trust_env=settings.openai_trust_env,
    )
    client = AsyncOpenAI(
        api_key=settings.openai_api_key_value,
        max_retries=settings.openai_max_retries,
        timeout=settings.openai_timeout_seconds,
        http_client=http_client,
    )
    return OpenAIProvider(client), settings.openai_model


def _ollama_message_content(payload: Mapping[str, Any]) -> str:
    message = payload.get("message")
    if not isinstance(message, Mapping):
        raise AIServiceError("Ollama response has no message")
    content = message.get("content")
    if not isinstance(content, str):
        raise AIServiceError("Ollama response has no text content")
    return content


def _chat_completion_content(response: Any) -> str:
    choices = getattr(response, "choices", None)
    if not choices:
        raise AIServiceError("AI provider returned no choices")
    content = choices[0].message.content
    if not isinstance(content, str) or not content.strip():
        raise AIServiceError("AI provider returned no text content")
    return content


def _ollama_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    return isinstance(exc, httpx.HTTPStatusError) and (
        exc.response.status_code == 429 or exc.response.status_code >= 500
    )
