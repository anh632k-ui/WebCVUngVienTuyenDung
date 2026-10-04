from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.api.v1.endpoints import admin as admin_endpoint
from app.core.config import Settings, get_settings
from app.core.database import get_db_session
from app.core.exceptions import APIError
from app.core.security import create_access_token
from app.models.user import User
from app.schemas.admin_schema import AdminUserPatchRequest
from app.schemas.auth_schema import UserRole
from main import app

TEST_SECRET = "admin-users-unit-test-jwt-secret-value"


def make_user(role: str, index: int, *, is_active: bool = True) -> User:
    now = datetime.now(UTC)
    return User(
        id=uuid.uuid5(uuid.NAMESPACE_URL, f"admin-test-user-{index}"),
        email=f"user{index}@example.com",
        password_hash="not-returned",
        full_name=f"User {index}",
        phone_number=None,
        role=role,
        is_active=is_active,
        created_at=now,
        updated_at=now,
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
class AdminAPIContext:
    client: AsyncClient
    actor: User
    settings: Settings
    users: dict[uuid.UUID, User]
    active_resume_owners: set[uuid.UUID] = field(default_factory=set)
    active_job_owners: set[uuid.UUID] = field(default_factory=set)

    @property
    def headers(self) -> dict[str, str]:
        token = create_access_token(self.actor.id, self.settings)
        return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def admin_api(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AdminAPIContext]:
    actor = make_user("ADMIN", 0)
    all_users = [
        actor,
        make_user("CANDIDATE", 1),
        make_user("HR", 2),
        make_user("CANDIDATE", 3, is_active=False),
        make_user("ADMIN", 4),
    ]
    users = {user.id: user for user in all_users}
    settings = Settings(_env_file=None, app_env="test", jwt_secret_key=TEST_SECRET)
    session = AuthSession(actor)
    context: AdminAPIContext | None = None

    async def override_session() -> AsyncIterator[AuthSession]:
        yield session

    def override_settings() -> Settings:
        return settings

    async def fake_list_users(
        _: Any,
        *,
        role: UserRole | None,
        is_active: bool | None,
        keyword: str | None,
        page: int,
        limit: int,
    ) -> tuple[list[User], int]:
        assert context is not None
        result = list(context.users.values())
        if role is not None:
            result = [user for user in result if user.role == role.value]
        if is_active is not None:
            result = [user for user in result if user.is_active is is_active]
        if keyword:
            lowered = keyword.lower()
            result = [
                user
                for user in result
                if lowered in user.email.lower() or lowered in user.full_name.lower()
            ]
        result.sort(key=lambda user: user.id)
        offset = (page - 1) * limit
        return result[offset : offset + limit], len(result)

    async def fake_patch_user(
        _: Any,
        *,
        actor: User,
        target_id: uuid.UUID,
        payload: AdminUserPatchRequest,
    ) -> User:
        assert context is not None
        target = context.users.get(target_id)
        if target is None:
            raise APIError(404, "USER_NOT_FOUND", "User not found")
        if target.id == actor.id and payload.is_active is False:
            raise APIError(400, "ADMIN_SELF_LOCK_FORBIDDEN", "Admin cannot deactivate themselves")
        if target.id == actor.id and payload.role not in {None, UserRole.ADMIN}:
            raise APIError(400, "ADMIN_SELF_DEMOTION_FORBIDDEN", "Admin cannot demote themselves")
        if (
            payload.role is not None
            and payload.role.value != target.role
            and {payload.role.value, target.role} == {"CANDIDATE", "HR"}
            and (
                target.id in context.active_resume_owners or target.id in context.active_job_owners
            )
        ):
            raise APIError(
                409,
                "ROLE_CHANGE_CONFLICT",
                "User role cannot change while active Resume or Job resources exist",
            )
        if "is_active" in payload.model_fields_set:
            assert payload.is_active is not None
            target.is_active = payload.is_active
        if "role" in payload.model_fields_set:
            assert payload.role is not None
            target.role = payload.role.value
        target.updated_at = datetime.now(UTC)
        return target

    monkeypatch.setattr(admin_endpoint, "list_users", fake_list_users)
    monkeypatch.setattr(admin_endpoint, "patch_user", fake_patch_user)
    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_settings] = override_settings
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        context = AdminAPIContext(client=client, actor=actor, settings=settings, users=users)
        yield context
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_admin_list_requires_authentication(admin_api: AdminAPIContext) -> None:
    response = await admin_api.client.get("/api/v1/admin/users")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["CANDIDATE", "HR"])
async def test_non_admin_cannot_list_users(admin_api: AdminAPIContext, role: str) -> None:
    admin_api.actor.role = role
    response = await admin_api.client.get("/api/v1/admin/users", headers=admin_api.headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "INSUFFICIENT_PERMISSIONS"


@pytest.mark.asyncio
async def test_admin_list_response_and_default_pagination(admin_api: AdminAPIContext) -> None:
    response = await admin_api.client.get("/api/v1/admin/users", headers=admin_api.headers)
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["meta"] == {"page": 1, "limit": 20, "total_items": 5, "total_pages": 1}
    assert "password_hash" not in response.text
    assert set(body["data"][0]) == {
        "id",
        "email",
        "full_name",
        "phone_number",
        "role",
        "is_active",
        "created_at",
        "updated_at",
    }


@pytest.mark.asyncio
async def test_admin_list_filters_and_pagination(admin_api: AdminAPIContext) -> None:
    role = await admin_api.client.get(
        "/api/v1/admin/users?role=CANDIDATE", headers=admin_api.headers
    )
    inactive = await admin_api.client.get(
        "/api/v1/admin/users?is_active=false", headers=admin_api.headers
    )
    keyword = await admin_api.client.get(
        "/api/v1/admin/users?keyword=USER 2", headers=admin_api.headers
    )
    page = await admin_api.client.get(
        "/api/v1/admin/users?page=2&limit=2", headers=admin_api.headers
    )
    empty = await admin_api.client.get(
        "/api/v1/admin/users?keyword=missing", headers=admin_api.headers
    )
    assert {user["role"] for user in role.json()["data"]} == {"CANDIDATE"}
    assert [user["is_active"] for user in inactive.json()["data"]] == [False]
    assert [user["full_name"] for user in keyword.json()["data"]] == ["User 2"]
    assert page.json()["meta"] == {"page": 2, "limit": 2, "total_items": 5, "total_pages": 3}
    assert len(page.json()["data"]) == 2
    assert empty.json()["data"] == []
    assert empty.json()["meta"]["total_pages"] == 0


@pytest.mark.asyncio
async def test_admin_can_change_active_state_and_candidate_hr_roles(
    admin_api: AdminAPIContext,
) -> None:
    candidate = next(
        user for user in admin_api.users.values() if user.role == "CANDIDATE" and user.is_active
    )
    hr = next(user for user in admin_api.users.values() if user.role == "HR")
    deactivate = await admin_api.client.patch(
        f"/api/v1/admin/users/{candidate.id}", headers=admin_api.headers, json={"is_active": False}
    )
    candidate_to_hr = await admin_api.client.patch(
        f"/api/v1/admin/users/{candidate.id}", headers=admin_api.headers, json={"role": "HR"}
    )
    hr_to_candidate = await admin_api.client.patch(
        f"/api/v1/admin/users/{hr.id}", headers=admin_api.headers, json={"role": "CANDIDATE"}
    )
    assert deactivate.status_code == 200 and deactivate.json()["data"]["is_active"] is False
    assert candidate_to_hr.status_code == 200 and candidate_to_hr.json()["data"]["role"] == "HR"
    assert hr_to_candidate.status_code == 200
    assert hr_to_candidate.json()["data"]["role"] == "CANDIDATE"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"is_active": False}, "ADMIN_SELF_LOCK_FORBIDDEN"),
        ({"role": "HR"}, "ADMIN_SELF_DEMOTION_FORBIDDEN"),
    ],
)
async def test_admin_cannot_lock_or_demote_self(
    admin_api: AdminAPIContext, payload: dict[str, Any], code: str
) -> None:
    response = await admin_api.client.patch(
        f"/api/v1/admin/users/{admin_api.actor.id}", headers=admin_api.headers, json=payload
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == code


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["resume", "job"])
async def test_active_resources_block_candidate_hr_transition(
    admin_api: AdminAPIContext, resource: str
) -> None:
    target = next(
        user for user in admin_api.users.values() if user.role == "CANDIDATE" and user.is_active
    )
    owners = admin_api.active_resume_owners if resource == "resume" else admin_api.active_job_owners
    owners.add(target.id)
    response = await admin_api.client.patch(
        f"/api/v1/admin/users/{target.id}", headers=admin_api.headers, json={"role": "HR"}
    )
    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "ROLE_CHANGE_CONFLICT",
        "message": "User role cannot change while active Resume or Job resources exist",
        "details": None,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["resume", "job"])
async def test_soft_deleted_resources_do_not_block_transition(
    admin_api: AdminAPIContext, resource: str
) -> None:
    target = next(
        user for user in admin_api.users.values() if user.role == "CANDIDATE" and user.is_active
    )
    # A soft-deleted resource is deliberately absent from the active-owner sets.
    response = await admin_api.client.patch(
        f"/api/v1/admin/users/{target.id}", headers=admin_api.headers, json={"role": "HR"}
    )
    assert response.status_code == 200
    assert response.json()["data"]["role"] == "HR"


@pytest.mark.asyncio
async def test_patch_nonexistent_user_and_invalid_payload(admin_api: AdminAPIContext) -> None:
    missing = await admin_api.client.patch(
        f"/api/v1/admin/users/{uuid.uuid4()}", headers=admin_api.headers, json={"is_active": False}
    )
    empty = await admin_api.client.patch(
        f"/api/v1/admin/users/{admin_api.actor.id}", headers=admin_api.headers, json={}
    )
    extra = await admin_api.client.patch(
        f"/api/v1/admin/users/{admin_api.actor.id}",
        headers=admin_api.headers,
        json={"email": "x@y.com"},
    )
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "USER_NOT_FOUND"
    assert empty.status_code == 422 and empty.json()["error"]["code"] == "VALIDATION_ERROR"
    assert extra.status_code == 422 and extra.json()["error"]["code"] == "VALIDATION_ERROR"


def test_admin_route_table_contains_only_canonical_methods() -> None:
    paths = app.openapi()["paths"]
    assert set(paths["/api/v1/admin/users"]) == {"get"}
    assert set(paths["/api/v1/admin/users/{id}"]) == {"patch"}
