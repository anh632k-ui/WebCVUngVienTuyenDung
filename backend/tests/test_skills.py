from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.v1.endpoints import skills as skills_endpoint
from app.core.config import Settings, get_settings
from app.core.database import get_db_session
from app.core.security import create_access_token
from app.models.skill import Skill
from app.models.user import User
from app.schemas.skill_schema import SkillKind
from app.services.skill_service import list_skills
from main import app

TEST_JWT_SECRET = "skills-unit-test-jwt-secret-value"

SKILLS = [
    Skill(id=1, name="Python", normalized_name="python", skill_kind="HARD", category="Language"),
    Skill(id=2, name="SQL", normalized_name="sql", skill_kind="HARD", category="Database"),
    Skill(
        id=3,
        name="PostgreSQL",
        normalized_name="postgresql",
        skill_kind="HARD",
        category="Database",
    ),
    Skill(
        id=4,
        name="Communication",
        normalized_name="communication",
        skill_kind="SOFT",
        category="Communication",
    ),
    Skill(
        id=5,
        name="Problem Solving",
        normalized_name="problem-solving",
        skill_kind="SOFT",
        category="Professional",
    ),
    Skill(
        id=6,
        name="Teamwork",
        normalized_name="teamwork",
        skill_kind="SOFT",
        category="Communication",
    ),
]


class FakeResult:
    def __init__(self, user: User) -> None:
        self.user = user

    def scalar_one_or_none(self) -> User:
        return self.user


class AuthSession:
    def __init__(self, user: User) -> None:
        self.user = user

    async def execute(self, _: Any) -> FakeResult:
        return FakeResult(self.user)


@dataclass
class SkillsAPIContext:
    client: AsyncClient
    user: User
    settings: Settings

    @property
    def headers(self) -> dict[str, str]:
        token = create_access_token(self.user.id, self.settings)
        return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def skills_api(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[SkillsAPIContext]:
    now = datetime.now(UTC)
    user = User(
        id=uuid.uuid4(),
        email="skills-reader@example.com",
        password_hash="unused",
        full_name="Skills Reader",
        phone_number=None,
        role="CANDIDATE",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    session = AuthSession(user)
    settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=None,
        jwt_secret_key=TEST_JWT_SECRET,
    )

    async def override_session() -> AsyncIterator[AuthSession]:
        yield session

    def override_settings() -> Settings:
        return settings

    async def fake_list_skills(
        _: Any,
        *,
        keyword: str | None,
        skill_kind: SkillKind | None,
        category: str | None,
        page: int,
        limit: int,
    ) -> tuple[list[Skill], int]:
        filtered = SKILLS
        if keyword:
            lowered_keyword = keyword.lower()
            filtered = [
                skill
                for skill in filtered
                if lowered_keyword in skill.name.lower()
                or lowered_keyword in skill.normalized_name.lower()
            ]
        if skill_kind is not None:
            filtered = [skill for skill in filtered if skill.skill_kind == skill_kind.value]
        if category:
            filtered = [skill for skill in filtered if skill.category.lower() == category.lower()]
        filtered = sorted(filtered, key=lambda skill: skill.id)
        offset = (page - 1) * limit
        return filtered[offset : offset + limit], len(filtered)

    monkeypatch.setattr(skills_endpoint, "list_skills", fake_list_skills)
    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_settings] = override_settings
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield SkillsAPIContext(client=client, user=user, settings=settings)
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_skills_requires_authentication(skills_api: SkillsAPIContext) -> None:
    response = await skills_api.client.get("/api/v1/skills")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["CANDIDATE", "HR", "ADMIN"])
async def test_all_canonical_roles_can_read_skills(
    skills_api: SkillsAPIContext,
    role: str,
) -> None:
    skills_api.user.role = role
    response = await skills_api.client.get("/api/v1/skills", headers=skills_api.headers)

    assert response.status_code == 200
    assert response.json()["success"] is True


@pytest.mark.asyncio
async def test_default_pagination_and_canonical_shape(skills_api: SkillsAPIContext) -> None:
    response = await skills_api.client.get("/api/v1/skills", headers=skills_api.headers)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"success", "data", "meta"}
    assert body["success"] is True
    assert body["meta"] == {"page": 1, "limit": 20, "total_items": 6, "total_pages": 1}
    assert len(body["data"]) == 6
    assert set(body["data"][0]) == {
        "id",
        "name",
        "normalized_name",
        "skill_kind",
        "category",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["page=0", "limit=0", "limit=101", "skill_kind=hard"])
async def test_query_parameter_validation(skills_api: SkillsAPIContext, query: str) -> None:
    response = await skills_api.client.get(f"/api/v1/skills?{query}", headers=skills_api.headers)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_keyword_filter_checks_name_and_normalized_name(
    skills_api: SkillsAPIContext,
) -> None:
    by_name = await skills_api.client.get(
        "/api/v1/skills?keyword=POSTGRE", headers=skills_api.headers
    )
    by_normalized_name = await skills_api.client.get(
        "/api/v1/skills?keyword=problem-solv", headers=skills_api.headers
    )

    assert [skill["name"] for skill in by_name.json()["data"]] == ["PostgreSQL"]
    assert [skill["name"] for skill in by_normalized_name.json()["data"]] == ["Problem Solving"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("skill_kind", "expected_count"), [("HARD", 3), ("SOFT", 3)])
async def test_skill_kind_filters(
    skills_api: SkillsAPIContext,
    skill_kind: str,
    expected_count: int,
) -> None:
    response = await skills_api.client.get(
        f"/api/v1/skills?skill_kind={skill_kind}", headers=skills_api.headers
    )

    assert response.status_code == 200
    assert len(response.json()["data"]) == expected_count
    assert {skill["skill_kind"] for skill in response.json()["data"]} == {skill_kind}


@pytest.mark.asyncio
async def test_category_filter_is_case_insensitive(skills_api: SkillsAPIContext) -> None:
    response = await skills_api.client.get(
        "/api/v1/skills?category=database", headers=skills_api.headers
    )

    assert response.status_code == 200
    assert [skill["name"] for skill in response.json()["data"]] == ["SQL", "PostgreSQL"]


@pytest.mark.asyncio
async def test_combined_filters(skills_api: SkillsAPIContext) -> None:
    response = await skills_api.client.get(
        "/api/v1/skills?keyword=work&skill_kind=SOFT&category=communication",
        headers=skills_api.headers,
    )

    assert response.status_code == 200
    assert [skill["name"] for skill in response.json()["data"]] == ["Teamwork"]


@pytest.mark.asyncio
async def test_empty_result_has_zero_pagination_metadata(skills_api: SkillsAPIContext) -> None:
    response = await skills_api.client.get(
        "/api/v1/skills?keyword=does-not-exist", headers=skills_api.headers
    )

    assert response.status_code == 200
    assert response.json()["data"] == []
    assert response.json()["meta"] == {
        "page": 1,
        "limit": 20,
        "total_items": 0,
        "total_pages": 0,
    }


@pytest.mark.asyncio
async def test_pagination_metadata_and_page_slice(skills_api: SkillsAPIContext) -> None:
    response = await skills_api.client.get(
        "/api/v1/skills?page=2&limit=2", headers=skills_api.headers
    )

    assert response.status_code == 200
    assert [skill["id"] for skill in response.json()["data"]] == [3, 4]
    assert response.json()["meta"] == {
        "page": 2,
        "limit": 2,
        "total_items": 6,
        "total_pages": 3,
    }


class RecordedScalars:
    def all(self) -> list[Skill]:
        return SKILLS[:2]


class RecordingSession:
    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def scalar(self, statement: Any) -> int:
        self.statements.append(statement)
        return 6

    async def scalars(self, statement: Any) -> RecordedScalars:
        self.statements.append(statement)
        return RecordedScalars()


@pytest.mark.asyncio
async def test_service_builds_count_and_paginated_filtered_selects() -> None:
    session = RecordingSession()

    skills, total = await list_skills(
        session,  # type: ignore[arg-type]
        keyword="post",
        skill_kind=SkillKind.HARD,
        category="database",
        page=2,
        limit=5,
    )

    assert skills == SKILLS[:2]
    assert total == 6
    assert len(session.statements) == 2
    count_sql = str(session.statements[0])
    page_sql = str(session.statements[1])
    assert all(sql.lstrip().startswith("SELECT") for sql in (count_sql, page_sql))
    assert "lower(skills.name) LIKE lower(" in count_sql
    assert "lower(skills.normalized_name) LIKE lower(" in count_sql
    assert "skills.skill_kind" in count_sql
    assert "lower(skills.category)" in count_sql
    assert "ORDER BY skills.id ASC" in page_sql
    assert "LIMIT" in page_sql and "OFFSET" in page_sql


def test_route_table_has_only_read_only_skills_endpoint() -> None:
    skills_operations = app.openapi()["paths"]["/api/v1/skills"]

    assert set(skills_operations) == {"get"}
