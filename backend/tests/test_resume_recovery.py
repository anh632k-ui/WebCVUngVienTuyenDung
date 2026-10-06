from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.config import Settings
from app.services import resume_recovery
from app.services.resume_recovery import (
    ResumeRecoveryCandidate,
    recover_pending_resumes,
    run_resume_recovery_loop,
    start_resume_recovery,
    stop_resume_recovery,
)


class RecordingDispatcher:
    def __init__(self, *, fail_ids: set[uuid.UUID] | None = None) -> None:
        self.calls: list[tuple[uuid.UUID, int]] = []
        self.fail_ids = fail_ids or set()

    async def dispatch(self, resume_id: uuid.UUID, revision: int) -> None:
        self.calls.append((resume_id, revision))
        if resume_id in self.fail_ids:
            raise RuntimeError("queue unavailable")


class FakeSelectionSession:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows
        self.closed = False
        self.execute_calls = 0

    async def __aenter__(self) -> FakeSelectionSession:
        return self

    async def __aexit__(self, *args: Any) -> None:
        del args
        self.closed = True

    async def execute(self, statement: Any) -> Any:
        del statement
        self.execute_calls += 1
        return SimpleNamespace(all=lambda: self.rows)


@pytest.mark.asyncio
async def test_recovery_dispatches_after_selection_and_isolates_item_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = ResumeRecoveryCandidate(uuid.uuid4(), 1)
    second = ResumeRecoveryCandidate(uuid.uuid4(), 4)

    async def select(*args: Any, **kwargs: Any) -> tuple[ResumeRecoveryCandidate, ...]:
        del args, kwargs
        return first, second

    monkeypatch.setattr(resume_recovery, "select_resume_recovery_candidates", select)
    dispatcher = RecordingDispatcher(fail_ids={first.resume_id})

    result = await recover_pending_resumes(
        lambda: SimpleNamespace(),
        dispatcher,
        grace_seconds=300,
        batch_size=10,
    )

    assert dispatcher.calls == [(first.resume_id, 1), (second.resume_id, 4)]
    assert result.selected == 2
    assert result.dispatched == 1
    assert result.failed == 1


@pytest.mark.asyncio
async def test_recovery_closes_selection_session_before_queue_publication() -> None:
    resume_id = uuid.uuid4()
    session = FakeSelectionSession([SimpleNamespace(id=resume_id, revision=6)])

    class ClosureCheckingDispatcher:
        async def dispatch(self, dispatched_id: uuid.UUID, revision: int) -> None:
            assert session.closed is True
            assert (dispatched_id, revision) == (resume_id, 6)

    result = await recover_pending_resumes(
        lambda: session,  # type: ignore[arg-type]
        ClosureCheckingDispatcher(),
        grace_seconds=300,
        batch_size=10,
    )

    assert session.execute_calls == 1
    assert result.dispatched == 1


@pytest.mark.asyncio
async def test_loop_sweeps_immediately_survives_failure_and_cancels_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    second_sweep = asyncio.Event()

    async def recover(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        del args, kwargs
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary database outage")
        second_sweep.set()
        return None

    async def no_delay(_: float) -> None:
        await asyncio.sleep(0)

    monkeypatch.setattr(resume_recovery, "recover_pending_resumes", recover)
    task = asyncio.create_task(
        run_resume_recovery_loop(
            lambda: SimpleNamespace(),
            RecordingDispatcher(),
            grace_seconds=1,
            interval_seconds=1,
            batch_size=1,
            sleep=no_delay,
        )
    )
    await asyncio.wait_for(second_sweep.wait(), timeout=1)
    await stop_resume_recovery(task)

    assert calls >= 2
    assert task.cancelled()


def test_recovery_start_is_disabled_without_broker_or_database() -> None:
    assert start_resume_recovery(Settings(_env_file=None), lambda: SimpleNamespace()) is None
    configured = Settings(_env_file=None, celery_broker_url="redis://localhost:6379/0")
    assert start_resume_recovery(configured, None) is None


@pytest.mark.asyncio
async def test_configured_start_schedules_promptly_without_opening_broker_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()

    async def loop(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(resume_recovery, "run_resume_recovery_loop", loop)
    configured = Settings(_env_file=None, celery_broker_url="redis://localhost:6379/0")
    task = start_resume_recovery(configured, lambda: SimpleNamespace())
    assert task is not None
    await asyncio.wait_for(started.wait(), timeout=1)
    await stop_resume_recovery(task)
    assert task.cancelled()
