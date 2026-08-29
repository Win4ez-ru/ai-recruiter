from __future__ import annotations

from typing import ClassVar

import httpx
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)

from app.schemas import SearchErrorCode


class AIServiceError(RuntimeError):
    code: ClassVar[SearchErrorCode] = "ai_unavailable"
    user_message = "AI-модель временно недоступна. Попробуйте позже."


class AIRateLimitError(AIServiceError):
    code: ClassVar[SearchErrorCode] = "ai_rate_limited"
    user_message = "AI-модель временно ограничила запросы. Попробуйте позже."


class AIConfigurationError(AIServiceError):
    code: ClassVar[SearchErrorCode] = "ai_configuration"
    user_message = "AI-провайдер или выбранная модель не настроены."


class AIResponseValidationError(AIServiceError):
    """One model response was malformed; other vacancies may still succeed."""

    code: ClassVar[SearchErrorCode] = "ai_invalid_response"
    user_message = "AI-модель вернула некорректный ответ для части вакансий."


def normalize_openai_error(exc: BaseException) -> AIServiceError:
    if isinstance(exc, RateLimitError):
        return AIRateLimitError()
    if isinstance(
        exc,
        (AuthenticationError, PermissionDeniedError, NotFoundError, BadRequestError),
    ):
        return AIConfigurationError()
    if isinstance(exc, (APIConnectionError, APITimeoutError)):
        return AIServiceError()
    if isinstance(exc, APIStatusError):
        if exc.status_code == 429:
            return AIRateLimitError()
        if exc.status_code < 500:
            return AIConfigurationError()
    return AIServiceError()


def normalize_ollama_error(exc: BaseException) -> AIServiceError:
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        if status_code == 429:
            return AIRateLimitError()
        if status_code < 500:
            return AIConfigurationError()
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return AIServiceError()
    return AIServiceError()
