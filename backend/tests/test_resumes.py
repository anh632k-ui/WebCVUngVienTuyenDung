from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.v1.endpoints import resumes as resume_endpoint
from app.core.config import Settings, get_settings
from app.core.database import get_db_session
from app.core.exceptions import APIError
from app.core.security import create_access_token
from app.models.resume import CandidateProfile, Resume, ResumeEducation, ResumeExperience
from app.models.skill import ResumeSkill
from app.models.user import User
from app.schemas.resume_schema import ParsingStatus
from app.services.resume_service import ResumeAggregate
from main import app

TEST_SECRET = "resume-read-unit-test-jwt-secret"


def make_user(role: str, index: int) -> User:
    now = datetime.now(UTC)
    return User(
        id=uuid.uuid5(uuid.NAMESPACE_URL, f"resume-test-user-{index}"),
        email=f"resume-user-{index}@example.com",
        password_hash="not-returned",
        full_name=f"Resume User {index}",
        phone_number=None,
        role=role,
        is_active=True,
        created_at=now,
        updated_at=now,
    )


def make_resume(
    owner: User,
    index: int,
    status: str,
    *,
    deleted: bool = False,
) -> Resume:
    now = datetime.now(UTC)
    return Resume(
        id=uuid.uuid5(uuid.NAMESPACE_URL, f"resume-test-{index}"),
        owner_user_id=owner.id,
        file_name=f"resume-{index}.pdf",
        storage_key=f"unit/resume-{index}.pdf",
        file_size=100 + index,
        mime_type="application/pdf",
        create_request_fingerprint=f"{index:x}".zfill(64),
        revision=1,
        parsing_status=status,
        raw_text=None,
        resume_embedding=None,
        embedding_model=None,
        embedding_preprocessing_version=None,
        error_message="parse failed" if status == "FAILED" else None,
        is_manually_edited=False,
        is_deleted=deleted,
        created_at=now,
        updated_at=now,
        parsed_at=now if status == "PARSED" else None,
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
class ResumeAPIContext:
    client: AsyncClient
    actor: User
    settings: Settings
    resumes: list[Resume]
    aggregate_id: uuid.UUID

    @property
    def headers(self) -> dict[str, str]:
        token = create_access_token(self.actor.id, self.settings)
        return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def resume_api(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[ResumeAPIContext]:
    candidate = make_user("CANDIDATE", 1)
    other_candidate = make_user("CANDIDATE", 2)
    hr = make_user("HR", 3)
    resumes = [
        make_resume(candidate, 1, "PENDING"),
        make_resume(candidate, 2, "FAILED"),
        make_resume(other_candidate, 3, "PROCESSING"),
        make_resume(hr, 4, "PARSED"),
        make_resume(candidate, 5, "PARSED", deleted=True),
    ]
    settings = Settings(_env_file=None, app_env="test", jwt_secret_key=TEST_SECRET)
    session = AuthSession(candidate)
    context: ResumeAPIContext | None = None

    async def override_session() -> AsyncIterator[AuthSession]:
        yield session

    def override_settings() -> Settings:
        return settings

    def visible(current_user: User) -> list[Resume]:
        assert context is not None
        return [
            resume
            for resume in context.resumes
            if not resume.is_deleted
            and (current_user.role == "ADMIN" or resume.owner_user_id == current_user.id)
        ]

    async def fake_list_resumes(
        _: Any,
        *,
        current_user: User,
        keyword: str | None,
        parsing_status: ParsingStatus | None,
        page: int,
        limit: int,
    ) -> tuple[list[Resume], int]:
        result = visible(current_user)
        if keyword:
            result = [item for item in result if keyword.lower() in item.file_name.lower()]
        if parsing_status is not None:
            result = [item for item in result if item.parsing_status == parsing_status.value]
        result.sort(key=lambda item: (item.created_at, item.id), reverse=True)
        offset = (page - 1) * limit
        return result[offset : offset + limit], len(result)

    async def fake_get_resume(_: Any, *, current_user: User, resume_id: uuid.UUID) -> Resume:
        resume = next((item for item in visible(current_user) if item.id == resume_id), None)
        if resume is None:
            raise APIError(404, "RESUME_NOT_FOUND", "Resume not found")
        return resume

    async def fake_get_aggregate(
        session: Any, *, current_user: User, resume_id: uuid.UUID
    ) -> ResumeAggregate:
        resume = await fake_get_resume(session, current_user=current_user, resume_id=resume_id)
        if context is None or resume.id != context.aggregate_id:
            return ResumeAggregate(resume, None, [], [], [])
        profile = CandidateProfile(
            resume_id=resume.id,
            full_name="Parsed Candidate",
            email="parsed@example.com",
            phone_number=None,
            current_title="Engineer",
            location="Hanoi",
            linkedin_url=None,
            github_url=None,
            professional_summary="Summary",
        )
        skill = ResumeSkill(
            resume_id=resume.id,
            skill_id=1,
            years_of_experience=Decimal("2.0"),
            proficiency_level="ADVANCED",
        )
        experience = ResumeExperience(
            resume_id=resume.id,
            company_name="Company",
            job_title="Engineer",
            start_date=date(2024, 1, 1),
            end_date=None,
            is_current=True,
            description=None,
        )
        education = ResumeEducation(
            resume_id=resume.id,
            institution_name="University",
            degree="BSc",
            field_of_study="CS",
            start_year=2020,
            graduation_year=2024,
            gpa=Decimal("3.50"),
            description=None,
        )
        return ResumeAggregate(resume, profile, [skill], [experience], [education])

    async def fake_soft_delete_resume(_: Any, *, current_user: User, resume_id: uuid.UUID) -> None:
        resume = await fake_get_resume(session, current_user=current_user, resume_id=resume_id)
        now = datetime.now(UTC)
        resume.is_deleted = True
        resume.deleted_at = now
        resume.updated_at = now

    monkeypatch.setattr(resume_endpoint, "list_resumes", fake_list_resumes)
    monkeypatch.setattr(resume_endpoint, "get_resume", fake_get_resume)
    monkeypatch.setattr(resume_endpoint, "get_resume_aggregate", fake_get_aggregate)
    monkeypatch.setattr(resume_endpoint, "soft_delete_resume", fake_soft_delete_resume)
    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_settings] = override_settings
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        context = ResumeAPIContext(client, candidate, settings, resumes, resumes[3].id)
        yield context
    app.dependency_overrides.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/resumes",
        f"/api/v1/resumes/{uuid.uuid4()}",
        f"/api/v1/resumes/{uuid.uuid4()}/status",
    ],
)
async def test_resume_reads_require_authentication(resume_api: ResumeAPIContext, path: str) -> None:
    response = await resume_api.client.get(path)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


@pytest.mark.asyncio
async def test_candidate_list_is_owned_paginated_filtered_and_read_only(
    resume_api: ResumeAPIContext,
) -> None:
    before = [(item.id, item.parsing_status, item.is_deleted) for item in resume_api.resumes]
    response = await resume_api.client.get(
        "/api/v1/resumes",
        params={"parsing_status": "PENDING", "keyword": "RESUME", "limit": 1},
        headers=resume_api.headers,
    )
    empty = await resume_api.client.get(
        "/api/v1/resumes", params={"keyword": "missing"}, headers=resume_api.headers
    )
    assert response.status_code == 200
    assert response.json()["meta"] == {
        "page": 1,
        "limit": 1,
        "total_items": 1,
        "total_pages": 1,
    }
    assert response.json()["data"][0]["id"] == str(resume_api.resumes[0].id)
    assert empty.json()["data"] == []
    assert empty.json()["meta"]["total_pages"] == 0
    assert before == [
        (item.id, item.parsing_status, item.is_deleted) for item in resume_api.resumes
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["CANDIDATE", "HR"])
async def test_non_admin_cannot_read_another_users_resume(
    resume_api: ResumeAPIContext, role: str
) -> None:
    resume_api.actor.role = role
    response = await resume_api.client.get(
        f"/api/v1/resumes/{resume_api.resumes[2].id}", headers=resume_api.headers
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RESUME_NOT_FOUND"


@pytest.mark.asyncio
async def test_hr_can_read_own_resume(resume_api: ResumeAPIContext) -> None:
    resume_api.actor.id = resume_api.resumes[3].owner_user_id
    resume_api.actor.role = "HR"
    response = await resume_api.client.get(
        f"/api/v1/resumes/{resume_api.resumes[3].id}", headers=resume_api.headers
    )
    assert response.status_code == 200
    assert response.json()["data"]["candidate_profile"]["full_name"] == "Parsed Candidate"
    assert response.json()["data"]["skills"][0]["skill_id"] == 1
    assert response.json()["data"]["experiences"][0]["company_name"] == "Company"
    assert response.json()["data"]["educations"][0]["institution_name"] == "University"


@pytest.mark.asyncio
async def test_admin_can_list_and_read_all_non_deleted_resumes(
    resume_api: ResumeAPIContext,
) -> None:
    resume_api.actor.role = "ADMIN"
    listing = await resume_api.client.get("/api/v1/resumes", headers=resume_api.headers)
    detail = await resume_api.client.get(
        f"/api/v1/resumes/{resume_api.resumes[2].id}", headers=resume_api.headers
    )
    assert listing.status_code == 200
    assert listing.json()["meta"]["total_items"] == 4
    assert all(item["id"] != str(resume_api.resumes[4].id) for item in listing.json()["data"])
    assert detail.status_code == 200


@pytest.mark.asyncio
async def test_pending_detail_has_nullable_profile_and_canonical_fields(
    resume_api: ResumeAPIContext,
) -> None:
    response = await resume_api.client.get(
        f"/api/v1/resumes/{resume_api.resumes[0].id}", headers=resume_api.headers
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["candidate_profile"] is None
    assert data["skills"] == [] and data["experiences"] == [] and data["educations"] == []
    assert set(data["resume"]) == {
        "id",
        "revision",
        "file_name",
        "file_size",
        "mime_type",
        "parsing_status",
        "is_manually_edited",
        "created_at",
        "parsed_at",
    }
    assert "storage_key" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("status", list(ParsingStatus))
async def test_status_endpoint_returns_each_canonical_status(
    resume_api: ResumeAPIContext, status: ParsingStatus
) -> None:
    target = resume_api.resumes[0]
    target.parsing_status = status.value
    target.error_message = "failure" if status is ParsingStatus.FAILED else None
    response = await resume_api.client.get(
        f"/api/v1/resumes/{target.id}/status", headers=resume_api.headers
    )
    assert response.status_code == 200
    assert response.json()["data"]["parsing_status"] == status.value
    assert response.json()["data"]["error_message"] == target.error_message


@pytest.mark.asyncio
async def test_deleted_and_nonexistent_resume_return_canonical_not_found(
    resume_api: ResumeAPIContext,
) -> None:
    for resume_id in (resume_api.resumes[4].id, uuid.uuid4()):
        response = await resume_api.client.get(
            f"/api/v1/resumes/{resume_id}", headers=resume_api.headers
        )
        assert response.status_code == 404
        assert response.json() == {
            "success": False,
            "error": {
                "code": "RESUME_NOT_FOUND",
                "message": "Resume not found",
                "details": None,
            },
        }


@pytest.mark.asyncio
async def test_candidate_owner_soft_delete_hides_resume_without_other_mutation(
    resume_api: ResumeAPIContext,
) -> None:
    target = resume_api.resumes[0]
    original_revision = target.revision
    original_storage_key = target.storage_key
    response = await resume_api.client.delete(
        f"/api/v1/resumes/{target.id}", headers=resume_api.headers
    )
    listing = await resume_api.client.get("/api/v1/resumes", headers=resume_api.headers)
    detail = await resume_api.client.get(f"/api/v1/resumes/{target.id}", headers=resume_api.headers)
    status_response = await resume_api.client.get(
        f"/api/v1/resumes/{target.id}/status", headers=resume_api.headers
    )
    assert response.status_code == 204 and response.content == b""
    assert target.is_deleted is True and target.deleted_at is not None
    assert target.revision == original_revision
    assert target.storage_key == original_storage_key
    assert str(target.id) not in listing.text
    assert detail.status_code == 404
    assert status_response.status_code == 404


@pytest.mark.asyncio
async def test_hr_owner_and_admin_can_soft_delete(resume_api: ResumeAPIContext) -> None:
    hr_resume = resume_api.resumes[3]
    resume_api.actor.id = hr_resume.owner_user_id
    resume_api.actor.role = "HR"
    hr_response = await resume_api.client.delete(
        f"/api/v1/resumes/{hr_resume.id}", headers=resume_api.headers
    )

    resume_api.actor.role = "ADMIN"
    admin_target = resume_api.resumes[2]
    admin_response = await resume_api.client.delete(
        f"/api/v1/resumes/{admin_target.id}", headers=resume_api.headers
    )
    assert hr_response.status_code == 204 and hr_resume.is_deleted is True
    assert admin_response.status_code == 204 and admin_target.is_deleted is True


@pytest.mark.asyncio
async def test_resume_delete_auth_ownership_and_not_found_semantics(
    resume_api: ResumeAPIContext,
) -> None:
    unauthenticated = await resume_api.client.delete(f"/api/v1/resumes/{resume_api.resumes[0].id}")
    non_owner = await resume_api.client.delete(
        f"/api/v1/resumes/{resume_api.resumes[2].id}", headers=resume_api.headers
    )
    already_deleted = await resume_api.client.delete(
        f"/api/v1/resumes/{resume_api.resumes[4].id}", headers=resume_api.headers
    )
    nonexistent = await resume_api.client.delete(
        f"/api/v1/resumes/{uuid.uuid4()}", headers=resume_api.headers
    )
    assert unauthenticated.status_code == 401
    for response in (non_owner, already_deleted, nonexistent):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "RESUME_NOT_FOUND"


def test_resume_route_table_contains_only_requested_read_methods() -> None:
    paths = app.openapi()["paths"]
    assert set(paths["/api/v1/resumes"]) == {"get"}
    assert set(paths["/api/v1/resumes/{id}"]) == {"get", "delete"}
    assert set(paths["/api/v1/resumes/{id}/status"]) == {"get"}
