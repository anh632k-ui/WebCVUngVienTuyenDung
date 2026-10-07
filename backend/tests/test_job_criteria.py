from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.api.dependencies import get_current_user
from app.api.v1.endpoints import jobs as job_endpoint
from app.core.database import get_db_session
from app.core.exceptions import APIError
from app.models.job import JobDescription
from app.models.skill import JobSkill
from app.models.user import User
from app.schemas.job_schema import JobCriteriaRequest
from app.services.job_service import JobCriteriaRecord
from main import app


def make_user(role: str, index: int) -> User:
    now = datetime.now(UTC)
    return User(
        id=uuid.uuid5(uuid.NAMESPACE_URL, f"criteria-user-{index}"),
        email=f"criteria-{index}@example.com",
        password_hash="not-used",
        full_name=f"Criteria User {index}",
        phone_number=None,
        role=role,
        is_active=True,
        created_at=now,
        updated_at=now,
    )


def make_job(
    owner: User, index: int, parsing_status: str, *, deleted: bool = False
) -> JobDescription:
    now = datetime.now(UTC)
    return JobDescription(
        id=uuid.uuid5(uuid.NAMESPACE_URL, f"criteria-job-{index}"),
        recruiter_id=owner.id,
        title=f"Criteria Job {index}",
        job_level="MID",
        location=None,
        raw_content="Python role",
        create_request_fingerprint=f"{index:x}".zfill(64),
        revision=3,
        min_experience_years=Decimal("2.0"),
        education_requirement="Bachelor",
        job_embedding=[0.0] * 1024 if parsing_status == "PARSED" else None,
        embedding_model="BAAI/bge-m3" if parsing_status == "PARSED" else None,
        embedding_preprocessing_version="semantic-v1" if parsing_status == "PARSED" else None,
        parsing_status=parsing_status,
        parsing_error_message=None,
        is_criteria_verified=False,
        w_skill=Decimal("0.500"),
        w_semantic=Decimal("0.300"),
        w_experience=Decimal("0.200"),
        status="DRAFT",
        is_deleted=deleted,
        created_at=now,
        updated_at=now,
        parsed_at=now if parsing_status == "PARSED" else None,
        deleted_at=now if deleted else None,
    )


def make_skill(job: JobDescription, skill_id: int) -> JobSkill:
    return JobSkill(
        id=skill_id,
        job_id=job.id,
        skill_id=skill_id,
        importance="MANDATORY",
        min_years_required=Decimal("1.0"),
    )


@dataclass
class CriteriaAPIContext:
    client: AsyncClient
    actor: User
    owner: User
    other_hr: User
    jobs: dict[str, JobDescription]
    skills: dict[uuid.UUID, list[JobSkill]]


@pytest_asyncio.fixture
async def criteria_api(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[CriteriaAPIContext]:
    owner = make_user("HR", 1)
    other_hr = make_user("HR", 2)
    actor = make_user("CANDIDATE", 3)
    jobs = {
        "parsed": make_job(owner, 1, "PARSED"),
        "pending": make_job(owner, 2, "PENDING"),
        "other": make_job(other_hr, 3, "PARSED"),
        "deleted": make_job(owner, 4, "PARSED", deleted=True),
    }
    skills = {
        jobs["parsed"].id: [make_skill(jobs["parsed"], 2), make_skill(jobs["parsed"], 1)],
        jobs["pending"].id: [],
        jobs["other"].id: [make_skill(jobs["other"], 3)],
        jobs["deleted"].id: [make_skill(jobs["deleted"], 4)],
    }

    async def override_user() -> User:
        return actor

    async def override_session() -> AsyncIterator[object]:
        yield object()

    def visible(current_user: User, job_id: uuid.UUID) -> JobDescription:
        job = next(
            (item for item in jobs.values() if item.id == job_id and not item.is_deleted),
            None,
        )
        if job is None or (current_user.role == "HR" and job.recruiter_id != current_user.id):
            raise APIError(404, "JOB_NOT_FOUND", "Job not found")
        return job

    async def fake_get(_: Any, *, current_user: User, job_id: uuid.UUID) -> JobCriteriaRecord:
        job = visible(current_user, job_id)
        ordered = tuple(sorted(skills[job.id], key=lambda item: (item.skill_id, item.id)))
        return JobCriteriaRecord(job=job, skills=ordered)

    async def fake_update(
        _: Any,
        *,
        current_user: User,
        job_id: uuid.UUID,
        payload: JobCriteriaRequest,
    ) -> JobCriteriaRecord:
        job = visible(current_user, job_id)
        if job.parsing_status != "PARSED":
            raise APIError(
                422, "JOB_NOT_READY", "Job must be PARSED before criteria can be reviewed"
            )
        if any(item.skill_id == 999999 for item in payload.skills):
            raise APIError(
                422,
                "INVALID_JOB_CRITERIA",
                "One or more criteria skills are not in the canonical taxonomy",
            )
        if "min_experience_years" in payload.model_fields_set:
            job.min_experience_years = payload.min_experience_years
        if "education_requirement" in payload.model_fields_set:
            job.education_requirement = payload.education_requirement
        job.revision += 1
        job.is_criteria_verified = True
        skills[job.id] = [
            JobSkill(
                id=index,
                job_id=job.id,
                skill_id=item.skill_id,
                importance=item.importance.value,
                min_years_required=item.min_years_required,
            )
            for index, item in enumerate(payload.skills, start=10)
        ]
        return await fake_get(object(), current_user=current_user, job_id=job_id)

    monkeypatch.setattr(job_endpoint, "get_job_criteria", fake_get)
    monkeypatch.setattr(job_endpoint, "update_job_criteria", fake_update)
    app.dependency_overrides[get_current_user] = override_user
    app.dependency_overrides[get_db_session] = override_session
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield CriteriaAPIContext(client, actor, owner, other_hr, jobs, skills)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)


@pytest.mark.parametrize(
    "payload",
    [
        {"skills": []},
        {
            "skills": [
                {"skill_id": 1, "importance": "MANDATORY"},
                {"skill_id": 1, "importance": "OPTIONAL"},
            ]
        },
        {"skills": [{"skill_id": 1, "importance": "REQUIRED"}]},
        {"skills": [{"skill_id": 1, "importance": "MANDATORY", "min_years_required": -1}]},
        {"min_experience_years": -1, "skills": [{"skill_id": 1, "importance": "MANDATORY"}]},
        {"min_experience_years": 1000, "skills": [{"skill_id": 1, "importance": "MANDATORY"}]},
        {"skills": [{"skill_id": 1, "importance": "MANDATORY", "min_years_required": 999.99}]},
    ],
    ids=[
        "empty-skills",
        "duplicate-skill-id",
        "importance",
        "negative-skill-years",
        "negative-job-years",
        "numeric-overflow",
        "numeric-precision",
    ],
)
def test_criteria_schema_rejects_invalid_payloads(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        JobCriteriaRequest.model_validate(payload)


def test_criteria_schema_tracks_omitted_and_explicit_null_fields() -> None:
    omitted = JobCriteriaRequest.model_validate(
        {"skills": [{"skill_id": 1, "importance": "MANDATORY"}]}
    )
    cleared = JobCriteriaRequest.model_validate(
        {
            "education_requirement": None,
            "skills": [{"skill_id": 1, "importance": "MANDATORY"}],
        }
    )
    assert omitted.model_fields_set == {"skills"}
    assert cleared.model_fields_set == {"education_requirement", "skills"}


@pytest.mark.asyncio
async def test_hr_owner_and_admin_can_get_criteria(criteria_api: CriteriaAPIContext) -> None:
    target = criteria_api.jobs["parsed"]
    criteria_api.actor.id = criteria_api.owner.id
    criteria_api.actor.role = "HR"
    owner = await criteria_api.client.get(f"/api/v1/jobs/{target.id}/criteria")
    criteria_api.actor.role = "ADMIN"
    admin = await criteria_api.client.get(f"/api/v1/jobs/{target.id}/criteria")
    assert owner.status_code == admin.status_code == 200
    assert owner.json()["data"]["skills"] == [
        {"skill_id": 1, "importance": "MANDATORY", "min_years_required": 1.0},
        {"skill_id": 2, "importance": "MANDATORY", "min_years_required": 1.0},
    ]


@pytest.mark.asyncio
async def test_get_preparsed_job_is_read_only_and_returns_empty_skills(
    criteria_api: CriteriaAPIContext,
) -> None:
    criteria_api.actor.id = criteria_api.owner.id
    criteria_api.actor.role = "HR"
    target = criteria_api.jobs["pending"]
    before = (target.revision, target.updated_at, target.is_criteria_verified)
    response = await criteria_api.client.get(f"/api/v1/jobs/{target.id}/criteria")
    assert response.status_code == 200
    assert response.json()["data"]["skills"] == []
    assert response.json()["data"]["is_criteria_verified"] is False
    assert (target.revision, target.updated_at, target.is_criteria_verified) == before


@pytest.mark.asyncio
async def test_candidate_forbidden_and_other_hr_job_is_hidden(
    criteria_api: CriteriaAPIContext,
) -> None:
    target = criteria_api.jobs["parsed"]
    candidate_get = await criteria_api.client.get(f"/api/v1/jobs/{target.id}/criteria")
    candidate_put = await criteria_api.client.put(
        f"/api/v1/jobs/{target.id}/criteria",
        json={"skills": [{"skill_id": 1, "importance": "MANDATORY"}]},
    )
    criteria_api.actor.id = criteria_api.other_hr.id
    criteria_api.actor.role = "HR"
    hidden = await criteria_api.client.get(f"/api/v1/jobs/{target.id}/criteria")
    assert candidate_get.status_code == candidate_put.status_code == 403
    assert hidden.status_code == 404


@pytest.mark.asyncio
async def test_put_parsed_job_replaces_criteria_and_preserves_omitted_fields(
    criteria_api: CriteriaAPIContext,
) -> None:
    criteria_api.actor.id = criteria_api.owner.id
    criteria_api.actor.role = "HR"
    target = criteria_api.jobs["parsed"]
    response = await criteria_api.client.put(
        f"/api/v1/jobs/{target.id}/criteria",
        json={"skills": [{"skill_id": 7, "importance": "OPTIONAL", "min_years_required": 2.5}]},
    )
    assert response.status_code == 200
    assert response.json()["data"]["revision"] == 4
    assert response.json()["data"]["is_criteria_verified"] is True
    assert target.min_experience_years == Decimal("2.0")
    assert target.education_requirement == "Bachelor"


@pytest.mark.asyncio
async def test_put_explicit_null_clears_education(criteria_api: CriteriaAPIContext) -> None:
    criteria_api.actor.id = criteria_api.owner.id
    criteria_api.actor.role = "HR"
    target = criteria_api.jobs["parsed"]
    response = await criteria_api.client.put(
        f"/api/v1/jobs/{target.id}/criteria",
        json={
            "education_requirement": None,
            "skills": [{"skill_id": 1, "importance": "MANDATORY"}],
        },
    )
    assert response.status_code == 200
    assert target.education_requirement is None


@pytest.mark.asyncio
async def test_put_preconditions_and_failed_validation_do_not_mutate(
    criteria_api: CriteriaAPIContext,
) -> None:
    criteria_api.actor.id = criteria_api.owner.id
    criteria_api.actor.role = "HR"
    pending = criteria_api.jobs["pending"]
    parsed = criteria_api.jobs["parsed"]
    parsed_before = (
        parsed.revision,
        parsed.min_experience_years,
        list(criteria_api.skills[parsed.id]),
    )
    not_parsed = await criteria_api.client.put(
        f"/api/v1/jobs/{pending.id}/criteria",
        json={"skills": [{"skill_id": 1, "importance": "MANDATORY"}]},
    )
    unknown = await criteria_api.client.put(
        f"/api/v1/jobs/{parsed.id}/criteria",
        json={"skills": [{"skill_id": 999999, "importance": "MANDATORY"}]},
    )
    deleted = await criteria_api.client.put(
        f"/api/v1/jobs/{criteria_api.jobs['deleted'].id}/criteria",
        json={"skills": [{"skill_id": 1, "importance": "MANDATORY"}]},
    )
    assert not_parsed.status_code == 422
    assert not_parsed.json()["error"]["code"] == "JOB_NOT_READY"
    assert unknown.status_code == 422
    assert unknown.json()["error"]["code"] == "INVALID_JOB_CRITERIA"
    assert deleted.status_code == 404
    assert (
        parsed.revision,
        parsed.min_experience_years,
        criteria_api.skills[parsed.id],
    ) == parsed_before


def test_generated_openapi_contains_only_canonical_criteria_methods() -> None:
    operation = app.openapi()["paths"]["/api/v1/jobs/{id}/criteria"]
    assert set(operation) == {"get", "put"}
    request_schema = operation["put"]["requestBody"]["content"]["application/json"]["schema"]
    assert request_schema["$ref"].endswith("/JobCriteriaRequest")
