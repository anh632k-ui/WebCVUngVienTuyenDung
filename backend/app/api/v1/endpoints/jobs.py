import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Response, status

from app.api.dependencies import CurrentUser, DatabaseSession, require_roles
from app.models.user import User
from app.schemas.auth_schema import UserRole
from app.schemas.job_schema import (
    JobCreateRequest,
    JobData,
    JobResponse,
    JobStatus,
    JobStatusRequest,
    PaginatedJobsResponse,
    PaginationMeta,
    ParsingStatus,
)
from app.services.job_dispatcher import JobParseDispatcher, get_job_parse_dispatcher
from app.services.job_service import (
    change_job_status,
    create_job,
    get_job,
    list_jobs,
    soft_delete_job,
)

router = APIRouter(prefix="/jobs", tags=["Jobs"])
JobManager = Annotated[User, Depends(require_roles(UserRole.HR, UserRole.ADMIN))]
JobCreator = Annotated[User, Depends(require_roles(UserRole.HR))]
JobDispatcherDependency = Annotated[JobParseDispatcher, Depends(get_job_parse_dispatcher)]


@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_new_job(
    payload: JobCreateRequest,
    session: DatabaseSession,
    current_user: JobCreator,
    dispatcher: JobDispatcherDependency,
    idempotency_key: Annotated[uuid.UUID, Header(alias="Idempotency-Key")],
) -> JobResponse:
    job = await create_job(
        session,
        current_user=current_user,
        idempotency_key=idempotency_key,
        payload=payload,
        dispatcher=dispatcher,
    )
    return JobResponse(data=JobData.model_validate(job))


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


@router.patch("/{id}/status", response_model=JobResponse)
async def update_job_status(
    id: uuid.UUID,
    payload: JobStatusRequest,
    session: DatabaseSession,
    current_user: JobManager,
) -> JobResponse:
    job = await change_job_status(
        session,
        current_user=current_user,
        job_id=id,
        target_status=payload.status,
    )
    return JobResponse(data=JobData.model_validate(job))
