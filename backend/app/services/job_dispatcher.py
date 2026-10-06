from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from typing import Protocol

from app.core.config import Settings, get_settings

JOB_PARSE_TASK_NAME = "job.parse"


class JobDispatchError(RuntimeError):
    """Controlled queue publication failure without broker details."""


class JobParseDispatcher(Protocol):
    async def dispatch(self, job_id: uuid.UUID, revision: int) -> None: ...


class NoOpJobParseDispatcher:
    """Explicit no-broker mode; created Jobs remain recoverable as PENDING."""

    async def dispatch(self, job_id: uuid.UUID, revision: int) -> None:
        del job_id, revision


class CeleryJobParseDispatcher:
    """Publish the stable Job task contract without blocking the event loop."""

    def __init__(
        self,
        *,
        publisher: Callable[[str, list[str | int]], None] | None = None,
    ) -> None:
        self._publisher = publisher or self._publish_with_celery

    @staticmethod
    def _publish_with_celery(task_name: str, args: list[str | int]) -> None:
        try:
            from app.tasks.celery_app import get_celery_app

            celery_app = get_celery_app()
            celery_app.send_task(task_name, args=args, retry=False)
        except Exception as error:
            raise JobDispatchError("Job parse task publication failed") from error

    async def dispatch(self, job_id: uuid.UUID, revision: int) -> None:
        await asyncio.to_thread(
            self._publisher,
            JOB_PARSE_TASK_NAME,
            [str(job_id), revision],
        )


def build_job_parse_dispatcher(settings: Settings) -> JobParseDispatcher:
    if settings.celery_broker_url is None:
        return NoOpJobParseDispatcher()
    return CeleryJobParseDispatcher()


def get_job_parse_dispatcher() -> JobParseDispatcher:
    return build_job_parse_dispatcher(get_settings())
