from __future__ import annotations

import threading
import uuid
from typing import Any

import pytest

from app.core.config import Settings
from app.services.match_dispatcher import (
    MATCH_TASK_NAME,
    CeleryMatchDispatcher,
    MatchDispatchError,
    NoOpMatchDispatcher,
    build_match_dispatcher,
)
from app.tasks import celery_app


def test_dispatcher_selection_keeps_queue_optional() -> None:
    assert isinstance(build_match_dispatcher(Settings(_env_file=None)), NoOpMatchDispatcher)
    configured = Settings(_env_file=None, celery_broker_url="redis://localhost:6379/0")
    assert isinstance(build_match_dispatcher(configured), CeleryMatchDispatcher)


@pytest.mark.asyncio
async def test_dispatch_preserves_all_five_fields_off_event_loop() -> None:
    published: list[tuple[str, list[str | int], int]] = []
    caller_thread = threading.get_ident()

    def publisher(task_name: str, args: list[str | int]) -> None:
        published.append((task_name, args, threading.get_ident()))

    match_id = uuid.uuid4()
    await CeleryMatchDispatcher(publisher=publisher).dispatch(match_id, 9, 3, 7, "hybrid-v1")

    assert published[0][:2] == (MATCH_TASK_NAME, [str(match_id), 9, 3, 7, "hybrid-v1"])
    assert published[0][2] != caller_thread
    assert len(published) == 1


@pytest.mark.asyncio
async def test_noop_does_not_initialize_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable() -> None:
        raise AssertionError("No-op initialized Celery")

    monkeypatch.setattr(celery_app, "get_celery_app", unavailable)
    await NoOpMatchDispatcher().dispatch(uuid.uuid4(), 1, 2, 3, "hybrid-v1")


@pytest.mark.asyncio
async def test_publisher_uses_json_args_and_disables_implicit_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    class Application:
        def send_task(self, task_name: str, **kwargs: Any) -> None:
            calls.append((task_name, kwargs))

    monkeypatch.setattr(celery_app, "get_celery_app", lambda: Application())
    match_id = uuid.uuid4()
    await CeleryMatchDispatcher().dispatch(match_id, 4, 6, 8, "hybrid-v1")

    assert calls == [
        (MATCH_TASK_NAME, {"args": [str(match_id), 4, 6, 8, "hybrid-v1"], "retry": False})
    ]


@pytest.mark.asyncio
async def test_publication_errors_are_controlled_without_broker_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Application:
        def send_task(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("redis://user:never-log-me@localhost:6379/0")

    monkeypatch.setattr(celery_app, "get_celery_app", lambda: Application())
    with pytest.raises(MatchDispatchError) as caught:
        await CeleryMatchDispatcher().dispatch(uuid.uuid4(), 1, 1, 1, "hybrid-v1")
    assert str(caught.value) == "Match task publication failed"
