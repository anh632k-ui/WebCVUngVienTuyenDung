from fastapi import APIRouter

from app.api.dependencies import CurrentUser, DatabaseSession
from app.schemas.auth_schema import UserData, UserResponse, UserUpdateRequest
from app.services.auth_service import update_user_profile

router = APIRouter(prefix="/users", tags=["Users"])


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: CurrentUser) -> UserResponse:
    return UserResponse(data=UserData.model_validate(current_user))


@router.put("/me", response_model=UserResponse)
async def update_me(
    payload: UserUpdateRequest,
    session: DatabaseSession,
    current_user: CurrentUser,
) -> UserResponse:
    user = await update_user_profile(session, current_user, payload)
    return UserResponse(data=UserData.model_validate(user))
