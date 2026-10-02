from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.skill import Skill
from app.schemas.skill_schema import SkillKind


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def list_skills(
    session: AsyncSession,
    *,
    keyword: str | None,
    skill_kind: SkillKind | None,
    category: str | None,
    page: int,
    limit: int,
) -> tuple[list[Skill], int]:
    filters = []
    if keyword:
        pattern = f"%{_escape_like(keyword)}%"
        filters.append(
            or_(
                Skill.name.ilike(pattern, escape="\\"),
                Skill.normalized_name.ilike(pattern, escape="\\"),
            )
        )
    if skill_kind is not None:
        filters.append(Skill.skill_kind == skill_kind.value)
    if category:
        filters.append(func.lower(Skill.category) == category.lower())

    count_statement = select(func.count()).select_from(Skill).where(*filters)
    total_items = await session.scalar(count_statement) or 0

    skills_statement = (
        select(Skill)
        .where(*filters)
        .order_by(Skill.id.asc())
        .offset((page - 1) * limit)
        .limit(limit)
    )
    skills = list((await session.scalars(skills_statement)).all())
    return skills, total_items
