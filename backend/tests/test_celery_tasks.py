from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.config import Settings
from app.services.resume_dispatcher import RESUME_PARSE_TASK_NAME
from app.tasks import celery_tasks, worker_runtime
from app.tasks.celery_app import QueueRuntimeUnavailable, create_celery_app
from app.workers.resume_parse_worker import ResumeParseTaskOutcome


class FakeRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[uuid.UUID, int]] = []
        self.closed = False

    def run(self, resume_id: uuid.UUID, revision: int) -> ResumeParseTaskOutcome:
        self.calls.append((resume_id, revision))
        return ResumeParseTaskOutcome.PARSED

    def close(self) -> None:
        self.closed = True


class FakeSignal:
    def __init__(self) -> None:
        self.connections: list[tuple[Any, bool, str]] = []

    def connect(self, callback: Any, *, weak: bool, dispatch_uid: str) -> None:
        self.connections.append((callback, weak, dispatch_uid))


class FakeCelery:
    def __init__(self, name: str, *, broker: str) -> None:
        self.name = name
        self.broker = broker
        self.conf = SimpleNamespace(update=self._update)
        self.configuration: dict[str, Any] = {}
        self.registered: dict[str, tuple[Any, bool]] = {}

    def _update(self, **values: Any) -> None:
        self.configuration.update(values)

    def task(self, *, name: str, ignore_result: bool) -> Any:
        def decorator(function: Any) -> Any:
            self.registered[name] = (function, ignore_result)
            return function

        return decorator


def test_celery_imports_do_not_create_engine_before_child_runtime() -> None:
    script = textwrap.dedent(
        """
        import importlib
        import os
        import sys
        import types

        import sqlalchemy.ext.asyncio as sqlalchemy_asyncio

        engine_creations = []

        class FakeEngine:
            async def dispose(self):
                return None

        def recording_create_async_engine(*args, **kwargs):
            engine_creations.append((args, kwargs))
            return FakeEngine()

        class FakeConfiguration:
            def update(self, **kwargs):
                return None

        class FakeCelery:
            def __init__(self, *args, **kwargs):
                self.conf = FakeConfiguration()

            def task(self, **kwargs):
                return lambda function: function

        class FakeSignal:
            def connect(self, *args, **kwargs):
                return None

        celery_module = types.ModuleType("celery")
        celery_module.Celery = FakeCelery
        signals_module = types.ModuleType("celery.signals")
        signals_module.worker_process_shutdown = FakeSignal()
        signals_module.worker_shutdown = FakeSignal()
        sys.modules["celery"] = celery_module
        sys.modules["celery.signals"] = signals_module

        os.environ["DATABASE_URL"] = (
            "postgresql+asyncpg://worker:placeholder@localhost:5432/webcv_ungvien"
        )
        os.environ["CELERY_BROKER_URL"] = "redis://localhost:6379/0"
        sqlalchemy_asyncio.create_async_engine = recording_create_async_engine

        for module_name in (
            "app.tasks.celery_app",
            "app.tasks.celery_tasks",
            "app.tasks.worker_runtime",
            "app.tasks.celery_worker",
        ):
            importlib.import_module(module_name)

        assert engine_creations == []
        assert "app.core.database" not in sys.modules

        from app.core.config import Settings
        from app.tasks import worker_runtime

        worker_runtime.async_sessionmaker = lambda *args, **kwargs: object()
        runtime = worker_runtime.ResumeParseWorkerRuntime(
            Settings(_env_file=None),
            storage=object(),
            embedding_provider=object(),
        )
        assert len(engine_creations) == 1
        runtime.close()
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_task_validates_payload_and_delegates_to_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = FakeRuntime()
    monkeypatch.setattr(celery_tasks, "get_worker_runtime", lambda: runtime)
    resume_id = uuid.uuid4()

    result = celery_tasks.execute_resume_parse_task(str(resume_id), 3)

    assert result == "PARSED"
    assert runtime.calls == [(resume_id, 3)]


@pytest.mark.parametrize(
    ("resume_id", "revision"),
    [("invalid", 1), (str(uuid.uuid4()), 0), (str(uuid.uuid4()), True), (str(uuid.uuid4()), "1")],
)
def test_task_rejects_invalid_payload(resume_id: str, revision: Any) -> None:
    with pytest.raises(ValueError):
        celery_tasks.execute_resume_parse_task(resume_id, revision)


def test_celery_app_requires_broker_without_importing_celery() -> None:
    with pytest.raises(QueueRuntimeUnavailable, match="CELERY_BROKER_URL"):
        create_celery_app(Settings(_env_file=None))


def test_celery_app_uses_safe_json_no_result_configuration() -> None:
    signal = FakeSignal()
    settings = Settings(
        _env_file=None,
        celery_broker_url="redis://queue-user:secret@localhost:6379/0",
    )

    application = create_celery_app(
        settings,
        celery_factory=FakeCelery,
        shutdown_signal=signal,
    )

    assert application.name == "webcv_backend"
    assert application.configuration["task_serializer"] == "json"
    assert application.configuration["accept_content"] == ["json"]
    assert application.configuration["result_backend"] is None
    assert application.configuration["task_ignore_result"] is True
    assert application.configuration["task_publish_retry"] is False
    assert application.configuration["broker_connection_retry_on_startup"] is False
    assert application.configuration["enable_utc"] is True
    assert application.registered[RESUME_PARSE_TASK_NAME][1] is True
    assert len(signal.connections) == 1


def test_runtime_singleton_is_pid_aware_and_shutdown_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker_runtime, "_runtime", None)
    monkeypatch.setattr(worker_runtime, "_runtime_pid", None)
    current_pid = os.getpid()
    monkeypatch.setattr(worker_runtime.os, "getpid", lambda: current_pid)
    created: list[FakeRuntime] = []

    def factory(settings: Settings) -> Any:
        del settings
        runtime = FakeRuntime()
        created.append(runtime)
        return runtime

    settings = Settings(_env_file=None)
    first = worker_runtime.get_worker_runtime(settings=settings, runtime_factory=factory)
    second = worker_runtime.get_worker_runtime(settings=settings, runtime_factory=factory)
    assert first is second
    assert len(created) == 1

    current_pid += 1
    third = worker_runtime.get_worker_runtime(settings=settings, runtime_factory=factory)
    assert third is not first
    assert len(created) == 2

    worker_runtime.shutdown_worker_runtime()
    worker_runtime.shutdown_worker_runtime()
    assert created[1].closed is True
    assert created[0].closed is False


def test_worker_runtime_reuses_one_async_runner_and_disposes_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop_ids: list[int] = []

    class FakeEngine:
        disposed = False

        async def dispose(self) -> None:
            loop_ids.append(id(asyncio.get_running_loop()))
            self.disposed = True

    async def process(*args: Any, **kwargs: Any) -> ResumeParseTaskOutcome:
        del args, kwargs
        loop_ids.append(id(asyncio.get_running_loop()))
        return ResumeParseTaskOutcome.DISCARDED

    engine = FakeEngine()
    monkeypatch.setattr(worker_runtime, "process_resume_parse_task", process)
    monkeypatch.setattr(worker_runtime, "async_sessionmaker", lambda *args, **kwargs: object())
    runtime = worker_runtime.ResumeParseWorkerRuntime(
        Settings(_env_file=None),
        database_engine=engine,  # type: ignore[arg-type]
        storage=object(),  # type: ignore[arg-type]
        embedding_provider=object(),  # type: ignore[arg-type]
    )

    assert runtime.run(uuid.uuid4(), 1) == ResumeParseTaskOutcome.DISCARDED
    assert runtime.run(uuid.uuid4(), 2) == ResumeParseTaskOutcome.DISCARDED
    runtime.close()
    runtime.close()

    assert len(set(loop_ids)) == 1
    assert engine.disposed is True
