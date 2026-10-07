from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from typing import Protocol

from app.core.config import Settings, get_settings

MATCH_TASK_NAME = "match.calculate"


class MatchDispatchError(RuntimeError):
    """Controlled queue publication failure without broker details."""


class MatchDispatcher(Protocol):
    async def dispatch(
        self,
        match_id: uuid.UUID,
        expected_generation: int,
        expected_resume_revision: int,
        expected_job_revision: int,
        algorithm_version: str,
    ) -> None: ...


class NoOpMatchDispatcher:
    """Explicit no-broker mode; prepared Matches remain PENDING."""

    async def dispatch(
        self,
        match_id: uuid.UUID,
        expected_generation: int,
        expected_resume_revision: int,
        expected_job_revision: int,
        algorithm_version: str,
    ) -> None:
        del match_id, expected_generation, expected_resume_revision, expected_job_revision
        del algorithm_version


class CeleryMatchDispatcher:
    """Publish the immutable Match task snapshots without blocking the event loop."""

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
            raise MatchDispatchError("Match task publication failed") from error

    async def dispatch(
        self,
        match_id: uuid.UUID,
        expected_generation: int,
        expected_resume_revision: int,
        expected_job_revision: int,
        algorithm_version: str,
    ) -> None:
        await asyncio.to_thread(
            self._publisher,
            MATCH_TASK_NAME,
            [
                str(match_id),
                expected_generation,
                expected_resume_revision,
                expected_job_revision,
                algorithm_version,
            ],
        )


def build_match_dispatcher(settings: Settings) -> MatchDispatcher:
    if settings.celery_broker_url is None:
        return NoOpMatchDispatcher()
    return CeleryMatchDispatcher()


def get_match_dispatcher() -> MatchDispatcher:
    return build_match_dispatcher(get_settings())
