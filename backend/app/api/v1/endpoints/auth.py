from fastapi import APIRouter, status

from app.api.dependencies import ApplicationSettings, CurrentUser, DatabaseSession
from app.schemas.auth_schema import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    MessageResponse,
    RegisterRequest,
    UserData,
    UserResponse,
)
from app.services.auth_service import authenticate_user, change_user_password, register_user

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, session: DatabaseSession) -> UserResponse:
    user = await register_user(session, payload)
    return UserResponse(data=UserData.model_validate(user))


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> LoginResponse:
    login_data = await authenticate_user(session, payload, settings)
    return LoginResponse(data=login_data)


@router.put("/change-password", response_model=MessageResponse)
async def change_password(
    payload: ChangePasswordRequest,
    session: DatabaseSession,
    current_user: CurrentUser,
) -> MessageResponse:
    await change_user_password(session, current_user, payload)
    return MessageResponse(message="Password changed successfully")
