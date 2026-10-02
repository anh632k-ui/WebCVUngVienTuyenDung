from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, AsyncTransaction

from app.api.dependencies import get_current_user
from app.core.config import Settings, get_settings
from app.core.database import create_engine, get_db_session
from app.core.security import create_access_token
from app.models.skill import Skill
from app.models.user import User
from main import app

TEST_JWT_SECRET = "skills-postgres-integration-jwt-secret"


@dataclass
class PostgreSQLSkillsHarness:
    client: AsyncClient
    session: AsyncSession
    user: User
    settings: Settings

    @property
    def headers(self) -> dict[str, str]:
        token = create_access_token(self.user.id, self.settings)
        return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def postgres_skills() -> AsyncIterator[PostgreSQLSkillsHarness]:
    configured_settings = Settings()
    if configured_settings.database_url is None:
        pytest.skip("DATABASE_URL is not configured")

    database_settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=configured_settings.database_url,
        database_connect_timeout_seconds=configured_settings.database_connect_timeout_seconds,
    )
    integration_settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=None,
        jwt_secret_key=TEST_JWT_SECRET,
    )
    engine = create_engine(database_settings)
    assert engine is not None

    connection: AsyncConnection | None = None
    try:
        connection = await engine.connect()
    except Exception:  # noqa: BLE001 - do not expose connection details or credentials
        await engine.dispose()
        pytest.fail("Configured PostgreSQL database is unavailable", pytrace=False)

    outer_transaction: AsyncTransaction | None = None
    session: AsyncSession | None = None
    try:
        outer_transaction = await connection.begin()
        await connection.execute(text("SET TRANSACTION READ ONLY"))
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        now = datetime.now(UTC)
        user = User(
            id=uuid.uuid4(),
            email="skills-postgres-reader@example.com",
            password_hash="not-persisted",
            full_name="Skills PostgreSQL Reader",
            phone_number=None,
            role="CANDIDATE",
            is_active=True,
            created_at=now,
            updated_at=now,
        )

        async def override_session() -> AsyncIterator[AsyncSession]:
            assert session is not None
            yield session

        def override_settings() -> Settings:
            return integration_settings

        async def override_current_user() -> User:
            return user

        app.dependency_overrides[get_db_session] = override_session
        app.dependency_overrides[get_settings] = override_settings
        app.dependency_overrides[get_current_user] = override_current_user
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield PostgreSQLSkillsHarness(
                client=client,
                session=session,
                user=user,
                settings=integration_settings,
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(get_settings, None)
        app.dependency_overrides.pop(get_current_user, None)
        if session is not None:
            await session.close()
        if outer_transaction is not None and outer_transaction.is_active:
            await outer_transaction.rollback()
        if connection.in_transaction():
            await connection.rollback()
        await connection.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_real_postgres_skills_filters_and_pagination(
    postgres_skills: PostgreSQLSkillsHarness,
) -> None:
    total_items = await postgres_skills.session.scalar(select(func.count()).select_from(Skill))
    assert total_items is not None and total_items > 0

    default_response = await postgres_skills.client.get(
        "/api/v1/skills", headers=postgres_skills.headers
    )
    assert default_response.status_code == 200
    assert default_response.json()["data"]
    assert default_response.json()["meta"] == {
        "page": 1,
        "limit": 20,
        "total_items": total_items,
        "total_pages": (total_items + 19) // 20,
    }

    hard_count = await postgres_skills.session.scalar(
        select(func.count()).select_from(Skill).where(Skill.skill_kind == "HARD")
    )
    soft_count = await postgres_skills.session.scalar(
        select(func.count()).select_from(Skill).where(Skill.skill_kind == "SOFT")
    )
    assert hard_count is not None and hard_count > 0
    assert soft_count is not None and soft_count > 0

    hard_response = await postgres_skills.client.get(
        "/api/v1/skills",
        params={"skill_kind": "HARD", "limit": 100},
        headers=postgres_skills.headers,
    )
    soft_response = await postgres_skills.client.get(
        "/api/v1/skills",
        params={"skill_kind": "SOFT", "limit": 100},
        headers=postgres_skills.headers,
    )
    assert hard_response.status_code == 200
    assert hard_response.json()["meta"]["total_items"] == hard_count
    assert {skill["skill_kind"] for skill in hard_response.json()["data"]} == {"HARD"}
    assert soft_response.status_code == 200
    assert soft_response.json()["meta"]["total_items"] == soft_count
    assert {skill["skill_kind"] for skill in soft_response.json()["data"]} == {"SOFT"}

    sample = default_response.json()["data"][0]
    keyword_response = await postgres_skills.client.get(
        "/api/v1/skills",
        params={"keyword": sample["normalized_name"]},
        headers=postgres_skills.headers,
    )
    category_response = await postgres_skills.client.get(
        "/api/v1/skills",
        params={"category": sample["category"].swapcase(), "limit": 100},
        headers=postgres_skills.headers,
    )
    assert keyword_response.status_code == 200
    assert any(skill["id"] == sample["id"] for skill in keyword_response.json()["data"])
    assert category_response.status_code == 200
    assert category_response.json()["data"]
    assert all(
        skill["category"].lower() == sample["category"].lower()
        for skill in category_response.json()["data"]
    )

    pagination_response = await postgres_skills.client.get(
        "/api/v1/skills",
        params={"page": 2, "limit": 5},
        headers=postgres_skills.headers,
    )
    assert pagination_response.status_code == 200
    assert pagination_response.json()["meta"] == {
        "page": 2,
        "limit": 5,
        "total_items": total_items,
        "total_pages": (total_items + 4) // 5,
    }
    assert len(pagination_response.json()["data"]) == min(5, max(total_items - 5, 0))
