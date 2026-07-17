from __future__ import annotations

from datetime import datetime, timezone

from app.network.retry import RetryPolicy, parse_retry_after


def test_retry_policy_uses_exponential_backoff_and_cap() -> None:
    policy = RetryPolicy(
        max_attempts=5,
        base_delay_seconds=1,
        max_delay_seconds=5,
        jitter_ratio=0,
    )

    assert [policy.delay_seconds(attempt) for attempt in range(1, 5)] == [1, 2, 4, 5]


def test_retry_policy_applies_bounded_jitter() -> None:
    policy = RetryPolicy(base_delay_seconds=10, max_delay_seconds=30, jitter_ratio=0.2)

    assert policy.delay_seconds(1, random_value=lambda: 0.0) == 8
    assert policy.delay_seconds(1, random_value=lambda: 1.0) == 12


def test_retry_after_supports_seconds_and_http_dates() -> None:
    now = datetime(2026, 7, 17, 8, 0, tzinfo=timezone.utc)

    assert parse_retry_after("7", now=now) == 7
    assert parse_retry_after("Fri, 17 Jul 2026 08:00:12 GMT", now=now) == 12
    assert parse_retry_after("invalid", now=now) is None
