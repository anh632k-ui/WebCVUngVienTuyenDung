from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.database import get_db_session
from app.core.exceptions import APIError
from app.core.security import decode_access_token
from app.models.user import User
from app.schemas.auth_schema import UserRole

bearer_scheme = HTTPBearer(auto_error=False, scheme_name="BearerAuth", bearerFormat="JWT")

DatabaseSession = Annotated[AsyncSession, Depends(get_db_session)]
ApplicationSettings = Annotated[Settings, Depends(get_settings)]
BearerCredentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]


async def get_current_user(
    session: DatabaseSession,
    settings: ApplicationSettings,
    credentials: BearerCredentials,
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise APIError(401, "AUTHENTICATION_REQUIRED", "Authentication is required")

    user_id = decode_access_token(credentials.credentials, settings)
    if user_id is None:
        raise APIError(401, "INVALID_ACCESS_TOKEN", "Access token is invalid or expired")

    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise APIError(401, "INVALID_ACCESS_TOKEN", "Access token is invalid or expired")
    if not user.is_active:
        raise APIError(403, "ACCOUNT_INACTIVE", "Account is inactive")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
RoleDependency = Callable[..., Coroutine[Any, Any, User]]


def require_roles(*allowed_roles: UserRole) -> RoleDependency:
    """Build a reusable dependency that permits only the supplied canonical roles."""

    allowed = {role.value for role in allowed_roles}

    async def role_dependency(current_user: CurrentUser) -> User:
        if current_user.role not in allowed:
            raise APIError(403, "INSUFFICIENT_PERMISSIONS", "Insufficient permissions")
        return current_user

    return role_dependency
