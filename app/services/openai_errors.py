from __future__ import annotations

from typing import ClassVar

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


class OpenAIServiceError(RuntimeError):
    code: ClassVar[SearchErrorCode] = "openai_unavailable"
    user_message = "OpenAI временно недоступен. Попробуйте позже."


class OpenAIRateLimitError(OpenAIServiceError):
    code: ClassVar[SearchErrorCode] = "openai_rate_limited"
    user_message = "OpenAI временно ограничил запросы. Попробуйте позже."


class OpenAIConfigurationError(OpenAIServiceError):
    code: ClassVar[SearchErrorCode] = "openai_configuration"
    user_message = "OpenAI не настроен или выбранная модель недоступна."


def normalize_openai_error(exc: BaseException) -> OpenAIServiceError:
    if isinstance(exc, RateLimitError):
        return OpenAIRateLimitError()
    if isinstance(
        exc,
        (AuthenticationError, PermissionDeniedError, NotFoundError, BadRequestError),
    ):
        return OpenAIConfigurationError()
    if isinstance(exc, (APIConnectionError, APITimeoutError)):
        return OpenAIServiceError()
    if isinstance(exc, APIStatusError):
        if exc.status_code == 429:
            return OpenAIRateLimitError()
        if exc.status_code < 500:
            return OpenAIConfigurationError()
    return OpenAIServiceError()
