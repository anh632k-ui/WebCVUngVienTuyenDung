from __future__ import annotations

import uuid
from typing import Any

from app.services.job_dispatcher import JOB_PARSE_TASK_NAME
from app.services.resume_dispatcher import RESUME_PARSE_TASK_NAME
from app.tasks.worker_runtime import get_worker_runtime


def _validated_task_payload(
    resource_name: str, resource_id: str, expected_revision: int
) -> uuid.UUID:
    try:
        parsed_id = uuid.UUID(resource_id)
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError(f"{resource_name}_id must be a valid UUID") from error
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
        raise ValueError("expected_revision must be a positive integer")
    if expected_revision < 1:
        raise ValueError("expected_revision must be a positive integer")
    return parsed_id


def execute_resume_parse_task(resume_id: str, expected_revision: int) -> str:
    """Validate the Resume queue payload and delegate to the shared CAS runtime."""

    parsed_resume_id = _validated_task_payload("resume", resume_id, expected_revision)
    return get_worker_runtime().run_resume(parsed_resume_id, expected_revision).value


def execute_job_parse_task(job_id: str, expected_revision: int) -> str:
    """Validate the Job queue payload and delegate to the shared CAS runtime."""

    parsed_job_id = _validated_task_payload("job", job_id, expected_revision)
    return get_worker_runtime().run_job(parsed_job_id, expected_revision).value


def register_resume_parse_task(celery_app: Any) -> Any:
    """Register the canonical task name without importing Celery in ordinary API startup."""

    return celery_app.task(name=RESUME_PARSE_TASK_NAME, ignore_result=True)(
        execute_resume_parse_task
    )


def register_job_parse_task(celery_app: Any) -> Any:
    """Register the canonical Job task name without eager queue/runtime initialization."""

    return celery_app.task(name=JOB_PARSE_TASK_NAME, ignore_result=True)(execute_job_parse_task)
