from __future__ import annotations

import uuid
from typing import Any

from app.services.job_dispatcher import JOB_PARSE_TASK_NAME
from app.services.match_dispatcher import MATCH_TASK_NAME
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


def execute_match_task(
    match_id: str,
    expected_generation: int,
    expected_resume_revision: int,
    expected_job_revision: int,
    algorithm_version: str,
) -> str:
    """Validate the five-field Match payload before initializing any child resources."""

    try:
        parsed_match_id = uuid.UUID(match_id)
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("match_id must be a valid UUID") from error
    for name, value in (
        ("expected_generation", expected_generation),
        ("expected_resume_revision", expected_resume_revision),
        ("expected_job_revision", expected_job_revision),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 2**63 - 1:
            raise ValueError(f"{name} must be a positive bigint")
    if (
        not isinstance(algorithm_version, str)
        or not algorithm_version.strip()
        or len(algorithm_version) > 50
    ):
        raise ValueError("algorithm_version must be a nonblank string of at most 50 characters")
    return (
        get_worker_runtime()
        .run_match(
            parsed_match_id,
            expected_generation,
            expected_resume_revision,
            expected_job_revision,
            algorithm_version,
        )
        .value
    )


def register_resume_parse_task(celery_app: Any) -> Any:
    """Register the canonical task name without importing Celery in ordinary API startup."""

    return celery_app.task(name=RESUME_PARSE_TASK_NAME, ignore_result=True)(
        execute_resume_parse_task
    )


def register_job_parse_task(celery_app: Any) -> Any:
    """Register the canonical Job task name without eager queue/runtime initialization."""

    return celery_app.task(name=JOB_PARSE_TASK_NAME, ignore_result=True)(execute_job_parse_task)


def register_match_task(celery_app: Any) -> Any:
    """Register Match work on the same lazy, PID-owned runtime as parse tasks."""

    return celery_app.task(name=MATCH_TASK_NAME, ignore_result=True)(execute_match_task)
