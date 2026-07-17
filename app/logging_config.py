from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Mapping
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Literal

LogFormat = Literal["json", "text"]

_STANDARD_RECORD_FIELDS = frozenset(
    set(logging.makeLogRecord({}).__dict__)
    | {
        "asctime",
        "message",
    }
)
_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "client_secret",
        "openai_api_key",
        "password",
        "refresh_token",
        "secret",
        "telegram_bot_token",
        "token",
        "access_token",
    }
)
_REDACTION_RULES = (
    (re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{8,}\b"), "[REDACTED]"),
    (
        re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b", re.IGNORECASE),
        "[REDACTED]",
    ),
    (re.compile(r"(?i)(Bearer\s+)[^\s,;]+"), r"\1[REDACTED]"),
    (
        re.compile(
            r"(?i)((?:https?|socks4|socks5|socks5h)://)[^/@\s:]+:[^@\s]+@"
        ),
        r"\1[REDACTED]@",
    ),
    (
        re.compile(
            r"(?i)((?:token|api[_-]?key|secret|password)=)[^\s&,;]+"
        ),
        r"\1[REDACTED]",
    ),
)


def redact_text(value: str) -> str:
    redacted = value
    for pattern, replacement in _REDACTION_RULES:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().strip()
    return normalized in _SENSITIVE_KEYS or any(
        normalized.endswith(f"_{suffix}")
        for suffix in ("token", "secret", "password", "api_key")
    )


def _redact_value(value: Any, *, key: str = "") -> Any:
    if _is_sensitive_key(key):
        return "[REDACTED]"
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {
            str(nested_key): _redact_value(
                nested_value,
                key=str(nested_key),
            )
            for nested_key, nested_value in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_redact_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(str(value))


class JsonFormatter(logging.Formatter):
    """Emit one redacted JSON object per log record."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created,
                tz=timezone.utc,
            )
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_FIELDS and not key.startswith("_"):
                payload[key] = _redact_value(value, key=key)
        if record.exc_info:
            payload["exception"] = redact_text(
                self.formatException(record.exc_info)
            )
        if record.stack_info:
            payload["stack"] = redact_text(self.formatStack(record.stack_info))
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )


class RedactingTextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record))


def configure_logging(
    level: str = "INFO",
    *,
    output_format: LogFormat = "json",
    file_enabled: bool = False,
    file_path: Path = Path("logs/job-agent.log"),
    file_max_bytes: int = 2_000_000,
    file_backup_count: int = 3,
) -> None:
    formatter: logging.Formatter
    if output_format == "json":
        formatter = JsonFormatter()
    else:
        formatter = RedactingTextFormatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
        )

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    handlers: list[logging.Handler] = [console]

    if file_enabled:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            file_path,
            maxBytes=file_max_bytes,
            backupCount=file_backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        handlers=handlers,
        force=True,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
