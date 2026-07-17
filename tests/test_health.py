from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.health import HealthRegistry
from app.services.hh_oauth_callback import ApplicationHTTPServer


def test_health_registry_reports_degraded_component_without_losing_readiness() -> None:
    now = [100.0]
    registry = HealthRegistry(clock=lambda: now[0])
    registry.set_component("database", "ok")
    registry.set_component("telegram", "degraded", detail="route=proxy-1")
    registry.mark_ready()
    now[0] = 112.5

    snapshot = registry.snapshot()

    assert snapshot["status"] == "degraded"
    assert snapshot["ready"] is True
    assert snapshot["uptime_seconds"] == 12.5


@pytest.mark.asyncio
async def test_health_endpoints_distinguish_live_and_ready() -> None:
    registry = HealthRegistry()
    server = ApplicationHTTPServer(
        settings=SimpleNamespace(),  # type: ignore[arg-type]
        oauth_service=SimpleNamespace(),  # type: ignore[arg-type]
        bot=SimpleNamespace(),  # type: ignore[arg-type]
        health=registry,
    )

    live = await server._live(SimpleNamespace())  # type: ignore[arg-type]
    pending = await server._ready(SimpleNamespace())  # type: ignore[arg-type]
    registry.mark_ready()
    ready = await server._ready(SimpleNamespace())  # type: ignore[arg-type]

    assert live.status == 200
    assert pending.status == 503
    assert ready.status == 200
    assert json.loads(ready.text)["ready"] is True
