from __future__ import annotations

import asyncio
import hashlib
import io
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from docx import Document
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, AsyncTransaction

from app.ai.resume_parser import RESUME_TEXT_PREPROCESSING_VERSION
from app.ai.schemas import ParsedExperience, ParsedResumeResult, ParsedSkill
from app.ai.vector_embedding import BGE_M3_EMBEDDING_DIMENSION, BGE_M3_MODEL_NAME
from app.core.config import Settings
from app.core.database import create_engine
from app.core.version_guard import claim_resume_revision, fail_resume_revision
from app.models.resume import CandidateProfile, Resume, ResumeEducation, ResumeExperience
from app.models.skill import ResumeSkill, Skill
from app.models.user import User
from app.storage.resume_storage import LocalResumeStorage, ResumeStorage
from app.workers import resume_parse_worker
from app.workers.resume_parse_worker import ResumeParseTaskOutcome, process_resume_parse_task


class FakeEmbeddingProvider:
    model_name = BGE_M3_MODEL_NAME
    dimension = BGE_M3_EMBEDDING_DIMENSION

    async def embed(self, text: str) -> list[float]:
        assert text
        return [0.125] * self.dimension


class FailingEmbeddingProvider(FakeEmbeddingProvider):
    async def embed(self, text: str) -> list[float]:
        del text
        raise RuntimeError("C:/private/huggingface/cache/model")


@dataclass
class PostgreSQLWorkerHarness:
    connection: AsyncConnection
    outer_transaction: AsyncTransaction
    setup_session: AsyncSession
    session_factory: Any
    storage: LocalResumeStorage
    user: User

    async def create_resume(
        self,
        source: bytes,
        *,
        status: str = "PENDING",
        revision: int = 1,
        deleted: bool = False,
    ) -> Resume:
        resume_id = uuid.uuid4()
        storage_key = f"worker-tests/{resume_id}/source"
        await self.storage.put_if_absent(storage_key, source)
        now = datetime.now(UTC)
        is_parsed = status == "PARSED"
        resume = Resume(
            id=resume_id,
            owner_user_id=self.user.id,
            file_name="worker.docx",
            storage_key=storage_key,
            file_size=len(source),
            mime_type=("application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            create_request_fingerprint=hashlib.sha256(source).hexdigest(),
            revision=revision,
            parsing_status=status,
            raw_text="already parsed" if is_parsed else None,
            resume_embedding=[0.0] * BGE_M3_EMBEDDING_DIMENSION if is_parsed else None,
            embedding_model=BGE_M3_MODEL_NAME if is_parsed else None,
            embedding_preprocessing_version=(
                RESUME_TEXT_PREPROCESSING_VERSION if is_parsed else None
            ),
            error_message="prior failure" if status == "FAILED" else None,
            is_deleted=deleted,
            deleted_at=now if deleted else None,
            created_at=now,
            updated_at=now,
            parsed_at=now if is_parsed else None,
        )
        self.setup_session.add(resume)
        await self.setup_session.commit()
        return resume

    async def load_resume(self, resume_id: uuid.UUID) -> Resume:
        async with self.session_factory() as session:
            resume = await session.get(Resume, resume_id)
            assert resume is not None
            return resume


def integration_database_settings() -> Settings:
    configured = Settings()
    if configured.database_url is None:
        pytest.skip("DATABASE_URL is not configured")
    return Settings(
        _env_file=None,
        app_env="test",
        database_url=configured.database_url,
        database_connect_timeout_seconds=configured.database_connect_timeout_seconds,
    )


def make_user(label: str) -> User:
    now = datetime.now(UTC)
    return User(
        id=uuid.uuid4(),
        email=f"resume.worker.{label}.{uuid.uuid4().hex}@example.com",
        password_hash="not-used-by-worker-integration",
        full_name="Resume Worker Integration",
        phone_number=None,
        role="CANDIDATE",
        is_active=True,
        created_at=now,
        updated_at=now,
    )


def make_docx(skill_name: str = "") -> bytes:
    document = Document()
    for paragraph in [
        "Jane Candidate",
        "jane.worker@example.com",
        "Summary",
        "Backend engineer building reliable systems.",
        "Skills",
        skill_name,
        "Experience",
        "Software Engineer | Example Company",
        "01/2020 - Present",
        "Built production services.",
        "",
        "Education",
        "Example University",
        "Bachelor of Science in Computer Science",
        "2016 - 2020",
    ]:
        document.add_paragraph(paragraph)
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


@pytest_asyncio.fixture
async def postgres_worker(tmp_path: Path) -> AsyncIterator[PostgreSQLWorkerHarness]:
    settings = integration_database_settings()
    engine = create_engine(settings)
    assert engine is not None
    connection: AsyncConnection | None = None
    try:
        connection = await engine.connect()
    except Exception:  # noqa: BLE001 - never expose database credentials
        await engine.dispose()
        pytest.fail("Configured PostgreSQL database is unavailable", pytrace=False)

    outer_transaction = await connection.begin()

    def session_factory() -> AsyncSession:
        return AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )

    setup_session = session_factory()
    user = make_user("rollback")
    user_id = user.id
    setup_session.add(user)
    await setup_session.commit()
    harness = PostgreSQLWorkerHarness(
        connection,
        outer_transaction,
        setup_session,
        session_factory,
        LocalResumeStorage(tmp_path / "worker-storage"),
        user,
    )
    try:
        yield harness
    finally:
        await setup_session.close()
        if outer_transaction.is_active:
            await outer_transaction.rollback()
        remaining = await connection.scalar(
            select(func.count()).select_from(User).where(User.id == user_id)
        )
        if connection.in_transaction():
            await connection.rollback()
        await connection.close()
        await engine.dispose()
        assert remaining == 0


@pytest.mark.asyncio
async def test_claim_cas_accepts_only_pending_exact_revision_and_not_deleted(
    postgres_worker: PostgreSQLWorkerHarness,
) -> None:
    harness = postgres_worker
    pending = await harness.create_resume(make_docx())
    stale = await harness.create_resume(make_docx(), revision=2)
    deleted_resume = await harness.create_resume(make_docx(), deleted=True)
    processing = await harness.create_resume(make_docx(), status="PROCESSING")
    parsed = await harness.create_resume(make_docx(), status="PARSED")
    failed = await harness.create_resume(make_docx(), status="FAILED")

    async def claim(resume_id: uuid.UUID, revision: int) -> bool:
        async with harness.session_factory() as session:
            return await claim_resume_revision(
                session,
                resume_id=resume_id,
                expected_revision=revision,
            )

    assert await claim(pending.id, 1) is True
    assert await claim(pending.id, 1) is False
    assert await claim(stale.id, 1) is False
    assert await claim(deleted_resume.id, 1) is False
    assert await claim(processing.id, 1) is False
    assert await claim(parsed.id, 1) is False
    assert await claim(failed.id, 1) is False
    claimed_row = await harness.load_resume(pending.id)
    assert claimed_row.parsing_status == "PROCESSING"
    assert claimed_row.revision == 1


@pytest.mark.asyncio
async def test_real_parser_taxonomy_storage_and_aggregate_success(
    postgres_worker: PostgreSQLWorkerHarness,
) -> None:
    harness = postgres_worker
    skill = await harness.setup_session.scalar(
        select(Skill).order_by((Skill.normalized_name != "python").asc(), Skill.id.asc())
    )
    assert skill is not None
    taxonomy_count_before = await harness.setup_session.scalar(
        select(func.count()).select_from(Skill)
    )
    resume = await harness.create_resume(make_docx(skill.name))
    harness.setup_session.add_all(
        [
            CandidateProfile(resume_id=resume.id, full_name="Prior Profile"),
            ResumeSkill(resume_id=resume.id, skill_id=skill.id, proficiency_level="prior"),
            ResumeExperience(
                resume_id=resume.id,
                company_name="Prior Company",
                job_title="Prior Role",
            ),
            ResumeEducation(resume_id=resume.id, institution_name="Prior University"),
        ]
    )
    await harness.setup_session.commit()

    first = await process_resume_parse_task(
        resume.id,
        1,
        session_factory=harness.session_factory,
        storage=harness.storage,
        embedding_provider=FakeEmbeddingProvider(),
    )
    duplicate = await process_resume_parse_task(
        resume.id,
        1,
        session_factory=harness.session_factory,
        storage=harness.storage,
        embedding_provider=FakeEmbeddingProvider(),
    )

    async with harness.session_factory() as session:
        stored = await session.get(Resume, resume.id)
        assert stored is not None
        profile = await session.scalar(
            select(CandidateProfile).where(CandidateProfile.resume_id == resume.id)
        )
        resume_skills = (
            await session.scalars(select(ResumeSkill).where(ResumeSkill.resume_id == resume.id))
        ).all()
        experiences = (
            await session.scalars(
                select(ResumeExperience).where(ResumeExperience.resume_id == resume.id)
            )
        ).all()
        educations = (
            await session.scalars(
                select(ResumeEducation).where(ResumeEducation.resume_id == resume.id)
            )
        ).all()
        taxonomy_count_after = await session.scalar(select(func.count()).select_from(Skill))

    assert first is ResumeParseTaskOutcome.PARSED
    assert duplicate is ResumeParseTaskOutcome.DISCARDED
    assert stored.parsing_status == "PARSED"
    assert stored.revision == 1
    assert stored.raw_text and "Jane Candidate" in stored.raw_text
    assert stored.resume_embedding is not None and len(stored.resume_embedding) == 1024
    assert stored.embedding_model == BGE_M3_MODEL_NAME
    assert stored.embedding_preprocessing_version == RESUME_TEXT_PREPROCESSING_VERSION
    assert stored.parsed_at is not None and stored.error_message is None
    assert profile is not None and profile.full_name == "Jane Candidate"
    assert [item.skill_id for item in resume_skills] == [skill.id]
    assert resume_skills[0].proficiency_level is None
    assert len(experiences) == 1 and experiences[0].company_name == "Example Company"
    assert len(educations) == 1 and educations[0].institution_name == "Example University"
    assert taxonomy_count_after == taxonomy_count_before


class MutatingStorage:
    def __init__(
        self,
        delegate: ResumeStorage,
        mutation: Any,
    ) -> None:
        self.delegate = delegate
        self.mutation = mutation

    async def put_if_absent(self, key: str, data: bytes) -> Any:
        return await self.delegate.put_if_absent(key, data)

    async def read_bytes(self, key: str) -> bytes:
        data = await self.delegate.read_bytes(key)
        await self.mutation()
        return data


@pytest.mark.asyncio
@pytest.mark.parametrize("race", ["delete", "revision"])
async def test_terminal_cas_blocks_delete_or_stale_revision_without_aggregate(
    postgres_worker: PostgreSQLWorkerHarness,
    race: str,
) -> None:
    harness = postgres_worker
    resume = await harness.create_resume(make_docx())

    async def mutate_after_claim() -> None:
        values: dict[str, Any]
        if race == "delete":
            values = {"is_deleted": True, "deleted_at": datetime.now(UTC)}
        else:
            values = {"revision": 2, "parsing_status": "PENDING"}
        async with harness.session_factory() as session:
            await session.execute(update(Resume).where(Resume.id == resume.id).values(**values))
            await session.commit()

    outcome = await process_resume_parse_task(
        resume.id,
        1,
        session_factory=harness.session_factory,
        storage=MutatingStorage(harness.storage, mutate_after_claim),
        embedding_provider=FakeEmbeddingProvider(),
    )

    async with harness.session_factory() as session:
        stored = await session.get(Resume, resume.id)
        child_count = await session.scalar(
            select(func.count())
            .select_from(ResumeExperience)
            .where(ResumeExperience.resume_id == resume.id)
        )
    assert outcome is ResumeParseTaskOutcome.DISCARDED
    assert stored is not None and stored.parsing_status != "PARSED"
    assert stored.raw_text is None and child_count == 0


@pytest.mark.asyncio
async def test_storage_or_embedding_failure_is_controlled_and_revision_is_unchanged(
    postgres_worker: PostgreSQLWorkerHarness,
) -> None:
    harness = postgres_worker
    storage_failure = await harness.create_resume(make_docx())
    parsing_failure = await harness.create_resume(b"not a DOCX package")
    embedding_failure = await harness.create_resume(make_docx())

    class FailingStorage:
        async def put_if_absent(self, key: str, data: bytes) -> Any:
            del key, data
            raise AssertionError

        async def read_bytes(self, key: str) -> bytes:
            del key
            raise OSError("D:/secret/resume-storage/source")

    first = await process_resume_parse_task(
        storage_failure.id,
        1,
        session_factory=harness.session_factory,
        storage=FailingStorage(),
        embedding_provider=FakeEmbeddingProvider(),
    )
    second = await process_resume_parse_task(
        parsing_failure.id,
        1,
        session_factory=harness.session_factory,
        storage=harness.storage,
        embedding_provider=FakeEmbeddingProvider(),
    )
    third = await process_resume_parse_task(
        embedding_failure.id,
        1,
        session_factory=harness.session_factory,
        storage=harness.storage,
        embedding_provider=FailingEmbeddingProvider(),
    )

    first_row = await harness.load_resume(storage_failure.id)
    second_row = await harness.load_resume(parsing_failure.id)
    third_row = await harness.load_resume(embedding_failure.id)
    assert first is ResumeParseTaskOutcome.FAILED
    assert second is ResumeParseTaskOutcome.FAILED
    assert third is ResumeParseTaskOutcome.FAILED
    assert (
        first_row.parsing_status
        == second_row.parsing_status
        == third_row.parsing_status
        == "FAILED"
    )
    assert first_row.revision == second_row.revision == third_row.revision == 1
    assert first_row.error_message == "Resume source could not be read"
    assert second_row.error_message == "Resume parsing failed: UNREADABLE_SOURCE"
    assert third_row.error_message == "Resume embedding failed"
    assert "secret" not in first_row.error_message and "huggingface" not in third_row.error_message


@pytest.mark.asyncio
async def test_deleted_failure_terminal_is_discarded(
    postgres_worker: PostgreSQLWorkerHarness,
) -> None:
    harness = postgres_worker
    resume = await harness.create_resume(make_docx())
    async with harness.session_factory() as session:
        assert await claim_resume_revision(session, resume_id=resume.id, expected_revision=1)
    async with harness.session_factory() as session:
        await session.execute(
            update(Resume)
            .where(Resume.id == resume.id)
            .values(is_deleted=True, deleted_at=datetime.now(UTC))
        )
        await session.commit()
    async with harness.session_factory() as session:
        failed = await fail_resume_revision(
            session,
            resume_id=resume.id,
            expected_revision=1,
            error_message="must not persist",
        )
    stored = await harness.load_resume(resume.id)
    assert failed is False
    assert stored.parsing_status == "PROCESSING" and stored.error_message is None


@pytest.mark.asyncio
async def test_aggregate_constraint_failure_rolls_back_parsed_and_marks_failed(
    postgres_worker: PostgreSQLWorkerHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = postgres_worker
    resume = await harness.create_resume(make_docx())
    parsed = ParsedResumeResult(
        raw_text="valid normalized text",
        experiences=(ParsedExperience(company_name="x" * 151, job_title="Engineer"),),
    )
    monkeypatch.setattr(resume_parse_worker, "parse_resume", lambda *args: parsed)

    outcome = await process_resume_parse_task(
        resume.id,
        1,
        session_factory=harness.session_factory,
        storage=harness.storage,
        embedding_provider=FakeEmbeddingProvider(),
    )

    async with harness.session_factory() as session:
        stored = await session.get(Resume, resume.id)
        child_count = await session.scalar(
            select(func.count())
            .select_from(ResumeExperience)
            .where(ResumeExperience.resume_id == resume.id)
        )
    assert outcome is ResumeParseTaskOutcome.FAILED
    assert stored is not None and stored.parsing_status == "FAILED"
    assert stored.raw_text is None and stored.resume_embedding is None
    assert stored.embedding_model is None and stored.parsed_at is None
    assert child_count == 0


@pytest.mark.asyncio
async def test_unknown_parser_skill_is_never_inserted(
    postgres_worker: PostgreSQLWorkerHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = postgres_worker
    maximum_skill_id = await harness.setup_session.scalar(select(func.max(Skill.id))) or 0
    taxonomy_count_before = await harness.setup_session.scalar(
        select(func.count()).select_from(Skill)
    )
    resume = await harness.create_resume(make_docx())
    parsed = ParsedResumeResult(
        raw_text="unknown skill",
        skills=(ParsedSkill(maximum_skill_id + 1, "Unknown", "unknown"),),
    )
    monkeypatch.setattr(resume_parse_worker, "parse_resume", lambda *args: parsed)

    outcome = await process_resume_parse_task(
        resume.id,
        1,
        session_factory=harness.session_factory,
        storage=harness.storage,
        embedding_provider=FakeEmbeddingProvider(),
    )

    async with harness.session_factory() as session:
        resume_skill_count = await session.scalar(
            select(func.count()).select_from(ResumeSkill).where(ResumeSkill.resume_id == resume.id)
        )
        taxonomy_count_after = await session.scalar(select(func.count()).select_from(Skill))
    stored = await harness.load_resume(resume.id)
    assert outcome is ResumeParseTaskOutcome.FAILED
    assert stored.parsing_status == "FAILED"
    assert resume_skill_count == 0
    assert taxonomy_count_after == taxonomy_count_before


@pytest.mark.asyncio
async def test_two_independent_sessions_race_one_claim_wins() -> None:
    settings = integration_database_settings()
    engine = create_engine(settings)
    assert engine is not None
    user = make_user("race")
    resume_id = uuid.uuid4()
    now = datetime.now(UTC)
    setup = AsyncSession(engine, expire_on_commit=False)
    cleanup = AsyncSession(engine, expire_on_commit=False)
    try:
        setup.add(user)
        await setup.commit()
        setup.add(
            Resume(
                id=resume_id,
                owner_user_id=user.id,
                file_name="race.pdf",
                storage_key=f"worker-race/{resume_id}/source",
                file_size=10,
                mime_type="application/pdf",
                create_request_fingerprint=uuid.uuid4().hex * 2,
                revision=1,
                parsing_status="PENDING",
                created_at=now,
                updated_at=now,
            )
        )
        await setup.commit()

        async def claim() -> bool:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                return await claim_resume_revision(
                    session,
                    resume_id=resume_id,
                    expected_revision=1,
                )

        results = await asyncio.gather(claim(), claim())
        assert sorted(results) == [False, True]
    finally:
        await setup.close()
        await cleanup.execute(delete(Resume).where(Resume.id == resume_id))
        await cleanup.execute(delete(User).where(User.id == user.id))
        await cleanup.commit()
        await cleanup.close()
        await engine.dispose()
