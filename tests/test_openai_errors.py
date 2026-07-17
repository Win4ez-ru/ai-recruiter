from __future__ import annotations

import httpx
from openai import APIConnectionError, AuthenticationError, RateLimitError

from app.services.openai_errors import (
    OpenAIConfigurationError,
    OpenAIRateLimitError,
    OpenAIServiceError,
    normalize_openai_error,
)


def request() -> httpx.Request:
    return httpx.Request("POST", "https://api.openai.com/v1/responses")


def response(status_code: int) -> httpx.Response:
    return httpx.Response(status_code, request=request())


def test_openai_errors_are_normalized_without_provider_payloads() -> None:
    assert isinstance(
        normalize_openai_error(APIConnectionError(request=request())),
        OpenAIServiceError,
    )
    assert isinstance(
        normalize_openai_error(
            RateLimitError("limited", response=response(429), body=None)
        ),
        OpenAIRateLimitError,
    )
    assert isinstance(
        normalize_openai_error(
            AuthenticationError("invalid", response=response(401), body=None)
        ),
        OpenAIConfigurationError,
    )
