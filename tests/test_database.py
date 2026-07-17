from app.database import normalize_database_url


def test_normalize_database_url_supports_paas_postgres_aliases() -> None:
    expected = "postgresql+asyncpg://user:password@db.example/jobs"

    assert (
        normalize_database_url("postgres://user:password@db.example/jobs")
        == expected
    )
    assert (
        normalize_database_url("postgresql://user:password@db.example/jobs")
        == expected
    )


def test_normalize_database_url_preserves_explicit_async_driver() -> None:
    url = "sqlite+aiosqlite:///./job_agent.db"

    assert normalize_database_url(url) == url
