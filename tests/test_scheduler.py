from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.scheduler.jobs import reconcile_stale_submissions


@pytest.mark.asyncio
async def test_periodic_reconciliation_recovers_post_restart_submission() -> None:
    repository = SimpleNamespace(
        reconcile_incomplete_finalizations=AsyncMock(return_value=(0, 1))
    )
    context = SimpleNamespace(
        settings=SimpleNamespace(hh_submission_recovery_seconds=300),
        hh_application_repository=repository,
    )

    await reconcile_stale_submissions(context)  # type: ignore[arg-type]

    call = repository.reconcile_incomplete_finalizations.await_args
    assert call is not None
    assert "stale_before" in call.kwargs
