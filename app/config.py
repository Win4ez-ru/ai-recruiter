from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TELEGRAM_PROXY_SCHEMES = {"http", "socks4", "socks5"}
HTTPX_PROXY_SCHEMES = {"http", "https", "socks5", "socks5h"}
DEFAULT_HH_SEARCH_QUERIES = [
    "iOS Developer",
    "iOS-разработчик",
    "Junior iOS Developer",
    "Swift Developer",
    "SwiftUI Developer",
    "стажер iOS",
    "мобильный разработчик Swift",
]


def _parse_proxy_list(value: Any) -> list[SecretStr]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        normalized = value.replace(";", ",").replace("\n", ",")
        return [
            SecretStr(item.strip()) for item in normalized.split(",") if item.strip()
        ]
    if isinstance(value, (list, tuple)):
        return [
            item if isinstance(item, SecretStr) else SecretStr(str(item))
            for item in value
        ]
    raise TypeError("proxy list must be a comma-separated string")


def _parse_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        normalized = value.replace(";", ",").replace("\n", ",")
        return [item.strip() for item in normalized.split(",") if item.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    raise TypeError("value must be a comma-separated string")


def _validate_proxy_url(
    value: SecretStr | None, *, allowed_schemes: set[str]
) -> SecretStr | None:
    if value is None:
        return None
    raw_value = value.get_secret_value().strip()
    if not raw_value:
        return None
    parsed = urlparse(raw_value)
    if parsed.scheme.lower() not in allowed_schemes or not parsed.hostname:
        allowed = ", ".join(sorted(allowed_schemes))
        raise ValueError(f"proxy URL must use one of: {allowed}")
    return SecretStr(raw_value)


class Settings(BaseSettings):
    """Application configuration loaded from environment variables and .env."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    telegram_bot_token: SecretStr
    telegram_user_id: int
    telegram_direct_enabled: bool = True
    telegram_proxy_urls: Annotated[list[SecretStr], NoDecode] = Field(
        default_factory=list
    )
    telegram_request_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    telegram_polling_timeout_seconds: int = Field(default=30, ge=1, le=60)
    telegram_polling_backoff_min_seconds: float = Field(default=1.0, gt=0, le=60)
    telegram_polling_backoff_max_seconds: float = Field(default=30.0, gt=0, le=300)
    telegram_polling_backoff_factor: float = Field(default=1.7, gt=1, le=10)
    telegram_polling_backoff_jitter: float = Field(default=0.2, ge=0, le=1)
    telegram_route_cooldown_seconds: float = Field(default=60.0, ge=0, le=3600)
    demo_mode: bool = False
    ai_provider: Literal["openai", "ollama", "yandex"] = "openai"
    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = ""
    openai_proxy_url: SecretStr | None = None
    openai_trust_env: bool = False
    openai_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    openai_max_retries: int = Field(default=3, ge=0, le=10)
    yandex_api_key: SecretStr = SecretStr("")
    yandex_folder_id: str = ""
    yandex_model: str = "yandexgpt-5.1"
    yandex_base_url: str = "https://ai.api.cloud.yandex.net/v1"
    yandex_proxy_url: SecretStr | None = None
    yandex_trust_env: bool = False
    yandex_timeout_seconds: float = Field(default=120.0, gt=0, le=1200)
    yandex_max_retries: int = Field(default=3, ge=0, le=10)
    yandex_data_logging_enabled: bool = False
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:4b-instruct"
    ollama_timeout_seconds: float = Field(default=180.0, gt=0, le=1800)
    ollama_max_retries: int = Field(default=2, ge=0, le=10)
    ollama_context_length: int = Field(default=16_384, ge=2_048, le=131_072)
    database_url: SecretStr = SecretStr("sqlite+aiosqlite:///./job_agent.db")
    database_auto_create: bool = True
    database_connect_timeout_seconds: float = Field(default=10.0, gt=0, le=300)
    database_pool_size: int = Field(default=5, ge=1, le=100)
    database_max_overflow: int = Field(default=10, ge=0, le=200)
    database_pool_recycle_seconds: int = Field(default=1800, ge=60)
    hh_user_agent: str = "KirillJobAgent/1.0"
    hh_search_queries: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: list(DEFAULT_HH_SEARCH_QUERIES)
    )
    hh_search_area_id: str = "2"
    hh_search_period_days: int = Field(default=7, ge=1, le=30)
    hh_search_remote: bool = True
    hh_client_id: str = ""
    hh_client_secret: SecretStr = SecretStr("")
    hh_default_resume_id: str = ""
    hh_redirect_uri: str = "http://127.0.0.1:8080/oauth/hh/callback"
    hh_auth_base_url: str = "https://hh.ru"
    hh_api_base_url: str = "https://api.hh.ru"
    hh_proxy_url: SecretStr | None = None
    hh_trust_env: bool = False
    hh_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=120)
    hh_read_timeout_seconds: float = Field(default=15.0, gt=0, le=300)
    hh_write_timeout_seconds: float = Field(default=15.0, gt=0, le=300)
    hh_pool_timeout_seconds: float = Field(default=5.0, gt=0, le=120)
    hh_retry_attempts: int = Field(default=4, ge=1, le=10)
    hh_retry_base_delay_seconds: float = Field(default=1.0, gt=0, le=60)
    hh_retry_max_delay_seconds: float = Field(default=30.0, gt=0, le=300)
    hh_retry_jitter_ratio: float = Field(default=0.2, ge=0, le=1)
    hh_oauth_state_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    hh_confirmation_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    hh_submission_recovery_seconds: int = Field(default=300, ge=60, le=3600)
    hh_callback_host: str = "127.0.0.1"
    hh_callback_port: int = Field(default=8080, ge=1, le=65535)
    http_host: str | None = None
    http_port: int | None = Field(default=None, ge=1, le=65535)
    port: int | None = Field(default=None, ge=1, le=65535)
    healthcheck_enabled: bool = True
    health_live_path: str = "/health/live"
    health_ready_path: str = "/health/ready"
    search_interval_hours: int = Field(default=12, ge=1)
    vacancy_refresh_ttl_hours: int = Field(default=24, ge=1, le=720)
    max_ai_analyses_per_search: int = Field(default=25, ge=1, le=200)
    min_score_to_send: int = Field(default=65, ge=0, le=100)
    max_vacancies_per_digest: int = Field(default=10, ge=1, le=50)
    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"
    log_file_enabled: bool = False
    log_file_path: Path = PROJECT_ROOT / "logs" / "job-agent.log"
    log_file_max_bytes: int = Field(default=2_000_000, ge=10_000)
    log_file_backup_count: int = Field(default=3, ge=0, le=100)
    candidate_profile_path: Path = PROJECT_ROOT / "data" / "candidate_profile.json"
    resume_path: Path = PROJECT_ROOT / "data" / "resume.txt"

    @field_validator("hh_user_agent")
    @classmethod
    def must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("значение не должно быть пустым")
        return value.strip()

    @field_validator("telegram_bot_token")
    @classmethod
    def secret_must_not_be_blank(cls, value: SecretStr) -> SecretStr:
        raw_value = value.get_secret_value().strip()
        if not raw_value:
            raise ValueError("значение не должно быть пустым")
        return SecretStr(raw_value)

    @field_validator("telegram_proxy_urls", mode="before")
    @classmethod
    def parse_telegram_proxy_urls(cls, value: Any) -> list[SecretStr]:
        return _parse_proxy_list(value)

    @field_validator("hh_search_queries", mode="before")
    @classmethod
    def parse_hh_search_queries(cls, value: Any) -> list[str]:
        return _parse_string_list(value)

    @field_validator("hh_search_queries")
    @classmethod
    def validate_hh_search_queries(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("HH_SEARCH_QUERIES must contain at least one query")
        return list(dict.fromkeys(value))

    @field_validator("telegram_proxy_urls")
    @classmethod
    def validate_telegram_proxy_urls(cls, values: list[SecretStr]) -> list[SecretStr]:
        return [
            value
            for item in values
            if (
                value := _validate_proxy_url(
                    item, allowed_schemes=TELEGRAM_PROXY_SCHEMES
                )
            )
        ]

    @field_validator("hh_proxy_url", "openai_proxy_url", "yandex_proxy_url")
    @classmethod
    def validate_optional_proxy(cls, value: SecretStr | None) -> SecretStr | None:
        return _validate_proxy_url(value, allowed_schemes=HTTPX_PROXY_SCHEMES)

    @field_validator("ollama_base_url")
    @classmethod
    def validate_ollama_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        parsed = urlparse(normalized)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError(
                "Ollama base URL must be an http(s) origin without /v1 or /api"
            )
        return normalized

    @field_validator("yandex_base_url")
    @classmethod
    def validate_yandex_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        parsed = urlparse(normalized)
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Yandex base URL must be an https URL")
        return normalized

    @field_validator("health_live_path", "health_ready_path")
    @classmethod
    def validate_http_path(cls, value: str) -> str:
        path = value.strip()
        if not path.startswith("/") or "?" in path or "#" in path:
            raise ValueError("health path must be an absolute URL path")
        return path

    @model_validator(mode="after")
    def validate_network_configuration(self) -> "Settings":
        if self.ai_provider == "openai":
            if not self.openai_api_key.get_secret_value().strip():
                raise ValueError("OPENAI_API_KEY is required for AI_PROVIDER=openai")
            if not self.openai_model.strip():
                raise ValueError("OPENAI_MODEL is required for AI_PROVIDER=openai")
            self.openai_model = self.openai_model.strip()
        elif self.ai_provider == "ollama":
            if not self.ollama_model.strip():
                raise ValueError("OLLAMA_MODEL is required for AI_PROVIDER=ollama")
            self.ollama_model = self.ollama_model.strip()
        else:
            if not self.yandex_api_key.get_secret_value().strip():
                raise ValueError("YANDEX_API_KEY is required for AI_PROVIDER=yandex")
            if not self.yandex_folder_id.strip():
                raise ValueError("YANDEX_FOLDER_ID is required for AI_PROVIDER=yandex")
            if not self.yandex_model.strip():
                raise ValueError("YANDEX_MODEL is required for AI_PROVIDER=yandex")
            self.yandex_folder_id = self.yandex_folder_id.strip()
            self.yandex_model = self.yandex_model.strip()
        if not self.telegram_direct_enabled and not self.telegram_proxy_urls:
            raise ValueError(
                "Telegram requires a direct route or at least one proxy URL"
            )
        if (
            self.telegram_polling_backoff_max_seconds
            <= self.telegram_polling_backoff_min_seconds
        ):
            raise ValueError("Telegram polling backoff max must be greater than min")
        if self.hh_retry_max_delay_seconds < self.hh_retry_base_delay_seconds:
            raise ValueError("HH retry max delay must be greater than or equal to base")
        health_paths = {self.health_live_path, self.health_ready_path}
        if self.healthcheck_enabled and len(health_paths) != 2:
            raise ValueError("Health paths must be unique")
        if (
            self.healthcheck_enabled
            and self.hh_oauth_configured
            and self.hh_callback_path in health_paths
        ):
            raise ValueError("OAuth callback and health paths must be unique")
        return self

    @property
    def hh_oauth_configured(self) -> bool:
        return bool(
            self.hh_client_id.strip()
            and self.hh_client_secret.get_secret_value().strip()
            and self.hh_redirect_uri.strip()
        )

    @property
    def telegram_bot_token_value(self) -> str:
        return self.telegram_bot_token.get_secret_value()

    @property
    def openai_api_key_value(self) -> str:
        return self.openai_api_key.get_secret_value()

    @property
    def yandex_api_key_value(self) -> str:
        return self.yandex_api_key.get_secret_value()

    @property
    def yandex_model_uri(self) -> str:
        if self.yandex_model.startswith("gpt://"):
            return self.yandex_model
        return f"gpt://{self.yandex_folder_id}/{self.yandex_model.lstrip('/')}"

    @property
    def hh_client_secret_value(self) -> str:
        return self.hh_client_secret.get_secret_value()

    @property
    def database_url_value(self) -> str:
        return self.database_url.get_secret_value()

    @property
    def hh_callback_path(self) -> str:
        return urlparse(self.hh_redirect_uri).path or "/oauth/hh/callback"

    @property
    def telegram_proxy_values(self) -> tuple[str, ...]:
        return tuple(item.get_secret_value() for item in self.telegram_proxy_urls)

    @property
    def hh_proxy_value(self) -> str | None:
        return self.hh_proxy_url.get_secret_value() if self.hh_proxy_url else None

    @property
    def openai_proxy_value(self) -> str | None:
        return (
            self.openai_proxy_url.get_secret_value() if self.openai_proxy_url else None
        )

    @property
    def yandex_proxy_value(self) -> str | None:
        return (
            self.yandex_proxy_url.get_secret_value() if self.yandex_proxy_url else None
        )

    @property
    def http_bind_host(self) -> str:
        return self.http_host or self.hh_callback_host

    @property
    def http_bind_port(self) -> int:
        return self.port or self.http_port or self.hh_callback_port


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
