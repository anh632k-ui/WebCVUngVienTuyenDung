from __future__ import annotations

import uuid
from typing import Protocol


class ResumeParseDispatcher(Protocol):
    async def dispatch(self, resume_id: uuid.UUID, revision: int) -> None: ...


class NoOpResumeParseDispatcher:
    """Explicit seam for a future parser queue; intentionally dispatches no work yet."""

    async def dispatch(self, resume_id: uuid.UUID, revision: int) -> None:
        del resume_id, revision


def get_resume_parse_dispatcher() -> ResumeParseDispatcher:
    return NoOpResumeParseDispatcher()
