import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import DatabaseSession, require_roles
from app.models.user import User
from app.schemas.admin_schema import AdminUserPatchRequest, PaginatedUsersResponse, PaginationMeta
from app.schemas.auth_schema import UserData, UserResponse, UserRole
from app.services.admin_service import list_users, patch_user

router = APIRouter(prefix="/admin", tags=["Admin"])
AdminUser = Annotated[User, Depends(require_roles(UserRole.ADMIN))]


@router.get("/users", response_model=PaginatedUsersResponse)
async def get_users(
    session: DatabaseSession,
    admin_user: AdminUser,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    role: Annotated[UserRole | None, Query()] = None,
    is_active: Annotated[bool | None, Query()] = None,
    keyword: Annotated[str | None, Query()] = None,
) -> PaginatedUsersResponse:
    users, total_items = await list_users(
        session,
        role=role,
        is_active=is_active,
        keyword=keyword,
        page=page,
        limit=limit,
    )
    return PaginatedUsersResponse(
        data=[UserData.model_validate(user) for user in users],
        meta=PaginationMeta(
            page=page,
            limit=limit,
            total_items=total_items,
            total_pages=(total_items + limit - 1) // limit,
        ),
    )


@router.patch("/users/{id}", response_model=UserResponse)
async def update_user(
    id: uuid.UUID,
    payload: AdminUserPatchRequest,
    session: DatabaseSession,
    admin_user: AdminUser,
) -> UserResponse:
    user = await patch_user(session, actor=admin_user, target_id=id, payload=payload)
    return UserResponse(data=UserData.model_validate(user))
