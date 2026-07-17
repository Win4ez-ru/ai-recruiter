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
    assert "123456:TEST_TOKEN" not in repr(settings)
    assert "test-key" not in repr(settings)


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


def test_proxy_settings_respect_provider_transport_schemes() -> None:
    with pytest.raises(ValidationError, match="http, socks4, socks5"):
        make_settings(telegram_proxy_urls="socks5h://proxy.example:1080")
    with pytest.raises(ValidationError, match="http, https, socks5, socks5h"):
        make_settings(hh_proxy_url="socks4://proxy.example:1080")


def test_platform_port_takes_precedence_over_application_port() -> None:
    settings = make_settings(
        port=9000,
        http_port=8080,
        hh_callback_port=7000,
    )

    assert settings.http_bind_port == 9000


def test_health_paths_must_be_unique() -> None:
    with pytest.raises(ValidationError, match="Health paths must be unique"):
        make_settings(
            health_live_path="/health",
            health_ready_path="/health",
        )


def test_database_credentials_are_redacted() -> None:
    settings = make_settings(
        database_url="postgresql://agent:db-password@db.example/jobs"
    )

    assert settings.database_url_value.endswith("@db.example/jobs")
    assert "db-password" not in repr(settings)
