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

    monkeypatch.setattr(job_endpoint, "list_jobs", fake_list_jobs)
    monkeypatch.setattr(job_endpoint, "get_job", fake_get_job)
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


def test_job_route_table_contains_only_requested_read_methods() -> None:
    paths = app.openapi()["paths"]
    assert set(paths["/api/v1/jobs"]) == {"get"}
    assert set(paths["/api/v1/jobs/{id}"]) == {"get"}
