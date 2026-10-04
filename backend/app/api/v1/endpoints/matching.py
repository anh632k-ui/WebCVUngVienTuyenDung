import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.dependencies import CurrentUser, DatabaseSession
from app.schemas.match_schema import (
    MatchDetail,
    MatchResponse,
    MatchStatus,
    MatchSummary,
    PaginatedMatchesResponse,
    PaginationMeta,
)
from app.services.match_service import get_match, list_matches

router = APIRouter(prefix="/matching", tags=["Matching"])


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
