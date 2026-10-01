from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, AsyncTransaction

from app.core.config import Settings, get_settings
from app.core.database import create_engine, get_db_session
from app.core.security import decode_access_token, verify_password
from app.models.user import User
from app.services import auth_service
from main import app

TEST_JWT_SECRET = "postgres-integration-only-jwt-secret-value"


@dataclass
class PostgreSQLAuthHarness:
    client: AsyncClient
    session: AsyncSession
    connection: AsyncConnection
    outer_transaction: AsyncTransaction
    settings: Settings
    tracked_emails: set[str] = field(default_factory=set)

    def track_email(self, email: str) -> str:
        normalized_email = email.lower()
        self.tracked_emails.add(normalized_email)
        return normalized_email


@pytest_asyncio.fixture
async def postgres_auth() -> AsyncIterator[PostgreSQLAuthHarness]:
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
        jwt_algorithm="HS256",
        access_token_expire_minutes=15,
    )
    engine = create_engine(database_settings)
    assert engine is not None

    connection: AsyncConnection | None = None
    try:
        connection = await engine.connect()
    except Exception:  # noqa: BLE001 - do not expose connection details or credentials
        await engine.dispose()
        pytest.fail("Configured PostgreSQL database is unavailable", pytrace=False)

    outer_transaction = await connection.begin()
    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield session

    def override_settings() -> Settings:
        return integration_settings

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_settings] = override_settings
    harness: PostgreSQLAuthHarness | None = None
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            harness = PostgreSQLAuthHarness(
                client=client,
                session=session,
                connection=connection,
                outer_transaction=outer_transaction,
                settings=integration_settings,
            )
            yield harness
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(get_settings, None)
        remaining_users: int | None = None
        try:
            await session.close()
            if outer_transaction.is_active:
                await outer_transaction.rollback()

            if harness is not None and harness.tracked_emails:
                remaining_users = await connection.scalar(
                    select(func.count())
                    .select_from(User)
                    .where(func.lower(User.email).in_(harness.tracked_emails))
                )
        finally:
            if connection.in_transaction():
                await connection.rollback()
            await connection.close()
            await engine.dispose()

        assert remaining_users in {None, 0}, "Outer rollback left an integration-test user behind"


@pytest.mark.asyncio
async def test_real_postgres_auth_account_lifecycle(
    postgres_auth: PostgreSQLAuthHarness,
) -> None:
    unique_part = uuid.uuid4().hex
    mixed_case_email = f"Auth.Integration.{unique_part}@Example.COM"
    normalized_email = postgres_auth.track_email(mixed_case_email)
    original_password = "initial-password"
    new_password = "replacement-password"

    registration = await postgres_auth.client.post(
        "/api/v1/auth/register",
        json={
            "email": mixed_case_email,
            "password": original_password,
            "full_name": "PostgreSQL Candidate",
            "role": "CANDIDATE",
        },
    )

    assert registration.status_code == 201
    assert postgres_auth.outer_transaction.is_active
    registered_user_id = uuid.UUID(registration.json()["data"]["id"])
    stored_result = await postgres_auth.session.execute(
        text(
            "SELECT email, password_hash, full_name, phone_number, role, is_active "
            "FROM users WHERE id = :user_id"
        ),
        {"user_id": registered_user_id},
    )
    stored_user = stored_result.mappings().one()
    original_hash = stored_user["password_hash"]
    assert stored_user["email"] == normalized_email
    assert original_hash != original_password
    assert original_hash.startswith("$argon2id$")
    assert verify_password(original_password, original_hash)

    login = await postgres_auth.client.post(
        "/api/v1/auth/login",
        json={"email": normalized_email.upper(), "password": original_password},
    )

    assert login.status_code == 200
    access_token = login.json()["data"]["access_token"]
    assert decode_access_token(access_token, postgres_auth.settings) == registered_user_id
    headers = {"Authorization": f"Bearer {access_token}"}

    profile = await postgres_auth.client.get("/api/v1/users/me", headers=headers)
    assert profile.status_code == 200
    assert profile.json()["data"]["id"] == str(registered_user_id)

    update = await postgres_auth.client.put(
        "/api/v1/users/me",
        headers=headers,
        json={"full_name": "Updated PostgreSQL Candidate", "phone_number": "+84123456789"},
    )

    assert update.status_code == 200
    updated_result = await postgres_auth.session.execute(
        text(
            "SELECT email, password_hash, full_name, phone_number, role, is_active "
            "FROM users WHERE id = :user_id"
        ),
        {"user_id": registered_user_id},
    )
    updated_user = updated_result.mappings().one()
    assert updated_user["full_name"] == "Updated PostgreSQL Candidate"
    assert updated_user["phone_number"] == "+84123456789"
    assert updated_user["email"] == normalized_email
    assert updated_user["role"] == "CANDIDATE"
    assert updated_user["is_active"] is True
    assert updated_user["password_hash"] == original_hash

    password_change = await postgres_auth.client.put(
        "/api/v1/auth/change-password",
        headers=headers,
        json={"current_password": original_password, "new_password": new_password},
    )

    assert password_change.status_code == 200
    replacement_hash = await postgres_auth.session.scalar(
        select(User.password_hash).where(User.id == registered_user_id)
    )
    assert replacement_hash is not None
    assert replacement_hash != original_hash
    assert replacement_hash != new_password
    assert replacement_hash.startswith("$argon2id$")
    assert verify_password(new_password, replacement_hash)

    old_login = await postgres_auth.client.post(
        "/api/v1/auth/login",
        json={"email": normalized_email, "password": original_password},
    )
    new_login = await postgres_auth.client.post(
        "/api/v1/auth/login",
        json={"email": normalized_email, "password": new_password},
    )

    assert old_login.status_code == 401
    assert old_login.json()["error"]["code"] == "INVALID_CREDENTIALS"
    assert new_login.status_code == 200
    assert postgres_auth.outer_transaction.is_active


@pytest.mark.asyncio
async def test_real_postgres_case_insensitive_unique_constraint_returns_409(
    postgres_auth: PostgreSQLAuthHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unique_part = uuid.uuid4().hex
    original_email = f"auth.constraint.{unique_part}@example.com"
    postgres_auth.track_email(original_email)
    payload = {
        "email": original_email,
        "password": "constraint-password",
        "full_name": "Constraint Candidate",
        "role": "CANDIDATE",
    }
    first_registration = await postgres_auth.client.post("/api/v1/auth/register", json=payload)
    assert first_registration.status_code == 201

    async def simulate_concurrent_precheck_miss(_: AsyncSession, __: str) -> User | None:
        return None

    # Simulate a concurrent precheck miss so the second INSERT reaches the real unique index.
    monkeypatch.setattr(auth_service, "find_user_by_email", simulate_concurrent_precheck_miss)
    duplicate_registration = await postgres_auth.client.post(
        "/api/v1/auth/register",
        json={**payload, "email": original_email.upper()},
    )

    assert duplicate_registration.status_code == 409
    assert duplicate_registration.json() == {
        "success": False,
        "error": {
            "code": "EMAIL_ALREADY_EXISTS",
            "message": "An account with this email already exists",
            "details": None,
        },
    }
    row_count = await postgres_auth.session.scalar(
        select(func.count())
        .select_from(User)
        .where(func.lower(User.email) == original_email.lower())
    )
    assert row_count == 1
    assert postgres_auth.outer_transaction.is_active
