from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "telegram_bot_token": "123456:TEST_TOKEN",
        "telegram_user_id": 42,
        "openai_api_key": "test-key",
        "openai_model": "test-model",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_proxy_settings_are_parsed_and_redacted() -> None:
    settings = make_settings(
        telegram_proxy_urls=(
            "socks5://user:password@proxy.example:1080, "
            "http://backup.example:8080"
        ),
        hh_proxy_url="socks5://hh.example:1080",
    )

    assert settings.telegram_proxy_values == (
        "socks5://user:password@proxy.example:1080",
        "http://backup.example:8080",
    )
    assert settings.hh_proxy_value == "socks5://hh.example:1080"
    assert "password" not in repr(settings)


def test_telegram_requires_at_least_one_route() -> None:
    with pytest.raises(ValidationError, match="direct route or at least one proxy"):
        make_settings(telegram_direct_enabled=False, telegram_proxy_urls="")


@pytest.mark.parametrize(
    "field",
    ["telegram_proxy_urls", "hh_proxy_url", "openai_proxy_url"],
)
def test_proxy_settings_reject_unsupported_schemes(field: str) -> None:
    with pytest.raises(ValidationError, match="proxy URL"):
        make_settings(**{field: "ftp://proxy.example:21"})
