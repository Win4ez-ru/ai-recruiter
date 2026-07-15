from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Application configuration loaded from environment variables and .env."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    telegram_bot_token: str
    telegram_user_id: int
    openai_api_key: str
    openai_model: str
    database_url: str = "sqlite+aiosqlite:///./job_agent.db"
    hh_user_agent: str = "KirillJobAgent/1.0"
    search_interval_hours: int = Field(default=12, ge=1)
    min_score_to_send: int = Field(default=65, ge=0, le=100)
    max_vacancies_per_digest: int = Field(default=10, ge=1, le=50)
    log_level: str = "INFO"
    candidate_profile_path: Path = PROJECT_ROOT / "data" / "candidate_profile.json"
    resume_path: Path = PROJECT_ROOT / "data" / "resume.txt"

    @field_validator(
        "telegram_bot_token", "openai_api_key", "openai_model", "hh_user_agent"
    )
    @classmethod
    def must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("значение не должно быть пустым")
        return value.strip()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
