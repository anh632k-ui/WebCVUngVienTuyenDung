from __future__ import annotations

from typing import Any

import pytest

import main


@pytest.mark.asyncio
async def test_lifespan_starts_and_stops_both_recovery_loops_before_engine_disposal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    resume_task = object()
    job_task = object()

    def start_resume(*args: Any, **kwargs: Any) -> object:
        del args, kwargs
        events.append("start_resume")
        return resume_task

    def start_job(*args: Any, **kwargs: Any) -> object:
        del args, kwargs
        events.append("start_job")
        return job_task

    async def stop_resume(task: object) -> None:
        assert task is resume_task
        events.append("stop_resume")

    async def stop_job(task: object) -> None:
        assert task is job_task
        events.append("stop_job")

    async def dispose() -> None:
        events.append("dispose")

    monkeypatch.setattr(main, "start_resume_recovery", start_resume)
    monkeypatch.setattr(main, "start_job_recovery", start_job)
    monkeypatch.setattr(main, "stop_resume_recovery", stop_resume)
    monkeypatch.setattr(main, "stop_job_recovery", stop_job)
    monkeypatch.setattr(main, "dispose_engine", dispose)

    async with main.lifespan(main.app):
        events.append("running")

    assert events == [
        "start_resume",
        "start_job",
        "running",
        "stop_resume",
        "stop_job",
        "dispose",
    ]
