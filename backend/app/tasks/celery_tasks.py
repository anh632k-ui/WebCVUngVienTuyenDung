from __future__ import annotations

import uuid
from typing import Any

from app.services.resume_dispatcher import RESUME_PARSE_TASK_NAME
from app.tasks.worker_runtime import get_worker_runtime


def execute_resume_parse_task(resume_id: str, expected_revision: int) -> str:
    """Validate the small queue payload and delegate all business work to the CAS worker."""

    try:
        parsed_resume_id = uuid.UUID(resume_id)
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("resume_id must be a valid UUID") from error
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
        raise ValueError("expected_revision must be a positive integer")
    if expected_revision < 1:
        raise ValueError("expected_revision must be a positive integer")
    return get_worker_runtime().run(parsed_resume_id, expected_revision).value


def register_resume_parse_task(celery_app: Any) -> Any:
    """Register the canonical task name without importing Celery in ordinary API startup."""

    return celery_app.task(name=RESUME_PARSE_TASK_NAME, ignore_result=True)(
        execute_resume_parse_task
    )
