from typing import Annotated

from fastapi import APIRouter, Query

from app.api.dependencies import CurrentUser, DatabaseSession
from app.schemas.skill_schema import (
    PaginatedSkillsResponse,
    PaginationMeta,
    SkillData,
    SkillKind,
)
from app.services.skill_service import list_skills

router = APIRouter(prefix="/skills", tags=["Skills"])


@router.get("", response_model=PaginatedSkillsResponse)
async def get_skills(
    session: DatabaseSession,
    current_user: CurrentUser,
    keyword: Annotated[str | None, Query()] = None,
    skill_kind: Annotated[SkillKind | None, Query()] = None,
    category: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> PaginatedSkillsResponse:
    skills, total_items = await list_skills(
        session,
        keyword=keyword,
        skill_kind=skill_kind,
        category=category,
        page=page,
        limit=limit,
    )
    total_pages = (total_items + limit - 1) // limit
    return PaginatedSkillsResponse(
        data=[SkillData.model_validate(skill) for skill in skills],
        meta=PaginationMeta(
            page=page,
            limit=limit,
            total_items=total_items,
            total_pages=total_pages,
        ),
    )
