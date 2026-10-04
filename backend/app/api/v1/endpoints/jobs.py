import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.dependencies import CurrentUser, DatabaseSession, require_roles
from app.models.user import User
from app.schemas.auth_schema import UserRole
from app.schemas.job_schema import (
    JobData,
    JobResponse,
    JobStatus,
    PaginatedJobsResponse,
    PaginationMeta,
    ParsingStatus,
)
from app.services.job_service import get_job, list_jobs, soft_delete_job

router = APIRouter(prefix="/jobs", tags=["Jobs"])
JobManager = Annotated[User, Depends(require_roles(UserRole.HR, UserRole.ADMIN))]


@router.get("", response_model=PaginatedJobsResponse)
async def read_jobs(
    session: DatabaseSession,
    current_user: CurrentUser,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    keyword: Annotated[str | None, Query()] = None,
    status: Annotated[JobStatus | None, Query()] = None,
    parsing_status: Annotated[ParsingStatus | None, Query()] = None,
) -> PaginatedJobsResponse:
    jobs, total_items = await list_jobs(
        session,
        current_user=current_user,
        keyword=keyword,
        status=status,
        parsing_status=parsing_status,
        page=page,
        limit=limit,
    )
    return PaginatedJobsResponse(
        data=[JobData.model_validate(job) for job in jobs],
        meta=PaginationMeta(
            page=page,
            limit=limit,
            total_items=total_items,
            total_pages=(total_items + limit - 1) // limit,
        ),
    )


@router.get("/{id}", response_model=JobResponse)
async def read_job(
    id: uuid.UUID,
    session: DatabaseSession,
    current_user: CurrentUser,
) -> JobResponse:
    job = await get_job(session, current_user=current_user, job_id=id)
    return JobResponse(data=JobData.model_validate(job))


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(
    id: uuid.UUID,
    session: DatabaseSession,
    current_user: JobManager,
) -> Response:
    await soft_delete_job(session, current_user=current_user, job_id=id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
