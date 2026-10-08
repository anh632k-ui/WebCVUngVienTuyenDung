import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Response, status

from app.api.dependencies import CurrentUser, DatabaseSession, require_roles
from app.models.user import User
from app.schemas.auth_schema import UserRole
from app.schemas.job_schema import (
    JobCreateRequest,
    JobCriteriaData,
    JobCriteriaRequest,
    JobCriteriaResponse,
    JobData,
    JobResponse,
    JobSkillCriterion,
    JobStatus,
    JobStatusRequest,
    JobUpdateRequest,
    PaginatedJobsResponse,
    PaginationMeta,
    ParsingStatus,
)
from app.services.job_dispatcher import JobParseDispatcher, get_job_parse_dispatcher
from app.services.job_service import (
    JobCriteriaRecord,
    change_job_status,
    create_job,
    get_job,
    get_job_criteria,
    list_jobs,
    soft_delete_job,
    update_job,
    update_job_criteria,
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


@router.put("/{id}", response_model=JobResponse)
async def replace_job_fields(
    id: uuid.UUID,
    payload: JobUpdateRequest,
    session: DatabaseSession,
    current_user: JobManager,
    dispatcher: JobDispatcherDependency,
) -> JobResponse:
    job = await update_job(
        session,
        current_user=current_user,
        job_id=id,
        payload=payload,
        dispatcher=dispatcher,
    )
    return JobResponse(data=JobData.model_validate(job))


def _criteria_response(record: JobCriteriaRecord) -> JobCriteriaResponse:
    job = record.job
    skills = record.skills
    return JobCriteriaResponse(
        data=JobCriteriaData(
            job_id=job.id,
            revision=job.revision,
            min_experience_years=job.min_experience_years,
            education_requirement=job.education_requirement,
            is_criteria_verified=job.is_criteria_verified,
            skills=[
                JobSkillCriterion(
                    skill_id=skill.skill_id,
                    importance=skill.importance,
                    min_years_required=skill.min_years_required,
                )
                for skill in skills
            ],
        )
    )


@router.get("/{id}/criteria", response_model=JobCriteriaResponse)
async def read_job_criteria(
    id: uuid.UUID,
    session: DatabaseSession,
    current_user: JobManager,
) -> JobCriteriaResponse:
    record = await get_job_criteria(session, current_user=current_user, job_id=id)
    return _criteria_response(record)


@router.put("/{id}/criteria", response_model=JobCriteriaResponse)
async def replace_job_criteria(
    id: uuid.UUID,
    payload: JobCriteriaRequest,
    session: DatabaseSession,
    current_user: JobManager,
) -> JobCriteriaResponse:
    record = await update_job_criteria(
        session,
        current_user=current_user,
        job_id=id,
        payload=payload,
    )
    return _criteria_response(record)


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
