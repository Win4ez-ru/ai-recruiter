from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "telegram_bot_token": "123456:TEST_TOKEN",
        "telegram_user_id": 42,
        "ai_provider": "openai",
        "openai_api_key": "test-key",
        "openai_model": "test-model",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_proxy_settings_are_parsed_and_redacted() -> None:
    settings = make_settings(
        telegram_proxy_urls=(
            "socks5://user:password@proxy.example:1080, http://backup.example:8080"
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
    [
        "telegram_proxy_urls",
        "hh_proxy_url",
        "openai_proxy_url",
        "yandex_proxy_url",
    ],
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


def test_ollama_provider_does_not_require_openai_credentials() -> None:
    settings = make_settings(
        ai_provider="ollama",
        openai_api_key="",
        openai_model="",
        ollama_model="qwen3:4b-instruct",
    )

    assert settings.ai_provider == "ollama"
    assert settings.ollama_model == "qwen3:4b-instruct"
    assert settings.ollama_base_url == "http://127.0.0.1:11434"


def test_openai_provider_still_requires_key_and_model() -> None:
    with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
        make_settings(openai_api_key="")
    with pytest.raises(ValidationError, match="OPENAI_MODEL"):
        make_settings(openai_model="")


def test_ollama_provider_requires_model() -> None:
    with pytest.raises(ValidationError, match="OLLAMA_MODEL"):
        make_settings(ai_provider="ollama", ollama_model="")


def test_yandex_provider_builds_explicit_model_uri() -> None:
    settings = make_settings(
        ai_provider="yandex",
        openai_api_key="",
        openai_model="",
        yandex_api_key="test-yandex-key",
        yandex_folder_id="folder-42",
        yandex_model="yandexgpt-5.1",
    )

    assert settings.yandex_model_uri == "gpt://folder-42/yandexgpt-5.1"
    assert settings.yandex_data_logging_enabled is False


def test_hh_search_policy_is_configurable() -> None:
    settings = make_settings(
        hh_search_queries=" Swift Developer ; iOS Developer\nSwift Developer ",
        hh_search_area_id="1",
        hh_search_period_days=14,
        hh_search_remote=False,
    )

    assert settings.hh_search_queries == ["Swift Developer", "iOS Developer"]
    assert settings.hh_search_area_id == "1"
    assert settings.hh_search_period_days == 14
    assert settings.hh_search_remote is False


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("yandex_api_key", "YANDEX_API_KEY"),
        ("yandex_folder_id", "YANDEX_FOLDER_ID"),
        ("yandex_model", "YANDEX_MODEL"),
    ],
)
def test_yandex_provider_requires_credentials_and_model(
    field: str, message: str
) -> None:
    values = {
        "ai_provider": "yandex",
        "openai_api_key": "",
        "openai_model": "",
        "yandex_api_key": "test-yandex-key",
        "yandex_folder_id": "folder-42",
        "yandex_model": "yandexgpt-5.1",
        field: "",
    }
    with pytest.raises(ValidationError, match=message):
        make_settings(**values)


@pytest.mark.parametrize(
    "url",
    ["ftp://127.0.0.1:11434", "http://127.0.0.1:11434/v1"],
)
def test_ollama_base_url_must_be_an_http_origin(url: str) -> None:
    with pytest.raises(ValidationError, match="Ollama base URL"):
        make_settings(ai_provider="ollama", ollama_base_url=url)
