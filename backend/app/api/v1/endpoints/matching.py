import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.dependencies import CurrentUser, DatabaseSession
from app.schemas.match_schema import (
    ErrorResponse,
    GapAnalysisData,
    GapAnalysisResponse,
    MatchCalculateRequest,
    MatchDetail,
    MatchResponse,
    MatchStatus,
    MatchSummary,
    MatchTriggerData,
    MatchTriggerResponse,
    PaginatedMatchesResponse,
    PaginationMeta,
)
from app.services.match_dispatcher import MatchDispatcher, get_match_dispatcher
from app.services.match_service import calculate_matches, get_gap_analysis, get_match, list_matches

router = APIRouter(prefix="/matching", tags=["Matching"])
MatchDispatcherDependency = Annotated[MatchDispatcher, Depends(get_match_dispatcher)]


@router.post(
    "/calculate",
    response_model=MatchTriggerResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        401: {"model": ErrorResponse, "description": "Unauthorized"},
        403: {"model": ErrorResponse, "description": "Forbidden"},
        404: {"model": ErrorResponse, "description": "Not found or hidden"},
        422: {"model": ErrorResponse, "description": "Validation/precondition error"},
        503: {"model": ErrorResponse, "description": "Task dispatcher unavailable"},
    },
)
async def calculate_matching(
    payload: MatchCalculateRequest,
    session: DatabaseSession,
    current_user: CurrentUser,
    dispatcher: MatchDispatcherDependency,
) -> MatchTriggerResponse:
    match_ids = await calculate_matches(
        session,
        current_user=current_user,
        payload=payload,
        dispatcher=dispatcher,
    )
    return MatchTriggerResponse(
        success=True,
        data=MatchTriggerData(
            job_id=payload.job_id,
            match_ids=match_ids,
            total_matches=len(match_ids),
            status=MatchStatus.PENDING,
        ),
    )


@router.get("", response_model=PaginatedMatchesResponse)
async def read_matches(
    session: DatabaseSession,
    current_user: CurrentUser,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    job_id: Annotated[uuid.UUID | None, Query()] = None,
    resume_id: Annotated[uuid.UUID | None, Query()] = None,
    status: Annotated[MatchStatus | None, Query()] = None,
) -> PaginatedMatchesResponse:
    matches, total_items = await list_matches(
        session,
        current_user=current_user,
        job_id=job_id,
        resume_id=resume_id,
        status=status,
        page=page,
        limit=limit,
    )
    return PaginatedMatchesResponse(
        data=[MatchSummary.model_validate(match) for match in matches],
        meta=PaginationMeta(
            page=page,
            limit=limit,
            total_items=total_items,
            total_pages=(total_items + limit - 1) // limit,
        ),
    )


@router.get("/{match_id}", response_model=MatchResponse)
async def read_match(
    match_id: uuid.UUID,
    session: DatabaseSession,
    current_user: CurrentUser,
) -> MatchResponse:
    match = await get_match(session, current_user=current_user, match_id=match_id)
    return MatchResponse(data=MatchDetail.model_validate(match))


@router.get("/{match_id}/gap-analysis", response_model=GapAnalysisResponse)
async def read_gap_analysis(
    match_id: uuid.UUID,
    session: DatabaseSession,
    current_user: CurrentUser,
) -> GapAnalysisResponse:
    match = await get_gap_analysis(session, current_user=current_user, match_id=match_id)
    assert match.overall_score is not None
    return GapAnalysisResponse(
        data=GapAnalysisData(
            match_id=match.id,
            overall_score=float(match.overall_score),
            skill_score=(float(match.skill_score) if match.skill_score is not None else None),
            semantic_score=(
                float(match.semantic_score) if match.semantic_score is not None else None
            ),
            experience_score=(
                float(match.experience_score) if match.experience_score is not None else None
            ),
            matched_skills=match.matched_skills,
            missing_skills=match.missing_skills,
            recommendation=match.gap_analysis_summary,
            explanation=None,
        )
    )
