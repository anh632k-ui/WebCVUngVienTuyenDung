from __future__ import annotations

import asyncio
import os
import threading
import uuid
from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.ai.vector_embedding import BgeM3EmbeddingProvider
from app.core.config import Settings, get_settings
from app.core.engine_factory import create_engine
from app.storage.resume_storage import LocalResumeStorage, ResumeStorage
from app.workers.resume_parse_worker import ResumeParseTaskOutcome, process_resume_parse_task


class WorkerRuntimeConfigurationError(RuntimeError):
    """A safe worker startup error that contains no connection credentials."""


class ResumeParseWorkerRuntime:
    """Persistent async resources owned by exactly one Celery child process."""

    def __init__(
        self,
        settings: Settings,
        *,
        database_engine: AsyncEngine | None = None,
        storage: ResumeStorage | None = None,
        embedding_provider: BgeM3EmbeddingProvider | None = None,
    ) -> None:
        engine = database_engine if database_engine is not None else create_engine(settings)
        if engine is None:
            raise WorkerRuntimeConfigurationError("Resume parse worker database is not configured")
        self._engine = engine
        self._session_factory: Callable[[], AsyncSession] = async_sessionmaker(
            engine,
            expire_on_commit=False,
        )
        self._storage = storage or LocalResumeStorage(settings.resume_storage_root)
        self._embedding_provider = embedding_provider or BgeM3EmbeddingProvider()
        self._runner = asyncio.Runner()
        self._runner_lock = threading.Lock()
        self._closed = False

    def run(self, resume_id: uuid.UUID, expected_revision: int) -> ResumeParseTaskOutcome:
        with self._runner_lock:
            if self._closed:
                raise RuntimeError("Resume parse worker runtime is closed")
            return self._runner.run(
                process_resume_parse_task(
                    resume_id,
                    expected_revision,
                    session_factory=self._session_factory,
                    storage=self._storage,
                    embedding_provider=self._embedding_provider,
                )
            )

    def close(self) -> None:
        with self._runner_lock:
            if self._closed:
                return
            try:
                self._runner.run(self._engine.dispose())
            finally:
                self._runner.close()
                self._closed = True


RuntimeFactory = Callable[[Settings], ResumeParseWorkerRuntime]

_runtime_lock = threading.Lock()
_runtime: ResumeParseWorkerRuntime | None = None
_runtime_pid: int | None = None


def get_worker_runtime(
    *,
    settings: Settings | None = None,
    runtime_factory: RuntimeFactory = ResumeParseWorkerRuntime,
) -> ResumeParseWorkerRuntime:
    """Create resources lazily after fork and reuse them within the current child."""

    global _runtime, _runtime_pid
    current_pid = os.getpid()
    with _runtime_lock:
        if _runtime is None or _runtime_pid != current_pid:
            _runtime = runtime_factory(settings or get_settings())
            _runtime_pid = current_pid
        return _runtime


def shutdown_worker_runtime() -> None:
    global _runtime, _runtime_pid
    current_pid = os.getpid()
    with _runtime_lock:
        runtime = _runtime if _runtime_pid == current_pid else None
        _runtime = None
        _runtime_pid = None
    if runtime is not None:
        runtime.close()
