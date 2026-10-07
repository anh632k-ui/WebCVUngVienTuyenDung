from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, AsyncTransaction

from app.ai.vector_embedding import BGE_M3_EMBEDDING_DIMENSION, BGE_M3_MODEL_NAME
from app.api.v1.endpoints import resumes as resume_endpoint
from app.core.config import Settings, get_settings
from app.core.database import create_engine, get_db_session
from app.core.security import create_access_token
from app.models.job import JobDescription
from app.models.match_result import MatchResult
from app.models.resume import CandidateProfile, Resume, ResumeEducation, ResumeExperience
from app.models.skill import ResumeSkill, Skill
from app.models.user import User
from main import app

TEST_JWT_SECRET = "resume-postgres-integration-jwt-secret"


class RecordingEmbeddingProvider:
    model_name = BGE_M3_MODEL_NAME
    dimension = BGE_M3_EMBEDDING_DIMENSION

    def __init__(self) -> None:
        self.texts: list[str] = []

    async def embed(self, text: str) -> list[float]:
        self.texts.append(text)
        return [0.25] * self.dimension


class FailingEmbeddingProvider(RecordingEmbeddingProvider):
    async def embed(self, text: str) -> list[float]:
        self.texts.append(text)
        raise RuntimeError("secret model cache path and CV content")


class InvalidEmbeddingProvider(RecordingEmbeddingProvider):
    def __init__(self, kind: str) -> None:
        super().__init__()
        self.kind = kind
        if kind == "model":
            self.model_name = "incompatible-secret-model"
        if kind == "metadata_dimension":
            self.dimension = BGE_M3_EMBEDDING_DIMENSION - 1

    async def embed(self, text: str) -> list[float]:
        self.texts.append(text)
        if self.kind == "wrong_vector_dimension":
            return [0.25] * (BGE_M3_EMBEDDING_DIMENSION - 1)
        if self.kind == "nonfinite":
            return [float("nan"), *([0.25] * (BGE_M3_EMBEDDING_DIMENSION - 1))]
        raise RuntimeError("secret model cache path and CV content")


@dataclass
class PostgreSQLResumeHarness:
    client: AsyncClient
    session: AsyncSession
    users: dict[str, User]
    resumes: dict[str, Resume]
    settings: Settings
    unique_part: str
    outer_transaction: AsyncTransaction
    embedding_provider: RecordingEmbeddingProvider

    def headers(self, user_name: str) -> dict[str, str]:
        token = create_access_token(self.users[user_name].id, self.settings)
        return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def postgres_resumes() -> AsyncIterator[PostgreSQLResumeHarness]:
    configured_settings = Settings()
    if configured_settings.database_url is None:
        pytest.skip("DATABASE_URL is not configured")

    database_settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=configured_settings.database_url,
        database_connect_timeout_seconds=(configured_settings.database_connect_timeout_seconds),
    )
    integration_settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=None,
        jwt_secret_key=TEST_JWT_SECRET,
    )
    engine = create_engine(database_settings)
    assert engine is not None
    connection: AsyncConnection | None = None
    try:
        connection = await engine.connect()
    except Exception:  # noqa: BLE001 - never expose database credentials
        await engine.dispose()
        pytest.fail("Configured PostgreSQL database is unavailable", pytrace=False)

    outer_transaction = await connection.begin()
    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    unique_part = uuid.uuid4().hex
    now = datetime.now(UTC)

    def make_user(name: str, role: str) -> User:
        return User(
            id=uuid.uuid4(),
            email=f"resume.integration.{unique_part}.{name}@example.com",
            password_hash="not-used-by-resume-integration",
            full_name=f"Resume Integration {name}",
            phone_number=None,
            role=role,
            is_active=True,
            created_at=now,
            updated_at=now,
        )

    users = {
        "candidate": make_user("candidate", "CANDIDATE"),
        "other": make_user("other", "CANDIDATE"),
        "hr": make_user("hr", "HR"),
        "admin": make_user("admin", "ADMIN"),
    }
    session.add_all(users.values())
    await session.commit()

    def make_resume(
        name: str,
        owner: User,
        status: str,
        *,
        deleted: bool = False,
    ) -> Resume:
        is_parsed = status == "PARSED"
        return Resume(
            id=uuid.uuid4(),
            owner_user_id=owner.id,
            file_name=f"{unique_part}-{name}.pdf",
            storage_key=f"resume-integration/{unique_part}/{name}.pdf",
            file_size=100,
            mime_type="application/pdf",
            create_request_fingerprint=uuid.uuid4().hex * 2,
            revision=1,
            parsing_status=status,
            raw_text="parsed text" if is_parsed else None,
            resume_embedding=[0.0] * 1024 if is_parsed else None,
            embedding_model="integration-model" if is_parsed else None,
            embedding_preprocessing_version="v1" if is_parsed else None,
            error_message="integration failure" if status == "FAILED" else None,
            is_manually_edited=False,
            is_deleted=deleted,
            created_at=now,
            updated_at=now,
            parsed_at=now if is_parsed else None,
            deleted_at=now if deleted else None,
        )

    resumes = {
        "pending": make_resume("pending", users["candidate"], "PENDING"),
        "processing": make_resume("processing", users["candidate"], "PROCESSING"),
        "failed": make_resume("failed", users["candidate"], "FAILED"),
        "parsed": make_resume("parsed", users["candidate"], "PARSED"),
        "deleted": make_resume("deleted", users["candidate"], "PENDING", deleted=True),
        "other": make_resume("other", users["other"], "PENDING"),
        "hr": make_resume("hr", users["hr"], "PARSED"),
    }
    session.add_all(resumes.values())
    await session.commit()

    skill_id = await session.scalar(select(Skill.id).order_by(Skill.id.asc()).limit(1))
    assert skill_id is not None, "Canonical skill taxonomy is empty"
    parsed_id = resumes["parsed"].id
    session.add_all(
        [
            CandidateProfile(
                resume_id=parsed_id,
                full_name="PostgreSQL Parsed Candidate",
                email="parsed@example.com",
                current_title="Engineer",
            ),
            ResumeSkill(
                resume_id=parsed_id,
                skill_id=skill_id,
                years_of_experience=Decimal("2.0"),
                proficiency_level="ADVANCED",
            ),
            ResumeExperience(
                resume_id=parsed_id,
                company_name="Integration Company",
                job_title="Engineer",
                start_date=date(2023, 1, 1),
                is_current=True,
            ),
            ResumeEducation(
                resume_id=parsed_id,
                institution_name="Integration University",
                degree="BSc",
                start_year=2019,
                graduation_year=2023,
            ),
        ]
    )
    await session.commit()

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield session

    def override_settings() -> Settings:
        return integration_settings

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_settings] = override_settings
    embedding_provider = RecordingEmbeddingProvider()
    app.dependency_overrides[resume_endpoint.get_resume_embedding_provider] = lambda: (
        embedding_provider
    )
    tracked_user_ids = {item.id for item in users.values()}
    tracked_resume_ids = {item.id for item in resumes.values()}
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield PostgreSQLResumeHarness(
                client,
                session,
                users,
                resumes,
                integration_settings,
                unique_part,
                outer_transaction,
                embedding_provider,
            )
    finally:
        app.dependency_overrides.pop(get_db_session, None)
        app.dependency_overrides.pop(get_settings, None)
        app.dependency_overrides.pop(resume_endpoint.get_resume_embedding_provider, None)
        remaining_users: int | None = None
        remaining_resumes: int | None = None
        try:
            await session.close()
            if outer_transaction.is_active:
                await outer_transaction.rollback()
            remaining_users = await connection.scalar(
                select(func.count()).select_from(User).where(User.id.in_(tracked_user_ids))
            )
            remaining_resumes = await connection.scalar(
                select(func.count()).select_from(Resume).where(Resume.id.in_(tracked_resume_ids))
            )
        finally:
            if connection.in_transaction():
                await connection.rollback()
            await connection.close()
            await engine.dispose()
        assert remaining_users == 0 and remaining_resumes == 0


@pytest.mark.asyncio
async def test_real_postgres_resume_read_endpoints(
    postgres_resumes: PostgreSQLResumeHarness,
) -> None:
    harness = postgres_resumes
    initial_state = {
        item.id: (item.parsing_status, item.updated_at) for item in harness.resumes.values()
    }
    candidate_headers = harness.headers("candidate")

    listing = await harness.client.get(
        "/api/v1/resumes",
        params={"keyword": harness.unique_part, "page": 2, "limit": 2},
        headers=candidate_headers,
    )
    assert listing.status_code == 200
    assert listing.json()["meta"] == {
        "page": 2,
        "limit": 2,
        "total_items": 4,
        "total_pages": 2,
    }
    assert len(listing.json()["data"]) == 2
    assert "storage_key" not in listing.text

    failed = await harness.client.get(
        "/api/v1/resumes",
        params={"keyword": harness.unique_part.upper(), "parsing_status": "FAILED"},
        headers=candidate_headers,
    )
    assert failed.status_code == 200
    assert failed.json()["meta"]["total_items"] == 1
    assert failed.json()["data"][0]["parsing_status"] == "FAILED"

    unauthorized = await harness.client.get(
        f"/api/v1/resumes/{harness.resumes['other'].id}", headers=candidate_headers
    )
    deleted = await harness.client.get(
        f"/api/v1/resumes/{harness.resumes['deleted'].id}", headers=candidate_headers
    )
    assert unauthorized.status_code == 404
    assert deleted.status_code == 404

    pending = await harness.client.get(
        f"/api/v1/resumes/{harness.resumes['pending'].id}", headers=candidate_headers
    )
    parsed = await harness.client.get(
        f"/api/v1/resumes/{harness.resumes['parsed'].id}", headers=candidate_headers
    )
    assert pending.status_code == 200
    assert pending.json()["data"]["candidate_profile"] is None
    assert parsed.status_code == 200
    assert parsed.json()["data"]["candidate_profile"]["full_name"] == (
        "PostgreSQL Parsed Candidate"
    )
    assert parsed.json()["data"]["skills"][0]["years_of_experience"] == 2.0
    assert parsed.json()["data"]["experiences"][0]["company_name"] == ("Integration Company")
    assert parsed.json()["data"]["educations"][0]["institution_name"] == ("Integration University")

    for name, status in (
        ("pending", "PENDING"),
        ("processing", "PROCESSING"),
        ("parsed", "PARSED"),
        ("failed", "FAILED"),
    ):
        response = await harness.client.get(
            f"/api/v1/resumes/{harness.resumes[name].id}/status",
            headers=candidate_headers,
        )
        assert response.status_code == 200
        assert response.json()["data"]["parsing_status"] == status

    hr_listing = await harness.client.get("/api/v1/resumes", headers=harness.headers("hr"))
    admin_listing = await harness.client.get(
        "/api/v1/resumes",
        params={"keyword": harness.unique_part, "limit": 100},
        headers=harness.headers("admin"),
    )
    assert hr_listing.json()["meta"]["total_items"] == 1
    assert admin_listing.json()["meta"]["total_items"] == 6

    harness.session.expire_all()
    persisted = list(
        (await harness.session.scalars(select(Resume).where(Resume.id.in_(initial_state)))).all()
    )
    assert {item.id: (item.parsing_status, item.updated_at) for item in persisted} == initial_state
    assert harness.outer_transaction.is_active


@pytest.mark.asyncio
async def test_real_postgres_resume_soft_delete(
    postgres_resumes: PostgreSQLResumeHarness,
) -> None:
    harness = postgres_resumes
    target = harness.resumes["parsed"]
    target_id = target.id
    candidate_headers = harness.headers("candidate")
    original_revision = target.revision
    original_storage_key = target.storage_key
    original_updated_at = target.updated_at
    profile_count = await harness.session.scalar(
        select(func.count())
        .select_from(CandidateProfile)
        .where(CandidateProfile.resume_id == target_id)
    )

    response = await harness.client.delete(
        f"/api/v1/resumes/{target_id}", headers=candidate_headers
    )
    assert response.status_code == 204 and response.content == b""

    harness.session.expire_all()
    persisted = await harness.session.scalar(select(Resume).where(Resume.id == target_id))
    assert persisted is not None
    assert persisted.is_deleted is True
    assert persisted.deleted_at is not None
    assert persisted.updated_at >= original_updated_at
    assert persisted.revision == original_revision
    assert persisted.storage_key == original_storage_key
    assert (
        await harness.session.scalar(
            select(func.count())
            .select_from(CandidateProfile)
            .where(CandidateProfile.resume_id == target_id)
        )
        == profile_count
    )

    listing = await harness.client.get(
        "/api/v1/resumes",
        params={"keyword": harness.unique_part, "limit": 100},
        headers=candidate_headers,
    )
    detail = await harness.client.get(f"/api/v1/resumes/{target_id}", headers=candidate_headers)
    status_response = await harness.client.get(
        f"/api/v1/resumes/{target_id}/status", headers=candidate_headers
    )
    assert str(target_id) not in listing.text
    assert detail.status_code == 404
    assert status_response.status_code == 404
    assert harness.outer_transaction.is_active


@pytest.mark.asyncio
async def test_real_postgres_negative_years_are_rejected_without_any_mutation(
    postgres_resumes: PostgreSQLResumeHarness,
) -> None:
    harness = postgres_resumes
    target_id = harness.resumes["parsed"].id
    skill_id = await harness.session.scalar(select(Skill.id).order_by(Skill.id.asc()).limit(1))
    assert skill_id is not None
    headers = harness.headers("candidate")

    async def snapshot() -> tuple[object, ...]:
        harness.session.expire_all()
        resume = await harness.session.get(Resume, target_id, populate_existing=True)
        profile = await harness.session.scalar(
            select(CandidateProfile).where(CandidateProfile.resume_id == target_id)
        )
        skills = tuple(
            (
                row.skill_id,
                row.years_of_experience,
                row.proficiency_level,
            )
            for row in (
                await harness.session.scalars(
                    select(ResumeSkill)
                    .where(ResumeSkill.resume_id == target_id)
                    .order_by(ResumeSkill.id)
                )
            ).all()
        )
        matches = tuple(
            (
                row.id,
                row.generation,
                row.resume_revision,
                row.job_revision,
                row.status,
                row.overall_score,
                row.matched_skills,
                row.error_message,
            )
            for row in (
                await harness.session.scalars(
                    select(MatchResult)
                    .where(MatchResult.resume_id == target_id)
                    .order_by(MatchResult.id)
                )
            ).all()
        )
        assert resume is not None and profile is not None
        return (
            resume.revision,
            list(resume.resume_embedding or []),
            resume.embedding_model,
            resume.embedding_preprocessing_version,
            resume.is_manually_edited,
            profile.full_name,
            skills,
            matches,
        )

    before = await snapshot()
    provider_calls = len(harness.embedding_provider.texts)
    for invalid_years in (-1, -0.05, -0.04, -0.001):
        response = await harness.client.put(
            f"/api/v1/resumes/{target_id}/parsed-data",
            headers=headers,
            json={
                "candidate_profile": {"full_name": "must-not-persist"},
                "skills": [
                    {
                        "skill_id": skill_id,
                        "years_of_experience": invalid_years,
                    }
                ],
                "experiences": [],
                "educations": [],
            },
        )
        assert response.status_code == 422

    assert len(harness.embedding_provider.texts) == provider_calls
    assert await snapshot() == before


@pytest.mark.asyncio
async def test_real_postgres_replace_parsed_data_and_empty_replacement(
    postgres_resumes: PostgreSQLResumeHarness,
) -> None:
    harness = postgres_resumes
    target_id = harness.resumes["parsed"].id
    headers = harness.headers("candidate")
    skill_id = await harness.session.scalar(select(Skill.id).order_by(Skill.id.asc()).limit(1))
    assert skill_id is not None
    original_raw_text = harness.resumes["parsed"].raw_text
    original_parsed_at = harness.resumes["parsed"].parsed_at

    response = await harness.client.put(
        f"/api/v1/resumes/{target_id}/parsed-data",
        headers=headers,
        json={
            "candidate_profile": {
                "full_name": "Updated Candidate",
                "email": "updated@example.com",
                "professional_summary": "New summary",
            },
            "skills": [
                {
                    "skill_id": skill_id,
                    "years_of_experience": 1.25,
                    "proficiency_level": "EXPERT",
                }
            ],
            "experiences": [
                {
                    "company_name": "New Company",
                    "job_title": "Lead Engineer",
                    "start_date": "2024-01-01",
                    "end_date": None,
                    "is_current": True,
                }
            ],
            "educations": [
                {
                    "institution_name": "New University",
                    "start_year": None,
                    "graduation_year": None,
                    "gpa": 3.456,
                }
            ],
        },
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["resume"]["revision"] == 2
    assert data["resume"]["is_manually_edited"] is True
    assert data["candidate_profile"]["full_name"] == "Updated Candidate"
    assert data["skills"][0]["years_of_experience"] == 1.3
    assert data["educations"][0]["gpa"] == 3.46

    harness.session.expire_all()
    persisted = await harness.session.get(Resume, target_id, populate_existing=True)
    assert persisted is not None
    assert persisted.revision == 2
    assert persisted.raw_text == original_raw_text
    assert persisted.parsed_at == original_parsed_at
    assert persisted.embedding_model == BGE_M3_MODEL_NAME
    assert persisted.embedding_preprocessing_version == "resume-text-v1"
    assert list(persisted.resume_embedding or []) == [0.25] * BGE_M3_EMBEDDING_DIMENSION
    assert "Updated Candidate" in harness.embedding_provider.texts[-1]
    taxonomy_name = await harness.session.scalar(select(Skill.name).where(Skill.id == skill_id))
    assert taxonomy_name is not None and taxonomy_name in harness.embedding_provider.texts[-1]

    empty = await harness.client.put(
        f"/api/v1/resumes/{target_id}/parsed-data",
        headers=headers,
        json={"candidate_profile": None, "skills": [], "experiences": [], "educations": []},
    )
    assert empty.status_code == 200
    assert empty.json()["data"]["resume"]["revision"] == 3
    assert empty.json()["data"]["candidate_profile"] is None
    assert empty.json()["data"]["skills"] == []
    assert empty.json()["data"]["experiences"] == []
    assert empty.json()["data"]["educations"] == []


@pytest.mark.asyncio
async def test_real_postgres_parsed_data_authorization_readiness_and_taxonomy(
    postgres_resumes: PostgreSQLResumeHarness,
) -> None:
    harness = postgres_resumes
    parsed_id = harness.resumes["parsed"].id
    pending_id = harness.resumes["pending"].id
    deleted_id = harness.resumes["deleted"].id
    hr_resume_id = harness.resumes["hr"].id
    candidate_headers = harness.headers("candidate")
    other_headers = harness.headers("other")
    admin_headers = harness.headers("admin")
    hr_headers = harness.headers("hr")
    payload = {"candidate_profile": None, "skills": [], "experiences": [], "educations": []}

    not_owner = await harness.client.put(
        f"/api/v1/resumes/{parsed_id}/parsed-data", headers=other_headers, json=payload
    )
    not_ready = await harness.client.put(
        f"/api/v1/resumes/{pending_id}/parsed-data",
        headers=candidate_headers,
        json=payload,
    )
    invalid_skill = await harness.client.put(
        f"/api/v1/resumes/{parsed_id}/parsed-data",
        headers=candidate_headers,
        json={**payload, "skills": [{"skill_id": 2_147_483_647}]},
    )
    admin = await harness.client.put(
        f"/api/v1/resumes/{parsed_id}/parsed-data", headers=admin_headers, json=payload
    )
    deleted = await harness.client.put(
        f"/api/v1/resumes/{deleted_id}/parsed-data", headers=candidate_headers, json=payload
    )
    nonexistent = await harness.client.put(
        f"/api/v1/resumes/{uuid.uuid4()}/parsed-data", headers=admin_headers, json=payload
    )
    hr_owner = await harness.client.put(
        f"/api/v1/resumes/{hr_resume_id}/parsed-data", headers=hr_headers, json=payload
    )

    assert not_owner.status_code == 404
    assert not_owner.json()["error"]["code"] == "RESUME_NOT_FOUND"
    assert not_ready.status_code == 422
    assert not_ready.json()["error"]["code"] == "RESUME_NOT_READY"
    assert invalid_skill.status_code == 422
    assert invalid_skill.json()["error"]["code"] == "INVALID_RESUME_DATA"
    assert admin.status_code == 200
    assert deleted.status_code == 404
    assert nonexistent.status_code == 404
    assert hr_owner.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("resume_name", ["pending", "processing", "failed"])
async def test_real_postgres_all_nonparsed_states_reject_manual_edit(
    postgres_resumes: PostgreSQLResumeHarness,
    resume_name: str,
) -> None:
    harness = postgres_resumes
    resume_id = harness.resumes[resume_name].id
    headers = harness.headers("candidate")
    response = await harness.client.put(
        f"/api/v1/resumes/{resume_id}/parsed-data",
        headers=headers,
        json={"candidate_profile": None, "skills": [], "experiences": [], "educations": []},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "RESUME_NOT_READY"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_kind",
    ["exception", "model", "metadata_dimension", "wrong_vector_dimension", "nonfinite"],
)
async def test_real_postgres_embedding_failure_is_controlled_and_atomic(
    postgres_resumes: PostgreSQLResumeHarness,
    failure_kind: str,
) -> None:
    harness = postgres_resumes
    target_id = harness.resumes["parsed"].id
    headers = harness.headers("candidate")
    original_revision = harness.resumes["parsed"].revision
    original_embedding = list(harness.resumes["parsed"].resume_embedding or [])
    original_profile = await harness.session.scalar(
        select(CandidateProfile).where(CandidateProfile.resume_id == target_id)
    )
    assert original_profile is not None
    original_name = original_profile.full_name
    failing_provider: RecordingEmbeddingProvider
    if failure_kind == "exception":
        failing_provider = FailingEmbeddingProvider()
    else:
        failing_provider = InvalidEmbeddingProvider(failure_kind)
    app.dependency_overrides[resume_endpoint.get_resume_embedding_provider] = lambda: (
        failing_provider
    )

    response = await harness.client.put(
        f"/api/v1/resumes/{target_id}/parsed-data",
        headers=headers,
        json={
            "candidate_profile": {"full_name": "must not persist"},
            "skills": [],
            "experiences": [],
            "educations": [],
        },
    )
    assert response.status_code == 503
    assert response.json() == {
        "success": False,
        "error": {
            "code": "EMBEDDING_UNAVAILABLE",
            "message": "Resume embedding could not be generated",
            "details": None,
        },
    }
    assert "secret" not in response.text and "cache" not in response.text

    harness.session.expire_all()
    persisted = await harness.session.get(Resume, target_id, populate_existing=True)
    profile = await harness.session.scalar(
        select(CandidateProfile).where(CandidateProfile.resume_id == target_id)
    )
    assert persisted is not None and profile is not None
    assert persisted.revision == original_revision
    assert list(persisted.resume_embedding or []) == original_embedding
    assert profile.full_name == original_name


@pytest.mark.asyncio
async def test_real_postgres_edit_invalidates_all_matches_including_deleted_job(
    postgres_resumes: PostgreSQLResumeHarness,
) -> None:
    harness = postgres_resumes
    target_id = harness.resumes["parsed"].id
    headers = harness.headers("candidate")
    now = datetime.now(UTC)

    def make_job(name: str, revision: int, *, deleted: bool) -> JobDescription:
        return JobDescription(
            id=uuid.uuid4(),
            recruiter_id=harness.users["hr"].id,
            title=name,
            job_level="Senior",
            raw_content=name,
            create_request_fingerprint=uuid.uuid4().hex * 2,
            revision=revision,
            min_experience_years=Decimal("0.0"),
            job_embedding=[0.1] * 1024,
            embedding_model=BGE_M3_MODEL_NAME,
            embedding_preprocessing_version="resume-text-v1",
            parsing_status="PARSED",
            is_criteria_verified=True,
            w_skill=Decimal("0.500"),
            w_semantic=Decimal("0.300"),
            w_experience=Decimal("0.200"),
            status="DRAFT",
            is_deleted=deleted,
            created_at=now,
            updated_at=now,
            parsed_at=now,
            deleted_at=now if deleted else None,
        )

    active_job = make_job("Active historical match", 4, deleted=False)
    deleted_job = make_job("Deleted historical match", 9, deleted=True)
    completed = MatchResult(
        id=uuid.uuid4(),
        job_id=active_job.id,
        resume_id=target_id,
        generation=3,
        resume_revision=1,
        job_revision=4,
        overall_score=Decimal("80.00"),
        skill_score=Decimal("81.00"),
        semantic_score=Decimal("82.00"),
        experience_score=Decimal("83.00"),
        matched_skills=[{"name": "old"}],
        missing_skills=[{"name": "old"}],
        gap_analysis_summary="old",
        algorithm_version="hybrid-v1",
        embedding_model=BGE_M3_MODEL_NAME,
        embedding_preprocessing_version="resume-text-v1",
        status="COMPLETED",
        error_message=None,
        created_at=now,
        updated_at=now,
        calculated_at=now,
    )
    failed = MatchResult(
        id=uuid.uuid4(),
        job_id=deleted_job.id,
        resume_id=target_id,
        generation=7,
        resume_revision=1,
        job_revision=9,
        overall_score=None,
        skill_score=None,
        semantic_score=None,
        experience_score=None,
        matched_skills=[],
        missing_skills=[],
        gap_analysis_summary=None,
        algorithm_version="legacy-preserved",
        embedding_model=None,
        embedding_preprocessing_version=None,
        status="FAILED",
        error_message="old error",
        created_at=now,
        updated_at=now,
        calculated_at=None,
    )
    harness.session.add_all([active_job, deleted_job, completed, failed])
    await harness.session.commit()
    completed_id = completed.id
    failed_id = failed.id

    response = await harness.client.put(
        f"/api/v1/resumes/{target_id}/parsed-data",
        headers=headers,
        json={"candidate_profile": None, "skills": [], "experiences": [], "educations": []},
    )
    assert response.status_code == 200

    harness.session.expire_all()
    rows = list(
        (
            await harness.session.scalars(
                select(MatchResult)
                .where(MatchResult.id.in_([completed_id, failed_id]))
                .order_by(MatchResult.id.asc())
            )
        ).all()
    )
    by_id = {row.id: row for row in rows}
    assert by_id[completed_id].generation == 4
    assert by_id[failed_id].generation == 8
    assert by_id[completed_id].job_revision == 4
    assert by_id[failed_id].job_revision == 9
    assert {row.resume_revision for row in rows} == {2}
    assert {row.status for row in rows} == {"PENDING"}
    assert by_id[completed_id].algorithm_version == "hybrid-v1"
    assert by_id[failed_id].algorithm_version == "legacy-preserved"
    for row in rows:
        assert row.overall_score is None
        assert row.skill_score is None
        assert row.semantic_score is None
        assert row.experience_score is None
        assert row.matched_skills == []
        assert row.missing_skills == []
        assert row.gap_analysis_summary is None
        assert row.error_message is None
        assert row.embedding_model is None
        assert row.embedding_preprocessing_version is None
        assert row.calculated_at is None
