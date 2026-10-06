from __future__ import annotations

import threading
import uuid

import pytest

from app.core.config import Settings
from app.services.job_dispatcher import (
    JOB_PARSE_TASK_NAME,
    CeleryJobParseDispatcher,
    JobDispatchError,
    NoOpJobParseDispatcher,
    build_job_parse_dispatcher,
)
from app.tasks import celery_app


def test_no_broker_selects_noop_without_importing_celery() -> None:
    dispatcher = build_job_parse_dispatcher(Settings(_env_file=None))

    assert isinstance(dispatcher, NoOpJobParseDispatcher)


def test_configured_broker_selects_celery_dispatcher() -> None:
    settings = Settings(_env_file=None, celery_broker_url="redis://localhost:6379/0")

    assert isinstance(build_job_parse_dispatcher(settings), CeleryJobParseDispatcher)


@pytest.mark.asyncio
async def test_dispatch_uses_exact_task_contract_off_event_loop() -> None:
    caller_thread = threading.get_ident()
    published: list[tuple[str, list[str | int], int]] = []

    def publisher(task_name: str, args: list[str | int]) -> None:
        published.append((task_name, args, threading.get_ident()))

    job_id = uuid.uuid4()
    await CeleryJobParseDispatcher(publisher=publisher).dispatch(job_id, 7)

    assert published == [(JOB_PARSE_TASK_NAME, [str(job_id), 7], published[0][2])]
    assert published[0][2] != caller_thread


@pytest.mark.asyncio
async def test_dispatch_propagates_injected_publication_failure() -> None:
    def publisher(task_name: str, args: list[str | int]) -> None:
        del task_name, args
        raise RuntimeError("broker unavailable")

    with pytest.raises(RuntimeError, match="broker unavailable"):
        await CeleryJobParseDispatcher(publisher=publisher).dispatch(uuid.uuid4(), 1)


@pytest.mark.asyncio
async def test_default_publisher_wraps_failure_without_exposing_broker_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "redis://queue-user:never-log-me@localhost:6379/0"

    class FailingApplication:
        def send_task(self, *args: object, **kwargs: object) -> None:
            del args, kwargs
            raise RuntimeError(secret)

    monkeypatch.setattr(celery_app, "get_celery_app", lambda: FailingApplication())

    with pytest.raises(JobDispatchError) as caught:
        await CeleryJobParseDispatcher().dispatch(uuid.uuid4(), 1)

    assert "never-log-me" not in str(caught.value)
