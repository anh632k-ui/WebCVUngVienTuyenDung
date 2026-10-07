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
from app.workers.job_parse_worker import JobParseTaskOutcome, process_job_parse_task
from app.workers.match_worker import MatchTaskOutcome, process_match_task
from app.workers.resume_parse_worker import ResumeParseTaskOutcome, process_resume_parse_task


class WorkerRuntimeConfigurationError(RuntimeError):
    """A safe worker startup error that contains no connection credentials."""


class ParseWorkerRuntime:
    """Shared parse/Match resources owned by exactly one Celery child process."""

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
            raise WorkerRuntimeConfigurationError("Parse worker database is not configured")
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

    def run_resume(self, resume_id: uuid.UUID, expected_revision: int) -> ResumeParseTaskOutcome:
        with self._runner_lock:
            if self._closed:
                raise RuntimeError("Parse worker runtime is closed")
            return self._runner.run(
                process_resume_parse_task(
                    resume_id,
                    expected_revision,
                    session_factory=self._session_factory,
                    storage=self._storage,
                    embedding_provider=self._embedding_provider,
                )
            )

    def run_job(self, job_id: uuid.UUID, expected_revision: int) -> JobParseTaskOutcome:
        with self._runner_lock:
            if self._closed:
                raise RuntimeError("Parse worker runtime is closed")
            return self._runner.run(
                process_job_parse_task(
                    job_id,
                    expected_revision,
                    session_factory=self._session_factory,
                    embedding_provider=self._embedding_provider,
                )
            )

    def run(self, resume_id: uuid.UUID, expected_revision: int) -> ResumeParseTaskOutcome:
        """Backward-compatible Resume task entry point."""

        return self.run_resume(resume_id, expected_revision)

    def run_match(
        self,
        match_id: uuid.UUID,
        expected_generation: int,
        expected_resume_revision: int,
        expected_job_revision: int,
        algorithm_version: str,
    ) -> MatchTaskOutcome:
        with self._runner_lock:
            if self._closed:
                raise RuntimeError("Match worker runtime is closed")
            return self._runner.run(
                process_match_task(
                    match_id,
                    expected_generation,
                    expected_resume_revision,
                    expected_job_revision,
                    algorithm_version,
                    session_factory=self._session_factory,
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


ResumeParseWorkerRuntime = ParseWorkerRuntime

RuntimeFactory = Callable[[Settings], ParseWorkerRuntime]

_runtime_lock = threading.Lock()
_runtime: ParseWorkerRuntime | None = None
_runtime_pid: int | None = None


def get_worker_runtime(
    *,
    settings: Settings | None = None,
    runtime_factory: RuntimeFactory = ParseWorkerRuntime,
) -> ParseWorkerRuntime:
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
