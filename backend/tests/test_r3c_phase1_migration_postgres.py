from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.version_guard import (
    claim_job_revision,
    claim_match_generation,
    claim_resume_revision,
    fail_job_revision,
    fail_resume_revision,
)
from app.models.job import JobDescription
from app.models.match_result import MatchResult
from app.models.resume import Resume
from app.models.user import User
from app.workers.match_worker import _terminal_update

pytestmark = pytest.mark.skipif(
    os.getenv("R3C_PHASE1_MIGRATION_QA") != "1",
    reason="runs only in the isolated R3C Phase 1 migration workflow",
)


def migration_url() -> str:
    value = os.environ.get("R3C_PHASE1_DATABASE_URL", "")
    assert value.endswith("@127.0.0.1:55432/webcv_task_r3c_phase1_ci")
    assert os.environ.get("GITHUB_ACTIONS") == "true"
    assert os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
    return value


@pytest.mark.asyncio
async def test_migrated_database_identity_and_metadata() -> None:
    engine = create_async_engine(migration_url())
    try:
        async with engine.connect() as connection:
            identity = (
                await connection.execute(
                    text(
                        "SELECT current_database(), "
                        "current_setting('server_version_num')::integer / 10000"
                    )
                )
            ).one()
            assert identity == ("webcv_task_r3c_phase1_ci", 18)
            assert (
                await connection.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.tables "
                        "WHERE table_schema='public' AND table_type='BASE TABLE'"
                    )
                )
                == 10
            )
            assert await connection.scalar(text("SELECT count(*) FROM skills")) == 42
            assert (
                await connection.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_schema='public' "
                        "AND table_name IN ('resumes','job_descriptions','match_results') "
                        "AND column_name LIKE 'processing_%'"
                    )
                )
                == 15
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_preexisting_processing_rows_are_preserved() -> None:
    engine = create_async_engine(migration_url())
    try:
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        "SELECT 'resume', revision, parsing_status, processing_attempt_epoch, "
                        "processing_owner_token, processing_activation_token, "
                        "processing_heartbeat_at, processing_lease_expires_at "
                        "FROM resumes WHERE id='20000000-0000-0000-0000-000000000001' "
                        "UNION ALL "
                        "SELECT 'job', revision, parsing_status, processing_attempt_epoch, "
                        "processing_owner_token, processing_activation_token, "
                        "processing_heartbeat_at, processing_lease_expires_at "
                        "FROM job_descriptions "
                        "WHERE id='30000000-0000-0000-0000-000000000001' "
                        "UNION ALL "
                        "SELECT 'match', generation, status, processing_attempt_epoch, "
                        "processing_owner_token, processing_activation_token, "
                        "processing_heartbeat_at, processing_lease_expires_at "
                        "FROM match_results "
                        "WHERE id='40000000-0000-0000-0000-000000000001'"
                    )
                )
            ).all()
            assert {(row[0], row[1], row[2]) for row in rows} == {
                ("resume", 7, "PROCESSING"),
                ("job", 9, "PROCESSING"),
                ("match", 11, "PROCESSING"),
            }
            assert all(row[3] == 0 and all(value is None for value in row[4:]) for row in rows)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_legacy_claim_and_terminal_cas_need_no_fencing_tokens() -> None:
    engine = create_async_engine(migration_url())
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    now = datetime.now(UTC)
    user_id, resume_id, job_id, match_id = (uuid.uuid4() for _ in range(4))
    try:
        async with session_factory() as session:
            session.add(
                User(
                    id=user_id,
                    email=f"r3c-{user_id}@example.invalid",
                    password_hash="not-a-secret",
                    full_name="R3C Legacy CAS",
                    role="HR",
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()

            session.add_all(
                [
                    Resume(
                        id=resume_id,
                        owner_user_id=user_id,
                        file_name="legacy-cas.pdf",
                        storage_key=f"r3c/cas/{resume_id}",
                        file_size=64,
                        mime_type="application/pdf",
                        create_request_fingerprint="c" * 64,
                        revision=3,
                        parsing_status="PENDING",
                        created_at=now,
                        updated_at=now,
                    ),
                    JobDescription(
                        id=job_id,
                        recruiter_id=user_id,
                        title="R3C CAS Job",
                        job_level="MID",
                        raw_content="compatibility fixture",
                        create_request_fingerprint="d" * 64,
                        revision=5,
                        parsing_status="PENDING",
                        status="DRAFT",
                        created_at=now,
                        updated_at=now,
                    ),
                ]
            )
            await session.commit()

            session.add(
                MatchResult(
                    id=match_id,
                    job_id=job_id,
                    resume_id=resume_id,
                    generation=8,
                    resume_revision=3,
                    job_revision=5,
                    algorithm_version="hybrid-v1",
                    status="PENDING",
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()

        async with session_factory() as session:
            assert await claim_resume_revision(session, resume_id=resume_id, expected_revision=3)
        async with session_factory() as session:
            assert await fail_resume_revision(
                session,
                resume_id=resume_id,
                expected_revision=3,
                error_message="expected Phase 1 compatibility failure",
            )
        async with session_factory() as session:
            assert await claim_job_revision(session, job_id=job_id, expected_revision=5)
        async with session_factory() as session:
            assert await fail_job_revision(
                session,
                job_id=job_id,
                expected_revision=5,
                error_message="expected Phase 1 compatibility failure",
            )
        async with session_factory() as session:
            assert await claim_match_generation(
                session,
                match_id=match_id,
                expected_generation=8,
                expected_resume_revision=3,
                expected_job_revision=5,
                algorithm_version="hybrid-v1",
            )
        assert await _terminal_update(
            session_factory,
            match_id=match_id,
            expected_generation=8,
            expected_resume_revision=3,
            expected_job_revision=5,
            algorithm_version="hybrid-v1",
            values={
                "status": "COMPLETED",
                "overall_score": 70,
                "skill_score": 70,
                "semantic_score": 70,
                "experience_score": 70,
                "matched_skills": [],
                "missing_skills": [],
                "gap_analysis_summary": None,
                "embedding_model": "compatibility-model",
                "embedding_preprocessing_version": "compatibility-v1",
                "error_message": None,
                "calculated_at": now,
                "updated_at": now,
            },
        )

        async with session_factory() as session:
            fencing = (
                await session.execute(
                    text(
                        "SELECT processing_attempt_epoch, processing_owner_token, "
                        "processing_activation_token, processing_heartbeat_at, "
                        "processing_lease_expires_at FROM match_results WHERE id=:id"
                    ),
                    {"id": match_id},
                )
            ).one()
            assert fencing == (0, None, None, None, None)
            status = await session.scalar(
                select(MatchResult.status).where(MatchResult.id == match_id)
            )
            assert status == "COMPLETED"
            user = await session.get(User, user_id)
            assert user is not None
            await session.delete(user)
            await session.commit()
    finally:
        await engine.dispose()
