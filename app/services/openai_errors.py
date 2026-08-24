"""Compatibility imports for integrations using the old module name."""

from __future__ import annotations

from app.services.ai_errors import (
    AIConfigurationError,
    AIRateLimitError,
    AIServiceError,
    normalize_openai_error,
)

OpenAIServiceError = AIServiceError
OpenAIRateLimitError = AIRateLimitError
OpenAIConfigurationError = AIConfigurationError

__all__ = [
    "OpenAIConfigurationError",
    "OpenAIRateLimitError",
    "OpenAIServiceError",
    "normalize_openai_error",
]
