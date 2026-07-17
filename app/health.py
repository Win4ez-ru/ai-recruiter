from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Literal

HealthStatus = Literal["starting", "ok", "degraded", "down", "stopping"]


@dataclass(frozen=True, slots=True)
class ComponentHealth:
    status: HealthStatus
    detail: str | None
    updated_at: str


class HealthRegistry:
    """In-memory lifecycle state used by passive health probes."""

    def __init__(self, *, clock: Any = time.monotonic) -> None:
        self._clock = clock
        self._started_at = self._clock()
        self._ready = False
        self._stopping = False
        self._components: dict[str, ComponentHealth] = {}

    @property
    def ready(self) -> bool:
        return self._ready and not self._stopping

    def mark_ready(self) -> None:
        self._ready = True
        self._stopping = False

    def mark_stopping(self) -> None:
        self._ready = False
        self._stopping = True

    def set_component(
        self,
        name: str,
        status: HealthStatus,
        *,
        detail: str | None = None,
    ) -> None:
        self._components[name] = ComponentHealth(
            status=status,
            detail=detail,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )

    def snapshot(self) -> dict[str, Any]:
        if self._stopping:
            status: HealthStatus = "stopping"
        elif not self._ready:
            status = "starting"
        elif any(
            component.status in {"degraded", "down"}
            for component in self._components.values()
        ):
            status = "degraded"
        else:
            status = "ok"
        return {
            "status": status,
            "ready": self.ready,
            "uptime_seconds": round(max(0.0, self._clock() - self._started_at), 3),
            "components": {
                name: asdict(component)
                for name, component in sorted(self._components.items())
            },
        }
