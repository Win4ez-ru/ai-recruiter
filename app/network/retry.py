from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bounded exponential backoff configuration shared by network clients."""

    max_attempts: int = 4
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 30.0
    jitter_ratio: float = 0.2

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.base_delay_seconds <= 0:
            raise ValueError("base_delay_seconds must be positive")
        if self.max_delay_seconds < self.base_delay_seconds:
            raise ValueError("max_delay_seconds must be at least base_delay_seconds")
        if not 0 <= self.jitter_ratio <= 1:
            raise ValueError("jitter_ratio must be between 0 and 1")

    def delay_seconds(
        self,
        failed_attempt: int,
        *,
        retry_after: str | None = None,
        random_value: Callable[[], float] = random.random,
        now: datetime | None = None,
    ) -> float:
        """Return a capped delay after a one-based failed attempt."""

        if failed_attempt < 1:
            raise ValueError("failed_attempt must be at least 1")
        server_delay = parse_retry_after(retry_after, now=now)
        if server_delay is not None:
            return min(server_delay, self.max_delay_seconds)

        base = min(
            self.base_delay_seconds * (2 ** (failed_attempt - 1)),
            self.max_delay_seconds,
        )
        if self.jitter_ratio == 0:
            return base
        jitter = ((random_value() * 2) - 1) * self.jitter_ratio
        return max(0.0, min(base * (1 + jitter), self.max_delay_seconds))


def parse_retry_after(
    value: str | None, *, now: datetime | None = None
) -> float | None:
    """Parse Retry-After seconds or an HTTP date without raising."""

    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        target = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return max(0.0, (target - current).total_seconds())
