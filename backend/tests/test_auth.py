from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings, get_settings
from app.core.database import get_db_session
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.models.user import User
from main import app

TEST_JWT_SECRET = "test-only-jwt-secret-that-is-long-and-random-enough"


class FakeResult:
    def __init__(self, user: User | None) -> None:
        self.user = user

    def scalar_one_or_none(self) -> User | None:
        return self.user


class EmailUniqueViolation(Exception):
    constraint_name = "uq_users_email_ci"


class FakeSession:
    def __init__(self) -> None:
        self.users: dict[uuid.UUID, User] = {}
        self.pending_user: User | None = None
        self.raise_unique_on_flush = False

    async def execute(self, statement: Any) -> FakeResult:
        statement_text = str(statement)
        criterion = tuple(statement._where_criteria)[0]
        value = criterion.right.value
        if "lower(users.email)" in statement_text:
            user = next(
                (item for item in self.users.values() if item.email.lower() == str(value).lower()),
                None,
            )
            return FakeResult(user)
        if "users.id" in statement_text:
            return FakeResult(self.users.get(value))
        raise AssertionError(f"Unexpected statement: {statement_text}")

    def add(self, user: User) -> None:
        self.pending_user = user

    async def flush(self) -> None:
        if self.raise_unique_on_flush:
            raise IntegrityError("INSERT users", {}, EmailUniqueViolation())
        if self.pending_user is None:
            return
        now = datetime.now(UTC)
        self.pending_user.id = uuid.uuid4()
        self.pending_user.created_at = now
        self.pending_user.updated_at = now
        self.users[self.pending_user.id] = self.pending_user

    async def commit(self) -> None:
        self.pending_user = None

    async def rollback(self) -> None:
        self.pending_user = None

    async def refresh(self, _: User) -> None:
        return None

    def seed_user(
        self,
        *,
        email: str = "candidate@example.com",
        password: str = "correct-password",
        role: str = "CANDIDATE",
        is_active: bool = True,
    ) -> User:
        now = datetime.now(UTC)
        user = User(
            id=uuid.uuid4(),
            email=email,
            password_hash=hash_password(password),
            full_name="Test User",
            phone_number=None,
            role=role,
            is_active=is_active,
            created_at=now,
            updated_at=now,
        )
        self.users[user.id] = user
        return user


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        database_url=None,
        jwt_secret_key=TEST_JWT_SECRET,
        jwt_algorithm="HS256",
        access_token_expire_minutes=15,
    )


@pytest_asyncio.fixture
async def client(test_settings: Settings) -> AsyncIterator[tuple[AsyncClient, FakeSession]]:
    session = FakeSession()

    async def override_session() -> AsyncIterator[FakeSession]:
        yield session

    def override_settings() -> Settings:
        return test_settings

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_settings] = override_settings
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client, session
    app.dependency_overrides.clear()


def bearer_token(user: User, settings: Settings, *, expired: bool = False) -> dict[str, str]:
    now = datetime.now(UTC)
    if expired:
        now -= timedelta(minutes=settings.access_token_expire_minutes + 1)
    token = create_access_token(user.id, settings, now=now)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["CANDIDATE", "HR"])
async def test_candidate_and_hr_registration_success(
    client: tuple[AsyncClient, FakeSession], role: str
) -> None:
    test_client, session = client
    response = await test_client.post(
        "/api/v1/auth/register",
        json={
            "email": f"{role.lower()}@example.com",
            "password": "strong-password",
            "full_name": f"{role} User",
            "role": role,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["success"] is True
    assert body["data"]["role"] == role
    stored_user = next(iter(session.users.values()))
    assert stored_user.password_hash != "strong-password"
    assert stored_user.password_hash.startswith("$argon2id$")
    assert verify_password("strong-password", stored_user.password_hash)
    assert "password" not in str(body).lower()


@pytest.mark.asyncio
async def test_registration_normalizes_email(client: tuple[AsyncClient, FakeSession]) -> None:
    test_client, session = client
    response = await test_client.post(
        "/api/v1/auth/register",
        json={
            "email": "Mixed.Case@Example.COM",
            "password": "strong-password",
            "full_name": "Mixed Case",
            "role": "CANDIDATE",
        },
    )

    assert response.status_code == 201
    assert response.json()["data"]["email"] == "mixed.case@example.com"
    assert next(iter(session.users.values())).email == "mixed.case@example.com"


@pytest.mark.asyncio
async def test_admin_self_registration_is_validation_error(
    client: tuple[AsyncClient, FakeSession],
) -> None:
    test_client, _ = client
    response = await test_client.post(
        "/api/v1/auth/register",
        json={
            "email": "admin@example.com",
            "password": "strong-password",
            "full_name": "Admin User",
            "role": "ADMIN",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_validation_error_does_not_echo_password(
    client: tuple[AsyncClient, FakeSession],
) -> None:
    test_client, _ = client
    response = await test_client.post(
        "/api/v1/auth/register",
        json={
            "email": "candidate@example.com",
            "password": "secret7",
            "full_name": "Candidate User",
            "role": "CANDIDATE",
        },
    )

    assert response.status_code == 422
    assert "secret7" not in response.text


@pytest.mark.asyncio
async def test_duplicate_email_is_case_insensitive_conflict(
    client: tuple[AsyncClient, FakeSession],
) -> None:
    test_client, session = client
    session.seed_user(email="existing@example.com")
    response = await test_client.post(
        "/api/v1/auth/register",
        json={
            "email": "EXISTING@EXAMPLE.COM",
            "password": "strong-password",
            "full_name": "Duplicate User",
            "role": "HR",
        },
    )

    assert response.status_code == 409
    assert response.json() == {
        "success": False,
        "error": {
            "code": "EMAIL_ALREADY_EXISTS",
            "message": "An account with this email already exists",
            "details": None,
        },
    }


@pytest.mark.asyncio
async def test_registration_translates_unique_constraint_race(
    client: tuple[AsyncClient, FakeSession],
) -> None:
    test_client, session = client
    session.raise_unique_on_flush = True
    response = await test_client.post(
        "/api/v1/auth/register",
        json={
            "email": "race@example.com",
            "password": "strong-password",
            "full_name": "Race User",
            "role": "CANDIDATE",
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "EMAIL_ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_login_success_uses_normalized_email(
    client: tuple[AsyncClient, FakeSession], test_settings: Settings
) -> None:
    test_client, session = client
    user = session.seed_user(email="login@example.com")
    response = await test_client.post(
        "/api/v1/auth/login",
        json={"email": "LOGIN@EXAMPLE.COM", "password": "correct-password"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["token_type"] == "Bearer"
    assert data["expires_in"] == 900
    assert data["user"]["id"] == str(user.id)
    assert decode_access_token(data["access_token"], test_settings) == user.id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("email", "password"),
    [
        ("candidate@example.com", "wrong-password"),
        ("unknown@example.com", "wrong-password"),
    ],
)
async def test_login_rejects_bad_credentials_without_field_disclosure(
    client: tuple[AsyncClient, FakeSession], email: str, password: str
) -> None:
    test_client, session = client
    session.seed_user()
    response = await test_client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )

    assert response.status_code == 401
    assert response.json()["error"] == {
        "code": "INVALID_CREDENTIALS",
        "message": "Invalid email or password",
        "details": None,
    }


@pytest.mark.asyncio
async def test_login_rejects_inactive_account(client: tuple[AsyncClient, FakeSession]) -> None:
    test_client, session = client
    session.seed_user(is_active=False)
    response = await test_client.post(
        "/api/v1/auth/login",
        json={"email": "candidate@example.com", "password": "correct-password"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ACCOUNT_INACTIVE"


def test_jwt_creation_validation_and_expiration(test_settings: Settings) -> None:
    user_id = uuid.uuid4()
    token = create_access_token(user_id, test_settings)
    expired_token = create_access_token(
        user_id,
        test_settings,
        now=datetime.now(UTC) - timedelta(minutes=16),
    )

    assert decode_access_token(token, test_settings) == user_id
    assert decode_access_token(expired_token, test_settings) is None
    assert decode_access_token("not-a-jwt", test_settings) is None


@pytest.mark.asyncio
async def test_protected_endpoint_requires_valid_token(
    client: tuple[AsyncClient, FakeSession], test_settings: Settings
) -> None:
    test_client, session = client
    user = session.seed_user()

    missing = await test_client.get("/api/v1/users/me")
    invalid = await test_client.get(
        "/api/v1/users/me", headers={"Authorization": "Bearer invalid-token"}
    )
    expired = await test_client.get(
        "/api/v1/users/me", headers=bearer_token(user, test_settings, expired=True)
    )

    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
    assert invalid.status_code == 401
    assert invalid.json()["error"]["code"] == "INVALID_ACCESS_TOKEN"
    assert expired.status_code == 401
    assert expired.json()["error"]["code"] == "INVALID_ACCESS_TOKEN"


@pytest.mark.asyncio
async def test_inactive_user_cannot_call_protected_endpoint(
    client: tuple[AsyncClient, FakeSession], test_settings: Settings
) -> None:
    test_client, session = client
    user = session.seed_user(is_active=False)
    response = await test_client.get("/api/v1/users/me", headers=bearer_token(user, test_settings))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ACCOUNT_INACTIVE"


@pytest.mark.asyncio
async def test_get_and_update_current_user_only_allow_editable_fields(
    client: tuple[AsyncClient, FakeSession], test_settings: Settings
) -> None:
    test_client, session = client
    user = session.seed_user()
    headers = bearer_token(user, test_settings)

    get_response = await test_client.get("/api/v1/users/me", headers=headers)
    update_response = await test_client.put(
        "/api/v1/users/me",
        headers=headers,
        json={"full_name": "Updated Name", "phone_number": "+84123456789"},
    )
    forbidden_response = await test_client.put(
        "/api/v1/users/me",
        headers=headers,
        json={"role": "ADMIN", "email": "changed@example.com"},
    )

    assert get_response.status_code == 200
    assert get_response.json()["data"]["id"] == str(user.id)
    assert update_response.status_code == 200
    assert update_response.json()["data"]["full_name"] == "Updated Name"
    assert update_response.json()["data"]["phone_number"] == "+84123456789"
    assert forbidden_response.status_code == 422
    assert user.role == "CANDIDATE"
    assert user.email == "candidate@example.com"


@pytest.mark.asyncio
async def test_change_password_rejects_wrong_current_password(
    client: tuple[AsyncClient, FakeSession], test_settings: Settings
) -> None:
    test_client, session = client
    user = session.seed_user()
    response = await test_client.put(
        "/api/v1/auth/change-password",
        headers=bearer_token(user, test_settings),
        json={"current_password": "wrong-password", "new_password": "new-password"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "CURRENT_PASSWORD_INCORRECT"
    assert verify_password("correct-password", user.password_hash)


@pytest.mark.asyncio
async def test_change_password_replaces_old_login_with_new_login(
    client: tuple[AsyncClient, FakeSession], test_settings: Settings
) -> None:
    test_client, session = client
    user = session.seed_user()
    headers = bearer_token(user, test_settings)
    change_response = await test_client.put(
        "/api/v1/auth/change-password",
        headers=headers,
        json={"current_password": "correct-password", "new_password": "new-password"},
    )
    old_login = await test_client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "correct-password"},
    )
    new_login = await test_client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "new-password"},
    )

    assert change_response.status_code == 200
    assert change_response.json() == {
        "success": True,
        "message": "Password changed successfully",
    }
    assert old_login.status_code == 401
    assert new_login.status_code == 200
    assert verify_password("new-password", user.password_hash)


def test_route_table_contains_only_intended_auth_and_user_endpoints() -> None:
    specification = app.openapi()
    paths = specification["paths"]
    scoped_routes = {
        (method.upper(), path)
        for path, operations in paths.items()
        if path.startswith("/api/v1/auth") or path.startswith("/api/v1/users")
        for method in operations
    }

    assert scoped_routes == {
        ("POST", "/api/v1/auth/register"),
        ("POST", "/api/v1/auth/login"),
        ("PUT", "/api/v1/auth/change-password"),
        ("GET", "/api/v1/users/me"),
        ("PUT", "/api/v1/users/me"),
    }
    assert specification["components"]["securitySchemes"] == {
        "BearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
    }
    assert "security" not in paths["/api/v1/auth/register"]["post"]
    assert "security" not in paths["/api/v1/auth/login"]["post"]
    assert paths["/api/v1/users/me"]["get"]["security"] == [{"BearerAuth": []}]
