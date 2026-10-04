from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import APIError
from app.models.job import JobDescription
from app.models.resume import Resume
from app.models.user import User
from app.schemas.admin_schema import AdminUserPatchRequest
from app.schemas.auth_schema import UserRole


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def list_users(
    session: AsyncSession,
    *,
    role: UserRole | None,
    is_active: bool | None,
    keyword: str | None,
    page: int,
    limit: int,
) -> tuple[list[User], int]:
    filters = []
    if role is not None:
        filters.append(User.role == role.value)
    if is_active is not None:
        filters.append(User.is_active.is_(is_active))
    if keyword:
        pattern = f"%{_escape_like(keyword)}%"
        filters.append(
            or_(
                User.email.ilike(pattern, escape="\\"),
                User.full_name.ilike(pattern, escape="\\"),
            )
        )

    total_items = await session.scalar(select(func.count()).select_from(User).where(*filters)) or 0
    statement = (
        select(User).where(*filters).order_by(User.id.asc()).offset((page - 1) * limit).limit(limit)
    )
    users = list((await session.scalars(statement)).all())
    return users, total_items


async def patch_user(
    session: AsyncSession,
    *,
    actor: User,
    target_id: uuid.UUID,
    payload: AdminUserPatchRequest,
) -> User:
    target = await session.scalar(select(User).where(User.id == target_id).with_for_update())
    if target is None:
        raise APIError(404, "USER_NOT_FOUND", "User not found")

    if target.id == actor.id:
        if payload.is_active is False:
            raise APIError(400, "ADMIN_SELF_LOCK_FORBIDDEN", "Admin cannot deactivate themselves")
        if payload.role is not None and payload.role != UserRole.ADMIN:
            raise APIError(400, "ADMIN_SELF_DEMOTION_FORBIDDEN", "Admin cannot demote themselves")

    if (
        payload.role is not None
        and payload.role.value != target.role
        and {payload.role.value, target.role} == {UserRole.CANDIDATE.value, UserRole.HR.value}
    ):
        owns_resume = await session.scalar(
            select(
                select(Resume.id)
                .where(Resume.owner_user_id == target.id, Resume.is_deleted.is_(False))
                .exists()
            )
        )
        owns_job = await session.scalar(
            select(
                select(JobDescription.id)
                .where(
                    JobDescription.recruiter_id == target.id,
                    JobDescription.is_deleted.is_(False),
                )
                .exists()
            )
        )
        if owns_resume or owns_job:
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
    await session.commit()
    await session.refresh(target)
    return target
