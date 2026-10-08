from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.v1.endpoints import jobs as job_endpoint
from app.core.config import Settings, get_settings
from app.core.database import get_db_session
from app.core.exceptions import APIError
from app.core.security import create_access_token
from app.models.job import JobDescription
from app.models.user import User
from app.schemas.job_schema import JobStatus, ParsingStatus
from main import app

TEST_SECRET = "job-read-unit-test-jwt-secret-value"


def make_user(role: str, index: int) -> User:
    now = datetime.now(UTC)
    return User(
        id=uuid.uuid5(uuid.NAMESPACE_URL, f"job-test-user-{index}"),
        email=f"job-user-{index}@example.com",
        password_hash="not-returned",
        full_name=f"Job User {index}",
        phone_number=None,
        role=role,
        is_active=True,
        created_at=now,
        updated_at=now,
    )


def make_job(
    recruiter: User,
    index: int,
    status: str,
    parsing_status: str,
    *,
    deleted: bool = False,
) -> JobDescription:
    now = datetime.now(UTC)
    return JobDescription(
        id=uuid.uuid5(uuid.NAMESPACE_URL, f"job-test-{index}"),
        recruiter_id=recruiter.id,
        title=f"Software Engineer {index}",
        job_level="MID",
        location="Hanoi",
        raw_content=f"Python role {index}",
        create_request_fingerprint=f"{index:x}".zfill(64),
        revision=1,
        min_experience_years=Decimal("1.0"),
        education_requirement="Bachelor",
        job_embedding=None,
        embedding_model=None,
        embedding_preprocessing_version=None,
        parsing_status=parsing_status,
        parsing_error_message=None,
        is_criteria_verified=status == "ACTIVE",
        w_skill=Decimal("0.500"),
        w_semantic=Decimal("0.300"),
        w_experience=Decimal("0.200"),
        status=status,
        is_deleted=deleted,
        created_at=now,
        updated_at=now,
        parsed_at=now if parsing_status == "PARSED" else None,
        deleted_at=now if deleted else None,
    )


class FakeResult:
    def __init__(self, user: User) -> None:
        self.user = user

    def scalar_one_or_none(self) -> User:
        return self.user


class AuthSession:
    def __init__(self, actor: User) -> None:
        self.actor = actor

    async def execute(self, _: Any) -> FakeResult:
        return FakeResult(self.actor)


@dataclass
class JobAPIContext:
    client: AsyncClient
    actor: User
    settings: Settings
    jobs: list[JobDescription]
    hr_one: User
    hr_two: User
    jobs_with_skills: set[uuid.UUID] = field(default_factory=set)

    @property
    def headers(self) -> dict[str, str]:
        token = create_access_token(self.actor.id, self.settings)
        return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def job_api(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[JobAPIContext]:
    candidate = make_user("CANDIDATE", 1)
    hr_one = make_user("HR", 2)
    hr_two = make_user("HR", 3)
    jobs = [
        make_job(hr_one, 1, "DRAFT", "PENDING"),
        make_job(hr_one, 2, "ACTIVE", "PARSED"),
        make_job(hr_one, 3, "CLOSED", "PARSED"),
        make_job(hr_two, 4, "ACTIVE", "PARSED"),
        make_job(hr_one, 5, "ACTIVE", "PARSED", deleted=True),
    ]
    settings = Settings(_env_file=None, app_env="test", jwt_secret_key=TEST_SECRET)
    session = AuthSession(candidate)
    context: JobAPIContext | None = None

    async def override_session() -> AsyncIterator[AuthSession]:
        yield session

    def override_settings() -> Settings:
        return settings

    def visible(current_user: User) -> list[JobDescription]:
        assert context is not None
        result = [job for job in context.jobs if not job.is_deleted]
        if current_user.role == "CANDIDATE":
            return [job for job in result if job.status == "ACTIVE"]
        if current_user.role == "HR":
            return [job for job in result if job.recruiter_id == current_user.id]
        return result

    async def fake_list_jobs(
        _: Any,
        *,
        current_user: User,
        keyword: str | None,
        status: JobStatus | None,
        parsing_status: ParsingStatus | None,
        page: int,
        limit: int,
    ) -> tuple[list[JobDescription], int]:
        result = visible(current_user)
        if keyword:
            lowered = keyword.lower()
            result = [
                job
                for job in result
                if lowered in job.title.lower()
                or lowered in (job.location or "").lower()
                or lowered in job.raw_content.lower()
            ]
        if status is not None:
            result = [job for job in result if job.status == status.value]
        if parsing_status is not None:
            result = [job for job in result if job.parsing_status == parsing_status.value]
        result.sort(key=lambda job: (job.created_at, job.id), reverse=True)
        offset = (page - 1) * limit
        return result[offset : offset + limit], len(result)

    async def fake_get_job(_: Any, *, current_user: User, job_id: uuid.UUID) -> JobDescription:
        job = next((item for item in visible(current_user) if item.id == job_id), None)
        if job is None:
            raise APIError(404, "JOB_NOT_FOUND", "Job not found")
        return job

    async def fake_soft_delete_job(_: Any, *, current_user: User, job_id: uuid.UUID) -> None:
        job = await fake_get_job(session, current_user=current_user, job_id=job_id)
        now = datetime.now(UTC)
        job.is_deleted = True
        job.deleted_at = now
        job.updated_at = now

    async def fake_change_job_status(
        _: Any,
        *,
        current_user: User,
        job_id: uuid.UUID,
        target_status: JobStatus,
    ) -> JobDescription:
        assert context is not None
        job = await fake_get_job(session, current_user=current_user, job_id=job_id)
        if job.status == target_status.value:
            return job
        if job.status == "DRAFT" and target_status is JobStatus.CLOSED:
            raise APIError(
                422,
                "INVALID_STATUS_TRANSITION",
                "DRAFT jobs cannot transition directly to CLOSED",
            )
        if target_status is JobStatus.ACTIVE and not (
            job.parsing_status == "PARSED"
            and job.is_criteria_verified
            and job.job_embedding is not None
            and bool(job.embedding_model)
            and bool(job.embedding_preprocessing_version)
            and job.id in context.jobs_with_skills
        ):
            raise APIError(
                422,
                "JOB_NOT_READY",
                "Job does not satisfy ACTIVE readiness requirements",
            )
        job.status = target_status.value
        job.updated_at = datetime.now(UTC)
        return job

    monkeypatch.setattr(job_endpoint, "list_jobs", fake_list_jobs)
    monkeypatch.setattr(job_endpoint, "get_job", fake_get_job)
    monkeypatch.setattr(job_endpoint, "soft_delete_job", fake_soft_delete_job)
    monkeypatch.setattr(job_endpoint, "change_job_status", fake_change_job_status)
    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_settings] = override_settings
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        context = JobAPIContext(client, candidate, settings, jobs, hr_one, hr_two)
        yield context
    app.dependency_overrides.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/v1/jobs", f"/api/v1/jobs/{uuid.uuid4()}"])
async def test_job_reads_require_authentication(job_api: JobAPIContext, path: str) -> None:
    response = await job_api.client.get(path)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


@pytest.mark.asyncio
async def test_candidate_sees_only_active_non_deleted_jobs(job_api: JobAPIContext) -> None:
    before = [(job.id, job.status, job.updated_at) for job in job_api.jobs]
    response = await job_api.client.get("/api/v1/jobs", headers=job_api.headers)
    assert response.status_code == 200
    assert response.json()["meta"]["total_items"] == 2
    assert {job["status"] for job in response.json()["data"]} == {"ACTIVE"}
    assert str(job_api.jobs[4].id) not in response.text
    assert before == [(job.id, job.status, job.updated_at) for job in job_api.jobs]


@pytest.mark.asyncio
@pytest.mark.parametrize("job_index", [0, 2])
async def test_candidate_cannot_read_draft_or_closed_detail(
    job_api: JobAPIContext, job_index: int
) -> None:
    response = await job_api.client.get(
        f"/api/v1/jobs/{job_api.jobs[job_index].id}", headers=job_api.headers
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "JOB_NOT_FOUND"


@pytest.mark.asyncio
async def test_candidate_can_read_active_detail_with_only_canonical_fields(
    job_api: JobAPIContext,
) -> None:
    response = await job_api.client.get(
        f"/api/v1/jobs/{job_api.jobs[1].id}", headers=job_api.headers
    )
    assert response.status_code == 200
    assert set(response.json()["data"]) == {
        "id",
        "recruiter_id",
        "revision",
        "title",
        "job_level",
        "location",
        "raw_content",
        "min_experience_years",
        "education_requirement",
        "parsing_status",
        "is_criteria_verified",
        "w_skill",
        "w_semantic",
        "w_experience",
        "status",
        "created_at",
        "updated_at",
        "parsed_at",
    }
    assert "create_request_fingerprint" not in response.text
    assert "job_embedding" not in response.text


@pytest.mark.asyncio
async def test_hr_sees_only_owned_jobs_and_cannot_read_other_hr_job(
    job_api: JobAPIContext,
) -> None:
    job_api.actor.id = job_api.hr_one.id
    job_api.actor.role = "HR"
    listing = await job_api.client.get("/api/v1/jobs", headers=job_api.headers)
    denied = await job_api.client.get(f"/api/v1/jobs/{job_api.jobs[3].id}", headers=job_api.headers)
    assert listing.json()["meta"]["total_items"] == 3
    assert {job["recruiter_id"] for job in listing.json()["data"]} == {str(job_api.hr_one.id)}
    assert denied.status_code == 404


@pytest.mark.asyncio
async def test_admin_scope_filters_pagination_and_empty_results(job_api: JobAPIContext) -> None:
    job_api.actor.role = "ADMIN"
    page = await job_api.client.get(
        "/api/v1/jobs",
        params={"keyword": "PYTHON", "parsing_status": "PARSED", "page": 2, "limit": 2},
        headers=job_api.headers,
    )
    draft = await job_api.client.get(
        "/api/v1/jobs", params={"status": "DRAFT"}, headers=job_api.headers
    )
    empty = await job_api.client.get(
        "/api/v1/jobs", params={"keyword": "missing"}, headers=job_api.headers
    )
    assert page.status_code == 200
    assert page.json()["meta"] == {
        "page": 2,
        "limit": 2,
        "total_items": 3,
        "total_pages": 2,
    }
    assert len(page.json()["data"]) == 1
    assert [job["status"] for job in draft.json()["data"]] == ["DRAFT"]
    assert empty.json()["data"] == []
    assert empty.json()["meta"]["total_pages"] == 0


@pytest.mark.asyncio
async def test_admin_can_read_existing_job_and_missing_is_canonical_404(
    job_api: JobAPIContext,
) -> None:
    job_api.actor.role = "ADMIN"
    visible = await job_api.client.get(
        f"/api/v1/jobs/{job_api.jobs[0].id}", headers=job_api.headers
    )
    missing = await job_api.client.get(f"/api/v1/jobs/{uuid.uuid4()}", headers=job_api.headers)
    assert visible.status_code == 200
    assert missing.status_code == 404
    assert missing.json() == {
        "success": False,
        "error": {"code": "JOB_NOT_FOUND", "message": "Job not found", "details": None},
    }


@pytest.mark.asyncio
async def test_hr_owner_soft_delete_hides_job_without_unrelated_mutation(
    job_api: JobAPIContext,
) -> None:
    job_api.actor.id = job_api.hr_one.id
    job_api.actor.role = "HR"
    target = job_api.jobs[1]
    original = (target.revision, target.raw_content, target.parsing_status)
    response = await job_api.client.delete(f"/api/v1/jobs/{target.id}", headers=job_api.headers)
    listing = await job_api.client.get("/api/v1/jobs", headers=job_api.headers)
    detail = await job_api.client.get(f"/api/v1/jobs/{target.id}", headers=job_api.headers)
    assert response.status_code == 204 and response.content == b""
    assert target.is_deleted is True and target.deleted_at is not None
    assert (target.revision, target.raw_content, target.parsing_status) == original
    assert str(target.id) not in listing.text
    assert detail.status_code == 404


@pytest.mark.asyncio
async def test_job_delete_authorization_ownership_and_not_found(
    job_api: JobAPIContext,
) -> None:
    target = job_api.jobs[0]
    unauthenticated = await job_api.client.delete(f"/api/v1/jobs/{target.id}")
    candidate = await job_api.client.delete(f"/api/v1/jobs/{target.id}", headers=job_api.headers)

    job_api.actor.id = job_api.hr_two.id
    job_api.actor.role = "HR"
    non_owner = await job_api.client.delete(f"/api/v1/jobs/{target.id}", headers=job_api.headers)
    already_deleted = await job_api.client.delete(
        f"/api/v1/jobs/{job_api.jobs[4].id}", headers=job_api.headers
    )
    nonexistent = await job_api.client.delete(
        f"/api/v1/jobs/{uuid.uuid4()}", headers=job_api.headers
    )
    assert unauthenticated.status_code == 401
    assert candidate.status_code == 403
    assert candidate.json()["error"]["code"] == "INSUFFICIENT_PERMISSIONS"
    for response in (non_owner, already_deleted, nonexistent):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "JOB_NOT_FOUND"


@pytest.mark.asyncio
async def test_admin_can_soft_delete_job(job_api: JobAPIContext) -> None:
    job_api.actor.role = "ADMIN"
    target = job_api.jobs[0]
    response = await job_api.client.delete(f"/api/v1/jobs/{target.id}", headers=job_api.headers)
    assert response.status_code == 204
    assert target.is_deleted is True and target.deleted_at is not None


def test_job_route_table_preserves_existing_methods_with_create() -> None:
    paths = app.openapi()["paths"]
    assert set(paths["/api/v1/jobs"]) == {"get", "post"}
    assert set(paths["/api/v1/jobs/{id}"]) == {"get", "put", "delete"}
    assert set(paths["/api/v1/jobs/{id}/status"]) == {"patch"}


def make_job_ready(context: JobAPIContext, job: JobDescription) -> None:
    job.parsing_status = "PARSED"
    job.is_criteria_verified = True
    job.job_embedding = [0.0] * 1024
    job.embedding_model = "integration-model"
    job.embedding_preprocessing_version = "v1"
    context.jobs_with_skills.add(job.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("current_status", list(JobStatus))
async def test_same_job_status_is_idempotent(
    job_api: JobAPIContext, current_status: JobStatus
) -> None:
    job_api.actor.id = job_api.hr_one.id
    job_api.actor.role = "HR"
    target = job_api.jobs[0]
    target.status = current_status.value
    original_updated_at = target.updated_at
    original_revision = target.revision
    response = await job_api.client.patch(
        f"/api/v1/jobs/{target.id}/status",
        json={"status": current_status.value},
        headers=job_api.headers,
    )
    assert response.status_code == 200
    assert response.json()["data"]["status"] == current_status.value
    assert target.updated_at == original_updated_at
    assert target.revision == original_revision


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_status", [JobStatus.DRAFT, JobStatus.CLOSED])
async def test_ready_job_can_transition_to_active(
    job_api: JobAPIContext, initial_status: JobStatus
) -> None:
    job_api.actor.id = job_api.hr_one.id
    job_api.actor.role = "HR"
    target = job_api.jobs[0]
    target.status = initial_status.value
    original_revision = target.revision
    make_job_ready(job_api, target)
    response = await job_api.client.patch(
        f"/api/v1/jobs/{target.id}/status",
        json={"status": "ACTIVE"},
        headers=job_api.headers,
    )
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "ACTIVE"
    assert target.revision == original_revision


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing_condition",
    ["parsing", "verified", "embedding", "model", "preprocessing", "skill"],
)
async def test_each_active_readiness_failure_returns_422(
    job_api: JobAPIContext, missing_condition: str
) -> None:
    job_api.actor.id = job_api.hr_one.id
    job_api.actor.role = "HR"
    target = job_api.jobs[0]
    target.status = "DRAFT"
    make_job_ready(job_api, target)
    if missing_condition == "parsing":
        target.parsing_status = "PENDING"
    elif missing_condition == "verified":
        target.is_criteria_verified = False
    elif missing_condition == "embedding":
        target.job_embedding = None
    elif missing_condition == "model":
        target.embedding_model = None
    elif missing_condition == "preprocessing":
        target.embedding_preprocessing_version = None
    else:
        job_api.jobs_with_skills.remove(target.id)

    response = await job_api.client.patch(
        f"/api/v1/jobs/{target.id}/status",
        json={"status": "ACTIVE"},
        headers=job_api.headers,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "JOB_NOT_READY"
    assert target.status == "DRAFT"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("initial_status", "target_status"),
    [("ACTIVE", "DRAFT"), ("ACTIVE", "CLOSED"), ("CLOSED", "DRAFT")],
)
async def test_non_active_allowed_status_transitions(
    job_api: JobAPIContext, initial_status: str, target_status: str
) -> None:
    job_api.actor.id = job_api.hr_one.id
    job_api.actor.role = "HR"
    target = job_api.jobs[0]
    target.status = initial_status
    original_revision = target.revision
    response = await job_api.client.patch(
        f"/api/v1/jobs/{target.id}/status",
        json={"status": target_status},
        headers=job_api.headers,
    )
    assert response.status_code == 200
    assert target.status == target_status
    assert target.revision == original_revision


@pytest.mark.asyncio
async def test_draft_to_closed_is_invalid(job_api: JobAPIContext) -> None:
    job_api.actor.id = job_api.hr_one.id
    job_api.actor.role = "HR"
    target = job_api.jobs[0]
    target.status = "DRAFT"
    response = await job_api.client.patch(
        f"/api/v1/jobs/{target.id}/status",
        json={"status": "CLOSED"},
        headers=job_api.headers,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_STATUS_TRANSITION"
    assert target.status == "DRAFT"


@pytest.mark.asyncio
async def test_job_status_authorization_ownership_deleted_and_admin_paths(
    job_api: JobAPIContext,
) -> None:
    target = job_api.jobs[0]
    unauthenticated = await job_api.client.patch(
        f"/api/v1/jobs/{target.id}/status", json={"status": "DRAFT"}
    )
    candidate = await job_api.client.patch(
        f"/api/v1/jobs/{target.id}/status",
        json={"status": "DRAFT"},
        headers=job_api.headers,
    )
    job_api.actor.id = job_api.hr_two.id
    job_api.actor.role = "HR"
    non_owner = await job_api.client.patch(
        f"/api/v1/jobs/{target.id}/status",
        json={"status": "DRAFT"},
        headers=job_api.headers,
    )
    job_api.actor.id = job_api.hr_one.id
    deleted = await job_api.client.patch(
        f"/api/v1/jobs/{job_api.jobs[4].id}/status",
        json={"status": "ACTIVE"},
        headers=job_api.headers,
    )
    job_api.actor.role = "ADMIN"
    admin = await job_api.client.patch(
        f"/api/v1/jobs/{target.id}/status",
        json={"status": "DRAFT"},
        headers=job_api.headers,
    )
    assert unauthenticated.status_code == 401
    assert candidate.status_code == 403
    assert non_owner.status_code == 404
    assert deleted.status_code == 404
    assert admin.status_code == 200
