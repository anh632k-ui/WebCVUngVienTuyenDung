from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import JobDescription
from app.models.match_result import MatchResult
from app.models.resume import Resume


async def claim_resume_revision(
    session: AsyncSession,
    *,
    resume_id: uuid.UUID,
    expected_revision: int,
) -> bool:
    result = await session.execute(
        update(Resume)
        .where(
            Resume.id == resume_id,
            Resume.revision == expected_revision,
            Resume.parsing_status == "PENDING",
            Resume.is_deleted.is_(False),
        )
        .values(
            parsing_status="PROCESSING",
            error_message=None,
            updated_at=datetime.now(UTC),
        )
        .returning(Resume.id)
    )
    claimed = result.scalar_one_or_none() is not None
    if claimed:
        await session.commit()
    else:
        await session.rollback()
    return claimed


async def fail_resume_revision(
    session: AsyncSession,
    *,
    resume_id: uuid.UUID,
    expected_revision: int,
    error_message: str,
) -> bool:
    result = await session.execute(
        update(Resume)
        .where(
            Resume.id == resume_id,
            Resume.revision == expected_revision,
            Resume.parsing_status == "PROCESSING",
            Resume.is_deleted.is_(False),
        )
        .values(
            parsing_status="FAILED",
            error_message=error_message,
            updated_at=datetime.now(UTC),
        )
        .returning(Resume.id)
    )
    failed = result.scalar_one_or_none() is not None
    if failed:
        await session.commit()
    else:
        await session.rollback()
    return failed


async def claim_job_revision(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    expected_revision: int,
) -> bool:
    result = await session.execute(
        update(JobDescription)
        .where(
            JobDescription.id == job_id,
            JobDescription.revision == expected_revision,
            JobDescription.parsing_status == "PENDING",
            JobDescription.is_deleted.is_(False),
        )
        .values(
            parsing_status="PROCESSING",
            parsing_error_message=None,
            updated_at=datetime.now(UTC),
        )
        .returning(JobDescription.id)
    )
    claimed = result.scalar_one_or_none() is not None
    if claimed:
        await session.commit()
    else:
        await session.rollback()
    return claimed


async def fail_job_revision(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    expected_revision: int,
    error_message: str,
) -> bool:
    result = await session.execute(
        update(JobDescription)
        .where(
            JobDescription.id == job_id,
            JobDescription.revision == expected_revision,
            JobDescription.parsing_status == "PROCESSING",
            JobDescription.is_deleted.is_(False),
        )
        .values(
            parsing_status="FAILED",
            parsing_error_message=error_message,
            updated_at=datetime.now(UTC),
        )
        .returning(JobDescription.id)
    )
    failed = result.scalar_one_or_none() is not None
    if failed:
        await session.commit()
    else:
        await session.rollback()
    return failed


async def claim_match_generation(
    session: AsyncSession,
    *,
    match_id: uuid.UUID,
    expected_generation: int,
    expected_resume_revision: int,
    expected_job_revision: int,
    algorithm_version: str,
) -> bool:
    """Exclusively claim one current Match computation generation."""

    resume_is_current = exists(
        select(Resume.id).where(
            Resume.id == MatchResult.resume_id,
            Resume.revision == expected_resume_revision,
            Resume.is_deleted.is_(False),
        )
    )
    job_is_current = exists(
        select(JobDescription.id).where(
            JobDescription.id == MatchResult.job_id,
            JobDescription.revision == expected_job_revision,
            JobDescription.is_deleted.is_(False),
        )
    )
    result = await session.execute(
        update(MatchResult)
        .where(
            MatchResult.id == match_id,
            MatchResult.generation == expected_generation,
            MatchResult.resume_revision == expected_resume_revision,
            MatchResult.job_revision == expected_job_revision,
            MatchResult.algorithm_version == algorithm_version,
            MatchResult.status == "PENDING",
            resume_is_current,
            job_is_current,
        )
        .values(
            status="PROCESSING",
            error_message=None,
            updated_at=datetime.now(UTC),
        )
        .returning(MatchResult.id)
    )
    claimed = result.scalar_one_or_none() is not None
    if claimed:
        await session.commit()
    else:
        await session.rollback()
    return claimed
