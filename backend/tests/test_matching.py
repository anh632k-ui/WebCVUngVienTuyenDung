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

from app.api.v1.endpoints import matching as matching_endpoint
from app.core.config import Settings, get_settings
from app.core.database import get_db_session
from app.core.exceptions import APIError
from app.core.security import create_access_token
from app.models.match_result import MatchResult
from app.models.user import User
from app.schemas.match_schema import MatchStatus
from main import app

TEST_SECRET = "matching-read-unit-test-jwt-secret-value"


def make_user(role: str, index: int) -> User:
    now = datetime.now(UTC)
    return User(
        id=uuid.uuid5(uuid.NAMESPACE_URL, f"matching-user-{index}"),
        email=f"matching-user-{index}@example.com",
        password_hash="not-returned",
        full_name=f"Matching User {index}",
        phone_number=None,
        role=role,
        is_active=True,
        created_at=now,
        updated_at=now,
    )


def make_match(index: int, status: str) -> MatchResult:
    now = datetime.now(UTC)
    completed = status == "COMPLETED"
    return MatchResult(
        id=uuid.uuid5(uuid.NAMESPACE_URL, f"matching-result-{index}"),
        job_id=uuid.uuid5(uuid.NAMESPACE_URL, f"matching-job-{index}"),
        resume_id=uuid.uuid5(uuid.NAMESPACE_URL, f"matching-resume-{index}"),
        generation=index + 1,
        resume_revision=1,
        job_revision=1,
        overall_score=Decimal("80.00") if completed else None,
        skill_score=Decimal("75.00") if completed else None,
        semantic_score=Decimal("85.00") if completed else None,
        experience_score=Decimal("80.00") if completed else None,
        matched_skills=[{"skill_id": 1, "name": "Python"}] if completed else [],
        missing_skills=[{"skill_id": 2, "name": "SQL"}] if completed else [],
        gap_analysis_summary="Improve SQL" if completed else None,
        algorithm_version="hybrid-v1",
        embedding_model="integration-model" if completed else None,
        embedding_preprocessing_version="v1" if completed else None,
        status=status,
        error_message="matching failed" if status == "FAILED" else None,
        created_at=now,
        updated_at=now,
        calculated_at=now if completed else None,
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
class MatchingAPIContext:
    client: AsyncClient
    actor: User
    settings: Settings
    matches: list[MatchResult]
    candidate: User
    other_candidate: User
    hr_one: User
    hr_two: User
    resume_owners: dict[uuid.UUID, uuid.UUID]
    job_owners: dict[uuid.UUID, uuid.UUID]
    deleted_resumes: set[uuid.UUID] = field(default_factory=set)
    deleted_jobs: set[uuid.UUID] = field(default_factory=set)

    @property
    def headers(self) -> dict[str, str]:
        token = create_access_token(self.actor.id, self.settings)
        return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def matching_api(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[MatchingAPIContext]:
    candidate = make_user("CANDIDATE", 1)
    other_candidate = make_user("CANDIDATE", 2)
    hr_one = make_user("HR", 3)
    hr_two = make_user("HR", 4)
    matches = [
        make_match(1, "COMPLETED"),
        make_match(2, "PENDING"),
        make_match(3, "PROCESSING"),
        make_match(4, "FAILED"),
        make_match(5, "COMPLETED"),
        make_match(6, "COMPLETED"),
        make_match(7, "PENDING"),
        make_match(8, "FAILED"),
    ]
    resume_owners = {
        matches[0].resume_id: candidate.id,
        matches[1].resume_id: candidate.id,
        matches[2].resume_id: other_candidate.id,
        matches[3].resume_id: hr_one.id,
        matches[4].resume_id: hr_one.id,
        matches[5].resume_id: hr_two.id,
        matches[6].resume_id: candidate.id,
        matches[7].resume_id: hr_one.id,
    }
    job_owners = {
        matches[0].job_id: hr_one.id,
        matches[1].job_id: hr_two.id,
        matches[2].job_id: hr_one.id,
        matches[3].job_id: hr_one.id,
        matches[4].job_id: hr_two.id,
        matches[5].job_id: hr_one.id,
        matches[6].job_id: hr_one.id,
        matches[7].job_id: hr_one.id,
    }
    settings = Settings(_env_file=None, app_env="test", jwt_secret_key=TEST_SECRET)
    session = AuthSession(candidate)
    context: MatchingAPIContext | None = None

    async def override_session() -> AsyncIterator[AuthSession]:
        yield session

    def override_settings() -> Settings:
        return settings

    def visible(current_user: User) -> list[MatchResult]:
        assert context is not None
        result = [
            match
            for match in context.matches
            if match.resume_id not in context.deleted_resumes
            and match.job_id not in context.deleted_jobs
        ]
        if current_user.role == "CANDIDATE":
            return [
                match
                for match in result
                if context.resume_owners[match.resume_id] == current_user.id
            ]
        if current_user.role == "HR":
            return [
                match
                for match in result
                if context.resume_owners[match.resume_id] == current_user.id
                and context.job_owners[match.job_id] == current_user.id
            ]
        return result

    async def fake_list_matches(
        _: Any,
        *,
        current_user: User,
        job_id: uuid.UUID | None,
        resume_id: uuid.UUID | None,
        status: MatchStatus | None,
        page: int,
        limit: int,
    ) -> tuple[list[MatchResult], int]:
        result = visible(current_user)
        if job_id is not None:
            result = [match for match in result if match.job_id == job_id]
        if resume_id is not None:
            result = [match for match in result if match.resume_id == resume_id]
        if status is not None:
            result = [match for match in result if match.status == status.value]
        result.sort(key=lambda match: (match.created_at, match.id), reverse=True)
        offset = (page - 1) * limit
        return result[offset : offset + limit], len(result)

    async def fake_get_match(_: Any, *, current_user: User, match_id: uuid.UUID) -> MatchResult:
        match = next((item for item in visible(current_user) if item.id == match_id), None)
        if match is None:
            raise APIError(404, "MATCH_NOT_FOUND", "Match not found")
        return match

    monkeypatch.setattr(matching_endpoint, "list_matches", fake_list_matches)
    monkeypatch.setattr(matching_endpoint, "get_match", fake_get_match)
    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_settings] = override_settings
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        context = MatchingAPIContext(
            client,
            candidate,
            settings,
            matches,
            candidate,
            other_candidate,
            hr_one,
            hr_two,
            resume_owners,
            job_owners,
            {matches[6].resume_id},
            {matches[7].job_id},
        )
        yield context
    app.dependency_overrides.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/v1/matching", f"/api/v1/matching/{uuid.uuid4()}"])
async def test_matching_reads_require_authentication(
    matching_api: MatchingAPIContext, path: str
) -> None:
    response = await matching_api.client.get(path)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_candidate_sees_only_matches_for_owned_resumes(
    matching_api: MatchingAPIContext,
) -> None:
    before = [(item.id, item.status, item.updated_at) for item in matching_api.matches]
    response = await matching_api.client.get("/api/v1/matching", headers=matching_api.headers)
    assert response.status_code == 200
    assert response.json()["meta"]["total_items"] == 2
    assert {item["id"] for item in response.json()["data"]} == {
        str(matching_api.matches[0].id),
        str(matching_api.matches[1].id),
    }
    assert before == [(item.id, item.status, item.updated_at) for item in matching_api.matches]


@pytest.mark.asyncio
async def test_candidate_cannot_read_another_resume_match(
    matching_api: MatchingAPIContext,
) -> None:
    response = await matching_api.client.get(
        f"/api/v1/matching/{matching_api.matches[2].id}", headers=matching_api.headers
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_hr_requires_both_resume_and_job_ownership(
    matching_api: MatchingAPIContext,
) -> None:
    matching_api.actor.id = matching_api.hr_one.id
    matching_api.actor.role = "HR"
    listing = await matching_api.client.get("/api/v1/matching", headers=matching_api.headers)
    only_job = await matching_api.client.get(
        f"/api/v1/matching/{matching_api.matches[2].id}", headers=matching_api.headers
    )
    only_resume = await matching_api.client.get(
        f"/api/v1/matching/{matching_api.matches[4].id}", headers=matching_api.headers
    )
    assert listing.json()["meta"]["total_items"] == 1
    assert listing.json()["data"][0]["id"] == str(matching_api.matches[3].id)
    assert only_job.status_code == 404
    assert only_resume.status_code == 404


@pytest.mark.asyncio
async def test_admin_filters_pagination_and_lifecycle_representations(
    matching_api: MatchingAPIContext,
) -> None:
    matching_api.actor.role = "ADMIN"
    all_matches = await matching_api.client.get(
        "/api/v1/matching?page=2&limit=2", headers=matching_api.headers
    )
    completed = await matching_api.client.get(
        "/api/v1/matching",
        params={"status": "COMPLETED"},
        headers=matching_api.headers,
    )
    by_job = await matching_api.client.get(
        "/api/v1/matching",
        params={"job_id": matching_api.matches[0].job_id},
        headers=matching_api.headers,
    )
    by_resume = await matching_api.client.get(
        "/api/v1/matching",
        params={"resume_id": matching_api.matches[1].resume_id},
        headers=matching_api.headers,
    )
    assert all_matches.json()["meta"] == {
        "page": 2,
        "limit": 2,
        "total_items": 6,
        "total_pages": 3,
    }
    assert {item["status"] for item in completed.json()["data"]} == {"COMPLETED"}
    assert by_job.json()["meta"]["total_items"] == 1
    assert by_resume.json()["meta"]["total_items"] == 1
    lifecycle = {item.status for item in matching_api.matches[:4]}
    assert lifecycle == {"PENDING", "PROCESSING", "COMPLETED", "FAILED"}


@pytest.mark.asyncio
async def test_match_detail_returns_persisted_evidence_and_provenance(
    matching_api: MatchingAPIContext,
) -> None:
    response = await matching_api.client.get(
        f"/api/v1/matching/{matching_api.matches[0].id}", headers=matching_api.headers
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["generation"] == 2
    assert data["resume_revision"] == 1 and data["job_revision"] == 1
    assert data["matched_skills"] == [{"skill_id": 1, "name": "Python"}]
    assert data["missing_skills"] == [{"skill_id": 2, "name": "SQL"}]
    assert data["embedding_model"] == "integration-model"
    assert data["embedding_preprocessing_version"] == "v1"


@pytest.mark.asyncio
async def test_deleted_linked_resources_and_missing_match_are_not_found(
    matching_api: MatchingAPIContext,
) -> None:
    matching_api.actor.role = "ADMIN"
    for match_id in (matching_api.matches[6].id, matching_api.matches[7].id, uuid.uuid4()):
        response = await matching_api.client.get(
            f"/api/v1/matching/{match_id}", headers=matching_api.headers
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "MATCH_NOT_FOUND"


def test_matching_route_table_contains_only_read_endpoints() -> None:
    paths = app.openapi()["paths"]
    assert set(paths["/api/v1/matching"]) == {"get"}
    assert set(paths["/api/v1/matching/{match_id}"]) == {"get"}
