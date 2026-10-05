import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import DatabaseSession, require_roles
from app.models.user import User
from app.schemas.auth_schema import UserRole
from app.schemas.job_schema import (
    CandidateSummary,
    LeaderboardItem,
    LeaderboardResponse,
    PaginationMeta,
)
from app.schemas.match_schema import MatchSummary
from app.services.job_service import list_job_leaderboard

router = APIRouter(prefix="/jobs", tags=["Matching"])
JobLeaderboardReader = Annotated[User, Depends(require_roles(UserRole.HR, UserRole.ADMIN))]


@router.get("/{id}/leaderboard", response_model=LeaderboardResponse)
async def read_job_leaderboard(
    id: uuid.UUID,
    session: DatabaseSession,
    current_user: JobLeaderboardReader,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    min_score: Annotated[Decimal | None, Query(ge=0, le=100)] = None,
) -> LeaderboardResponse:
    rows, total_items = await list_job_leaderboard(
        session,
        current_user=current_user,
        job_id=id,
        page=page,
        limit=limit,
        min_score=min_score,
    )
    offset = (page - 1) * limit
    return LeaderboardResponse(
        data=[
            LeaderboardItem(
                rank=offset + index,
                match=MatchSummary.model_validate(match),
                candidate=CandidateSummary(
                    resume_id=match.resume_id,
                    full_name=profile.full_name if profile is not None else None,
                    current_title=(profile.current_title if profile is not None else None),
                ),
            )
            for index, (match, profile) in enumerate(rows, start=1)
        ],
        meta=PaginationMeta(
            page=page,
            limit=limit,
            total_items=total_items,
            total_pages=(total_items + limit - 1) // limit,
        ),
    )
