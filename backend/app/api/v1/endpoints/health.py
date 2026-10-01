from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from app.core.database import check_database_connection

router = APIRouter(tags=["Health"])


class DatabaseHealth(BaseModel):
    status: Literal["connected", "not_configured", "unavailable"]


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: DatabaseHealth


@router.get("/health", response_model=HealthResponse)
async def health(response: Response) -> HealthResponse:
    connected, database_status = await check_database_connection()
    if database_status == "unavailable":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status="ok" if connected else "degraded",
        database=DatabaseHealth(status=database_status),
    )
