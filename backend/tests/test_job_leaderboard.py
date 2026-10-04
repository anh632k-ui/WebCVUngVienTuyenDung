from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.v1.endpoints import jobs as jobs_endpoint
from app.core.config import Settings, get_settings
from app.core.database import get_db_session
from app.core.exceptions import APIError
from app.core.security import create_access_token
from app.models.match_result import MatchResult
from app.models.resume import CandidateProfile
from app.models.user import User
from main import app

TEST_SECRET = "job-leaderboard-unit-test-jwt-secret"


def make_user(role: str) -> User:
    now = datetime.now(UTC)
    return User(
        id=uuid.uuid4(),
        email="leaderboard@example.com",
        password_hash="not-used",
        full_name="Leaderboard User",
        phone_number=None,
        role=role,
        is_active=True,
        created_at=now,
        updated_at=now,
    )


def make_match(index: int, score: str) -> MatchResult:
    now = datetime.now(UTC)
    return MatchResult(
        id=uuid.UUID(int=index),
        job_id=uuid.UUID(int=100),
        resume_id=uuid.UUID(int=200 + index),
        generation=1,
        resume_revision=1,
        job_revision=1,
        overall_score=Decimal(score),
        skill_score=Decimal("70.00"),
        semantic_score=Decimal("80.00"),
        experience_score=Decimal("75.00"),
        matched_skills=[],
        missing_skills=[],
        gap_analysis_summary=None,
        algorithm_version="hybrid-v1",
        embedding_model="integration-model",
        embedding_preprocessing_version="v1",
        status="COMPLETED",
        error_message=None,
        created_at=now,
        updated_at=now,
        calculated_at=now,
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


@pytest_asyncio.fixture
async def leaderboard_api(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[
    tuple[AsyncClient, User, Settings, list[tuple[MatchResult, CandidateProfile | None]]]
]:
    actor = make_user("HR")
    settings = Settings(_env_file=None, app_env="test", jwt_secret_key=TEST_SECRET)
    rows = [
        (
            make_match(1, "90.00"),
            CandidateProfile(
                resume_id=uuid.UUID(int=201),
                full_name="First Candidate",
                current_title="Engineer",
            ),
        ),
        (make_match(2, "80.00"), None),
        (make_match(3, "70.00"), None),
    ]

    async def fake_list_job_leaderboard(
        _: Any,
        *,
        current_user: User,
        job_id: uuid.UUID,
        page: int,
        limit: int,
        min_score: Decimal | None,
    ) -> tuple[list[tuple[MatchResult, CandidateProfile | None]], int]:
        if job_id != uuid.UUID(int=100) or (
            current_user.role == "HR" and current_user.id == uuid.UUID(int=999)
        ):
            raise APIError(404, "JOB_NOT_FOUND", "Job not found")
        filtered = [row for row in rows if min_score is None or row[0].overall_score >= min_score]
        offset = (page - 1) * limit
        return filtered[offset : offset + limit], len(filtered)

    async def override_session() -> AsyncIterator[AuthSession]:
        yield AuthSession(actor)

    def override_settings() -> Settings:
        return settings

    monkeypatch.setattr(jobs_endpoint, "list_job_leaderboard", fake_list_job_leaderboard)
    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_settings] = override_settings
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, actor, settings, rows
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(get_settings, None)


def auth_headers(user: User, settings: Settings) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id, settings)}"}


@pytest.mark.asyncio
async def test_leaderboard_returns_ranked_paginated_persisted_summaries(
    leaderboard_api: tuple[
        AsyncClient, User, Settings, list[tuple[MatchResult, CandidateProfile | None]]
    ],
) -> None:
    client, actor, settings, rows = leaderboard_api
    initial = [(match.status, match.updated_at) for match, _ in rows]
    response = await client.get(
        f"/api/v1/jobs/{uuid.UUID(int=100)}/leaderboard",
        params={"page": 2, "limit": 1, "min_score": 75},
        headers=auth_headers(actor, settings),
    )
    assert response.status_code == 200
    assert response.json()["meta"] == {
        "page": 2,
        "limit": 1,
        "total_items": 2,
        "total_pages": 2,
    }
    item = response.json()["data"][0]
    assert item["rank"] == 2
    assert item["match"]["overall_score"] == 80.0
    assert item["candidate"] == {
        "resume_id": str(rows[1][0].resume_id),
        "full_name": None,
        "current_title": None,
    }
    assert [(match.status, match.updated_at) for match, _ in rows] == initial


@pytest.mark.asyncio
async def test_leaderboard_auth_role_and_job_scope(
    leaderboard_api: tuple[
        AsyncClient, User, Settings, list[tuple[MatchResult, CandidateProfile | None]]
    ],
) -> None:
    client, actor, settings, _ = leaderboard_api
    path = f"/api/v1/jobs/{uuid.UUID(int=100)}/leaderboard"
    assert (await client.get(path)).status_code == 401

    actor.role = "CANDIDATE"
    denied = await client.get(path, headers=auth_headers(actor, settings))
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "INSUFFICIENT_PERMISSIONS"

    actor.role = "HR"
    actor.id = uuid.UUID(int=999)
    hidden = await client.get(path, headers=auth_headers(actor, settings))
    assert hidden.status_code == 404

    actor.role = "ADMIN"
    allowed = await client.get(path, headers=auth_headers(actor, settings))
    assert allowed.status_code == 200


@pytest.mark.asyncio
async def test_leaderboard_query_validation(
    leaderboard_api: tuple[
        AsyncClient, User, Settings, list[tuple[MatchResult, CandidateProfile | None]]
    ],
) -> None:
    client, actor, settings, _ = leaderboard_api
    path = f"/api/v1/jobs/{uuid.UUID(int=100)}/leaderboard"
    headers = auth_headers(actor, settings)
    for query in ("page=0", "limit=0", "limit=101", "min_score=-1", "min_score=101"):
        assert (await client.get(f"{path}?{query}", headers=headers)).status_code == 422


def test_leaderboard_route_matches_canonical_contract() -> None:
    route = app.openapi()["paths"]["/api/v1/jobs/{id}/leaderboard"]
    assert set(route) == {"get"}
