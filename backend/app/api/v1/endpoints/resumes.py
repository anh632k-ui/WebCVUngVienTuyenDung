import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Header, Query, Response, UploadFile, status

from app.api.dependencies import ApplicationSettings, CurrentUser, DatabaseSession, require_roles
from app.models.user import User
from app.schemas.auth_schema import UserRole
from app.schemas.resume_schema import (
    CandidateProfileData,
    EducationData,
    ExperienceData,
    PaginatedResumesResponse,
    PaginationMeta,
    ParsingStatus,
    ResumeDetailData,
    ResumeDetailResponse,
    ResumeSkillData,
    ResumeStatusData,
    ResumeStatusResponse,
    ResumeSummary,
    ResumeUploadData,
    ResumeUploadResponse,
)
from app.services.resume_dispatcher import ResumeParseDispatcher, get_resume_parse_dispatcher
from app.services.resume_service import (
    MAX_RESUME_FILE_SIZE,
    get_resume,
    get_resume_aggregate,
    list_resumes,
    soft_delete_resume,
    upload_resume,
)
from app.storage.resume_storage import LocalResumeStorage, ResumeStorage

router = APIRouter(prefix="/resumes", tags=["Resumes"])
ResumeUploader = Annotated[User, Depends(require_roles(UserRole.CANDIDATE, UserRole.HR))]


def get_resume_storage(settings: ApplicationSettings) -> ResumeStorage:
    return LocalResumeStorage(settings.resume_storage_root)


ResumeStorageDependency = Annotated[ResumeStorage, Depends(get_resume_storage)]
ResumeDispatcherDependency = Annotated[ResumeParseDispatcher, Depends(get_resume_parse_dispatcher)]


@router.post("/upload", response_model=ResumeUploadResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_resume_upload(
    session: DatabaseSession,
    current_user: ResumeUploader,
    storage: ResumeStorageDependency,
    dispatcher: ResumeDispatcherDependency,
    idempotency_key: Annotated[uuid.UUID, Header(alias="Idempotency-Key")],
    file: Annotated[UploadFile, File()],
) -> ResumeUploadResponse:
    filename = file.filename
    try:
        data = await file.read(MAX_RESUME_FILE_SIZE + 1)
    finally:
        await file.close()
    resume = await upload_resume(
        session,
        current_user=current_user,
        idempotency_key=idempotency_key,
        filename=filename,
        data=data,
        storage=storage,
        dispatcher=dispatcher,
    )
    return ResumeUploadResponse(
        data=ResumeUploadData(
            resume_id=resume.id,
            revision=resume.revision,
            file_name=resume.file_name,
            file_size=resume.file_size,
            parsing_status=ParsingStatus(resume.parsing_status),
        )
    )


@router.get("", response_model=PaginatedResumesResponse)
async def read_resumes(
    session: DatabaseSession,
    current_user: CurrentUser,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    keyword: Annotated[str | None, Query()] = None,
    parsing_status: Annotated[ParsingStatus | None, Query()] = None,
) -> PaginatedResumesResponse:
    resumes, total_items = await list_resumes(
        session,
        current_user=current_user,
        keyword=keyword,
        parsing_status=parsing_status,
        page=page,
        limit=limit,
    )
    return PaginatedResumesResponse(
        data=[ResumeSummary.model_validate(resume) for resume in resumes],
        meta=PaginationMeta(
            page=page,
            limit=limit,
            total_items=total_items,
            total_pages=(total_items + limit - 1) // limit,
        ),
    )


@router.get("/{id}", response_model=ResumeDetailResponse)
async def read_resume(
    id: uuid.UUID,
    session: DatabaseSession,
    current_user: CurrentUser,
) -> ResumeDetailResponse:
    aggregate = await get_resume_aggregate(session, current_user=current_user, resume_id=id)
    return ResumeDetailResponse(
        data=ResumeDetailData(
            resume=ResumeSummary.model_validate(aggregate.resume),
            candidate_profile=(
                CandidateProfileData.model_validate(aggregate.candidate_profile)
                if aggregate.candidate_profile is not None
                else None
            ),
            skills=[ResumeSkillData.model_validate(item) for item in aggregate.skills],
            experiences=[ExperienceData.model_validate(item) for item in aggregate.experiences],
            educations=[EducationData.model_validate(item) for item in aggregate.educations],
        )
    )


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_resume(
    id: uuid.UUID,
    session: DatabaseSession,
    current_user: CurrentUser,
) -> Response:
    await soft_delete_resume(session, current_user=current_user, resume_id=id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{id}/status", response_model=ResumeStatusResponse)
async def read_resume_status(
    id: uuid.UUID,
    session: DatabaseSession,
    current_user: CurrentUser,
) -> ResumeStatusResponse:
    resume = await get_resume(session, current_user=current_user, resume_id=id)
    return ResumeStatusResponse(
        data=ResumeStatusData(
            resume_id=resume.id,
            revision=resume.revision,
            parsing_status=ParsingStatus(resume.parsing_status),
            parsed_at=resume.parsed_at,
            error_message=resume.error_message,
        )
    )
