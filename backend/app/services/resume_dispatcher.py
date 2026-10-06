from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from typing import Protocol

from app.core.config import Settings, get_settings

RESUME_PARSE_TASK_NAME = "resume.parse"


class ResumeDispatchError(RuntimeError):
    """Controlled queue publication failure without broker details."""


class ResumeParseDispatcher(Protocol):
    async def dispatch(self, resume_id: uuid.UUID, revision: int) -> None: ...


class NoOpResumeParseDispatcher:
    """Explicit no-broker mode; uploaded resumes remain recoverable as PENDING."""

    async def dispatch(self, resume_id: uuid.UUID, revision: int) -> None:
        del resume_id, revision


class CeleryResumeParseDispatcher:
    """Publish the stable resume task contract without blocking the event loop."""

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
            raise ResumeDispatchError("Resume parse task publication failed") from error

    async def dispatch(self, resume_id: uuid.UUID, revision: int) -> None:
        await asyncio.to_thread(
            self._publisher,
            RESUME_PARSE_TASK_NAME,
            [str(resume_id), revision],
        )


def build_resume_parse_dispatcher(settings: Settings) -> ResumeParseDispatcher:
    if settings.celery_broker_url is None:
        return NoOpResumeParseDispatcher()
    return CeleryResumeParseDispatcher()


def get_resume_parse_dispatcher() -> ResumeParseDispatcher:
    return build_resume_parse_dispatcher(get_settings())
