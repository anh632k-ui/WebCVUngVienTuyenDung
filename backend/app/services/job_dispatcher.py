from __future__ import annotations

import uuid
from typing import Protocol


class JobParseDispatcher(Protocol):
    async def dispatch(self, job_id: uuid.UUID, revision: int) -> None: ...


class NoOpJobParseDispatcher:
    """Explicit seam until the canonical Job parser worker is implemented."""

    async def dispatch(self, job_id: uuid.UUID, revision: int) -> None:
        del job_id, revision


def get_job_parse_dispatcher() -> JobParseDispatcher:
    return NoOpJobParseDispatcher()
