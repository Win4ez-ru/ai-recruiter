from __future__ import annotations

import json
import logging

from app.logging_config import JsonFormatter, redact_text


def test_json_formatter_emits_structured_fields_and_redacts_secrets() -> None:
    record = logging.LogRecord(
        name="app.test",
        level=logging.WARNING,
        pathname=__file__,
        lineno=10,
        msg=(
            "request failed for Bearer access-value and "
            "socks5://alice:proxy-pass@proxy.example:1080"
        ),
        args=(),
        exc_info=None,
    )
    record.event = "provider_retry"
    record.retry_delay_seconds = 2.5
    record.access_token = "raw-access-token"

    payload = json.loads(JsonFormatter().format(record))

    assert payload["level"] == "WARNING"
    assert payload["event"] == "provider_retry"
    assert payload["retry_delay_seconds"] == 2.5
    assert payload["access_token"] == "[REDACTED]"
    assert "access-value" not in payload["message"]
    assert "proxy-pass" not in payload["message"]


def test_text_redaction_covers_provider_credentials() -> None:
    message = "token=secret-token sk-example123456 123456789:telegram_token_value"

    redacted = redact_text(message)

    assert "secret-token" not in redacted
    assert "sk-example123456" not in redacted
    assert "telegram_token_value" not in redacted
