from fastapi import APIRouter

from app.api.v1.endpoints.admin import router as admin_router
from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.health import router as health_router
from app.api.v1.endpoints.jobs import router as jobs_router
from app.api.v1.endpoints.leaderboard import router as leaderboard_router
from app.api.v1.endpoints.matching import router as matching_router
from app.api.v1.endpoints.resumes import router as resumes_router
from app.api.v1.endpoints.skills import router as skills_router
from app.api.v1.endpoints.users import router as users_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(users_router)
api_router.include_router(skills_router)
api_router.include_router(admin_router)
api_router.include_router(resumes_router)
api_router.include_router(jobs_router)
api_router.include_router(matching_router)
api_router.include_router(leaderboard_router)
