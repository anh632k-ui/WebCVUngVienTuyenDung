from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import APIError
from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import User
from app.schemas.auth_schema import (
    ChangePasswordRequest,
    LoginData,
    LoginRequest,
    RegisterRequest,
    UserData,
    UserUpdateRequest,
)

# Used only to keep unknown-email and wrong-password checks on the same verification path.
_DUMMY_PASSWORD_HASH = hash_password("authentication-timing-placeholder")


def normalize_email(email: str) -> str:
    return email.lower()


async def find_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(func.lower(User.email) == email))
    return result.scalar_one_or_none()


async def register_user(session: AsyncSession, payload: RegisterRequest) -> User:
    normalized_email = normalize_email(str(payload.email))
    if await find_user_by_email(session, normalized_email) is not None:
        raise APIError(409, "EMAIL_ALREADY_EXISTS", "An account with this email already exists")

    user = User(
        email=normalized_email,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        phone_number=payload.phone_number,
        role=payload.role.value,
        is_active=True,
    )
    session.add(user)
    try:
        await session.flush()
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        constraint_name = getattr(getattr(exc, "orig", None), "constraint_name", None)
        if constraint_name == "uq_users_email_ci" or "uq_users_email_ci" in str(exc):
            raise APIError(
                409,
                "EMAIL_ALREADY_EXISTS",
                "An account with this email already exists",
            ) from exc
        raise
    await session.refresh(user)
    return user


async def authenticate_user(
    session: AsyncSession,
    payload: LoginRequest,
    settings: Settings,
) -> LoginData:
    user = await find_user_by_email(session, normalize_email(str(payload.email)))
    encoded_hash = user.password_hash if user is not None else _DUMMY_PASSWORD_HASH
    password_valid = verify_password(payload.password, encoded_hash)
    if user is None or not password_valid:
        raise APIError(401, "INVALID_CREDENTIALS", "Invalid email or password")
    if not user.is_active:
        raise APIError(403, "ACCOUNT_INACTIVE", "Account is inactive")

    return LoginData(
        access_token=create_access_token(user.id, settings),
        token_type="Bearer",
        expires_in=settings.access_token_expire_minutes * 60,
        user=UserData.model_validate(user),
    )


async def change_user_password(
    session: AsyncSession,
    user: User,
    payload: ChangePasswordRequest,
) -> None:
    if not verify_password(payload.current_password, user.password_hash):
        raise APIError(400, "CURRENT_PASSWORD_INCORRECT", "Current password is incorrect")

    user.password_hash = hash_password(payload.new_password)
    user.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(user)


async def update_user_profile(
    session: AsyncSession,
    user: User,
    payload: UserUpdateRequest,
) -> User:
    if "full_name" in payload.model_fields_set:
        assert payload.full_name is not None
        user.full_name = payload.full_name
    if "phone_number" in payload.model_fields_set:
        user.phone_number = payload.phone_number
    user.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(user)
    return user
