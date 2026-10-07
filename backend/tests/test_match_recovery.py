from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.config import Settings
from app.services import match_recovery
from app.services.match_recovery import (
    MatchRecoveryCandidate,
    recover_pending_matches,
    run_match_recovery_loop,
    start_match_recovery,
    stop_match_recovery,
)


class RecordingDispatcher:
    def __init__(self, *, fail_id: uuid.UUID | None = None) -> None:
        self.calls: list[tuple[uuid.UUID, int, int, int, str]] = []
        self.fail_id = fail_id

    async def dispatch(
        self,
        match_id: uuid.UUID,
        expected_generation: int,
        expected_resume_revision: int,
        expected_job_revision: int,
        algorithm_version: str,
    ) -> None:
        self.calls.append(
            (
                match_id,
                expected_generation,
                expected_resume_revision,
                expected_job_revision,
                algorithm_version,
            )
        )
        if match_id == self.fail_id:
            raise RuntimeError("redis://user:never-log-me@localhost:6379/0")


@pytest.mark.asyncio
async def test_recovery_uses_grace_cutoff_and_exact_snapshots_despite_item_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    first = MatchRecoveryCandidate(uuid.uuid4(), 7, 2, 5, "hybrid-v1")
    second = MatchRecoveryCandidate(uuid.uuid4(), 9, 3, 6, "hybrid-v1")
    now = datetime(2026, 10, 7, tzinfo=UTC)

    async def select(*args: Any, **kwargs: Any) -> tuple[MatchRecoveryCandidate, ...]:
        assert kwargs == {"cutoff": now - timedelta(seconds=300), "batch_size": 10}
        return first, second

    monkeypatch.setattr(match_recovery, "select_match_recovery_candidates", select)
    dispatcher = RecordingDispatcher(fail_id=first.match_id)
    result = await recover_pending_matches(
        lambda: SimpleNamespace(), dispatcher, grace_seconds=300, batch_size=10, now=now
    )

    assert dispatcher.calls == [
        (first.match_id, 7, 2, 5, "hybrid-v1"),
        (second.match_id, 9, 3, 6, "hybrid-v1"),
    ]
    assert (result.selected, result.dispatched, result.failed) == (2, 1, 1)
    assert "match_recovery_dispatch_failed" in caplog.text
    assert "never-log-me" not in caplog.text


@pytest.mark.asyncio
async def test_recovery_closes_selection_session_before_publication() -> None:
    match_id = uuid.uuid4()

    class SelectionSession:
        closed = False

        async def __aenter__(self) -> SelectionSession:
            return self

        async def __aexit__(self, *args: Any) -> None:
            self.closed = True

        async def execute(self, statement: Any) -> Any:
            return SimpleNamespace(
                all=lambda: [
                    SimpleNamespace(
                        id=match_id,
                        generation=8,
                        resume_revision=2,
                        job_revision=3,
                        algorithm_version="hybrid-v1",
                    )
                ]
            )

    session = SelectionSession()

    class ClosureCheckingDispatcher:
        async def dispatch(self, *args: Any) -> None:
            assert session.closed
            assert args == (match_id, 8, 2, 3, "hybrid-v1")

    result = await recover_pending_matches(
        lambda: session,  # type: ignore[arg-type]
        ClosureCheckingDispatcher(),
        grace_seconds=300,
        batch_size=10,
    )
    assert result.dispatched == 1


@pytest.mark.asyncio
async def test_loop_recovers_after_database_error_without_logging_details_and_cancels(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls = 0
    recovered = asyncio.Event()

    async def recover(*args: Any, **kwargs: Any) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("database password never-log-me")
        recovered.set()

    async def no_delay(_: float) -> None:
        await asyncio.sleep(0)

    monkeypatch.setattr(match_recovery, "recover_pending_matches", recover)
    task = asyncio.create_task(
        run_match_recovery_loop(
            lambda: SimpleNamespace(),
            RecordingDispatcher(),
            grace_seconds=1,
            interval_seconds=1,
            batch_size=1,
            sleep=no_delay,
        )
    )
    try:
        await asyncio.wait_for(recovered.wait(), timeout=1)
    finally:
        await stop_match_recovery(task)
    assert calls >= 2
    assert task.cancelled()
    assert "never-log-me" not in caplog.text


def test_recovery_is_disabled_without_broker_or_database() -> None:
    assert start_match_recovery(Settings(_env_file=None), lambda: SimpleNamespace()) is None
    configured = Settings(_env_file=None, celery_broker_url="redis://localhost:6379/0")
    assert start_match_recovery(configured, None) is None


@pytest.mark.asyncio
async def test_configured_start_passes_settings_and_stops_without_connecting_broker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()

    async def loop(*args: Any, **kwargs: Any) -> None:
        assert kwargs == {"grace_seconds": 12, "interval_seconds": 13, "batch_size": 14}
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(match_recovery, "run_match_recovery_loop", loop)
    configured = Settings(
        _env_file=None,
        celery_broker_url="redis://localhost:6379/0",
        match_recovery_grace_seconds=12,
        match_recovery_interval_seconds=13,
        match_recovery_batch_size=14,
    )
    task = start_match_recovery(configured, lambda: SimpleNamespace())
    assert task is not None
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
    finally:
        await stop_match_recovery(task)
    assert task.cancelled()
    await stop_match_recovery(None)
