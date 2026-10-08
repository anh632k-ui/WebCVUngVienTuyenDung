from __future__ import annotations

import hashlib
import io
import uuid
import zipfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1.endpoints import resumes as resume_endpoint
from app.core.config import Settings, get_settings
from app.core.database import get_db_session
from app.core.engine_factory import create_engine
from app.core.security import create_access_token
from app.models.job import JobDescription
from app.models.match_result import MatchResult
from app.models.resume import CandidateProfile, Resume, ResumeEducation, ResumeExperience
from app.models.skill import JobSkill, ResumeSkill, Skill
from app.models.user import User
from app.services.resume_service import DOCX_MIME_TYPE, PDF_MIME_TYPE
from app.storage.resume_storage import LocalResumeStorage, PutIfAbsentResult
from main import app

TEST_SECRET = "resume-download-http-postgres-secret"
PDF_BYTES = b"%PDF-1.7\nreal route source\x00\xff\n%%EOF\n"


def make_docx() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            "<Types><Override PartName='/word/document.xml' "
            "ContentType='application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document.main+xml'/></Types>",
        )
        archive.writestr(
            "_rels/.rels",
            "<Relationships><Relationship Id='rId1' "
            "Type='http://schemas.openxmlformats.org/officeDocument/2006/"
            "relationships/officeDocument' Target='word/document.xml'/></Relationships>",
        )
        archive.writestr(
            "word/document.xml",
            "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/"
            "2006/main'><w:body/></w:document>",
        )
    return output.getvalue()


class ControlledLocalStorage:
    def __init__(self, root: Path) -> None:
        self.delegate = LocalResumeStorage(root)
        self.root = root
        self.read_calls: list[str] = []
        self.failures: dict[str, OSError] = {}

    async def put_if_absent(self, key: str, data: bytes) -> PutIfAbsentResult:
        return await self.delegate.put_if_absent(key, data)

    async def read_bytes(self, key: str) -> bytes:
        return await self.delegate.read_bytes(key)

    async def read_bytes_bounded(
        self,
        key: str,
        *,
        expected_size: int,
        maximum_size: int,
    ) -> bytes:
        self.read_calls.append(key)
        failure = self.failures.get(key)
        if failure is not None:
            raise failure
        return await self.delegate.read_bytes_bounded(
            key,
            expected_size=expected_size,
            maximum_size=maximum_size,
        )


@dataclass(frozen=True)
class HTTPDownloadHarness:
    client: AsyncClient
    session_factory: async_sessionmaker[AsyncSession]
    settings: Settings
    storage: ControlledLocalStorage
    users: dict[str, uuid.UUID]
    resumes: dict[str, uuid.UUID]
    sources: dict[str, bytes]
    job_id: uuid.UUID
    match_id: uuid.UUID
    skill_id: int

    def headers(self, user: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {create_access_token(self.users[user], self.settings)}"}


def user(unique: str, name: str, role: str, *, active: bool = True) -> User:
    now = datetime.now(UTC)
    return User(
        id=uuid.uuid4(),
        email=f"download.http.{unique}.{name}@example.com",
        password_hash="not-used",
        full_name=f"HTTP Download {name}",
        role=role,
        is_active=active,
        created_at=now,
        updated_at=now,
    )


def resume(
    owner: User,
    name: str,
    status: str,
    content: bytes,
    media_type: str,
    *,
    deleted: bool = False,
) -> Resume:
    now = datetime.now(UTC)
    resume_id = uuid.uuid4()
    parsed = status == "PARSED"
    return Resume(
        id=resume_id,
        owner_user_id=owner.id,
        file_name=name,
        storage_key=f"resumes/{resume_id}/source",
        file_size=len(content),
        mime_type=media_type,
        create_request_fingerprint=hashlib.sha256(content).hexdigest(),
        revision=1,
        parsing_status=status,
        raw_text="meaningful parsed source text" if parsed else None,
        resume_embedding=[0.125] * 1024 if parsed else None,
        embedding_model="BAAI/bge-m3" if parsed else None,
        embedding_preprocessing_version="resume-text-v1" if parsed else None,
        error_message="controlled parse failure" if status == "FAILED" else None,
        is_manually_edited=False,
        is_deleted=deleted,
        created_at=now,
        updated_at=now,
        parsed_at=now if parsed else None,
        deleted_at=now if deleted else None,
    )


@pytest_asyncio.fixture
async def http_download_postgres(tmp_path: Path) -> AsyncIterator[HTTPDownloadHarness]:
    configured = Settings()
    if configured.database_url is None:
        pytest.skip("DATABASE_URL is not configured")
    database_settings = Settings(
        _env_file=None,
        app_env="test",
        database_url=configured.database_url,
        database_connect_timeout_seconds=configured.database_connect_timeout_seconds,
    )
    settings = Settings(
        _env_file=None,
        app_env="test",
        jwt_secret_key=TEST_SECRET,
        resume_storage_root=tmp_path / "resume-storage",
    )
    engine = create_engine(database_settings)
    assert engine is not None
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001 - never expose database credentials
        await engine.dispose()
        pytest.fail("Configured PostgreSQL database is unavailable", pytrace=False)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    storage = ControlledLocalStorage(settings.resume_storage_root)
    unique = uuid.uuid4().hex
    users = {
        "candidate": user(unique, "candidate", "CANDIDATE"),
        "other": user(unique, "other", "CANDIDATE"),
        "hr": user(unique, "hr", "HR"),
        "admin": user(unique, "admin", "ADMIN"),
        "inactive": user(unique, "inactive", "CANDIDATE", active=False),
    }
    docx_bytes = make_docx()
    rows = {
        "pending": resume(
            users["candidate"],
            'résumé "senior";\r\n\u202e.pdf',
            "PENDING",
            PDF_BYTES,
            PDF_MIME_TYPE,
        ),
        "processing": resume(
            users["candidate"], "processing.pdf", "PROCESSING", PDF_BYTES, PDF_MIME_TYPE
        ),
        "parsed": resume(users["candidate"], "parsed.pdf", "PARSED", PDF_BYTES, PDF_MIME_TYPE),
        "failed": resume(users["candidate"], "failed.pdf", "FAILED", PDF_BYTES, PDF_MIME_TYPE),
        "hr_docx": resume(users["hr"], "ứng viên.docx", "PENDING", docx_bytes, DOCX_MIME_TYPE),
        "other": resume(users["other"], "other.pdf", "PENDING", PDF_BYTES, PDF_MIME_TYPE),
        "deleted": resume(
            users["candidate"],
            "deleted.pdf",
            "PENDING",
            PDF_BYTES,
            PDF_MIME_TYPE,
            deleted=True,
        ),
        "corrupt": resume(users["candidate"], "corrupt.pdf", "PENDING", PDF_BYTES, PDF_MIME_TYPE),
        "missing": resume(users["candidate"], "missing.pdf", "PENDING", PDF_BYTES, PDF_MIME_TYPE),
        "io_error": resume(users["candidate"], "io-error.pdf", "PENDING", PDF_BYTES, PDF_MIME_TYPE),
    }

    async with factory() as setup:
        skill_id = await setup.scalar(select(Skill.id).order_by(Skill.id.asc()).limit(1))
        if skill_id is None:
            await engine.dispose()
            pytest.fail(
                "Configured PostgreSQL database is missing canonical taxonomy seed",
                pytrace=False,
            )
        setup.add_all(users.values())
        await setup.flush()
        setup.add_all(rows.values())
        await setup.flush()
        parsed_id = rows["parsed"].id
        setup.add_all(
            (
                CandidateProfile(
                    resume_id=parsed_id,
                    full_name="Meaningful Candidate",
                    email="candidate@example.com",
                    phone_number="+84901234567",
                    current_title="Senior Engineer",
                    location="Hanoi",
                    linkedin_url="https://linkedin.example/candidate",
                    github_url="https://github.example/candidate",
                    professional_summary="Builds reliable hiring systems",
                ),
                ResumeSkill(
                    resume_id=parsed_id,
                    skill_id=skill_id,
                    years_of_experience=Decimal("4.5"),
                    proficiency_level="ADVANCED",
                ),
                ResumeExperience(
                    resume_id=parsed_id,
                    company_name="Example Company",
                    job_title="Senior Engineer",
                    start_date=date(2021, 1, 1),
                    end_date=None,
                    is_current=True,
                    description="Owned production services",
                ),
                ResumeEducation(
                    resume_id=parsed_id,
                    institution_name="Example University",
                    degree="BSc",
                    field_of_study="Computer Science",
                    start_year=2016,
                    graduation_year=2020,
                    gpa=Decimal("3.75"),
                    description="Distributed systems",
                ),
            )
        )
        job = JobDescription(
            id=uuid.uuid4(),
            recruiter_id=users["hr"].id,
            title="Senior Backend Engineer",
            job_level="Senior",
            raw_content="Python PostgreSQL distributed systems",
            create_request_fingerprint=hashlib.sha256(b"job-content").hexdigest(),
            revision=3,
            min_experience_years=Decimal("3.0"),
            job_embedding=[0.25] * 1024,
            embedding_model="BAAI/bge-m3",
            embedding_preprocessing_version="resume-text-v1",
            parsing_status="PARSED",
            parsing_error_message=None,
            is_criteria_verified=True,
            w_skill=Decimal("0.500"),
            w_semantic=Decimal("0.300"),
            w_experience=Decimal("0.200"),
            status="ACTIVE",
            is_deleted=False,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            parsed_at=datetime.now(UTC),
            deleted_at=None,
        )
        setup.add(job)
        await setup.flush()
        setup.add(
            JobSkill(
                job_id=job.id,
                skill_id=skill_id,
                importance="MANDATORY",
                min_years_required=Decimal("3.0"),
            )
        )
        match = MatchResult(
            id=uuid.uuid4(),
            job_id=job.id,
            resume_id=parsed_id,
            generation=4,
            resume_revision=1,
            job_revision=3,
            overall_score=Decimal("88.50"),
            skill_score=Decimal("90.00"),
            semantic_score=Decimal("87.00"),
            experience_score=Decimal("88.00"),
            matched_skills=[{"skill_id": skill_id, "name": "matched"}],
            missing_skills=[{"skill_id": skill_id + 1000, "name": "missing"}],
            gap_analysis_summary="One bounded gap",
            algorithm_version="hybrid-v1",
            embedding_model="BAAI/bge-m3",
            embedding_preprocessing_version="resume-text-v1",
            status="COMPLETED",
            error_message=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            calculated_at=datetime.now(UTC),
        )
        setup.add(match)
        await setup.commit()

    sources = {name: (docx_bytes if name == "hr_docx" else PDF_BYTES) for name in rows}
    for name, row in rows.items():
        if name != "missing":
            await storage.put_if_absent(row.storage_key, sources[name])
    corrupt_path = storage.root.joinpath(*rows["corrupt"].storage_key.split("/"))
    corrupt_path.write_bytes(b"X" * len(PDF_BYTES))
    storage.failures[rows["io_error"].storage_key] = PermissionError(
        "private-root/secret-resume.pdf"
    )

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    def override_settings() -> Settings:
        return settings

    def override_storage() -> ControlledLocalStorage:
        return storage

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_settings] = override_settings
    app.dependency_overrides[resume_endpoint.get_resume_storage] = override_storage
    transport = ASGITransport(app=app)
    harness: HTTPDownloadHarness | None = None
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            harness = HTTPDownloadHarness(
                client=client,
                session_factory=factory,
                settings=settings,
                storage=storage,
                users={name: value.id for name, value in users.items()},
                resumes={name: value.id for name, value in rows.items()},
                sources=sources,
                job_id=job.id,
                match_id=match.id,
                skill_id=skill_id,
            )
            yield harness
    finally:
        app.dependency_overrides.clear()
        async with factory() as cleanup:
            await cleanup.execute(
                delete(User).where(User.id.in_([value.id for value in users.values()]))
            )
            await cleanup.commit()
        await engine.dispose()


def assert_binary_response(response: Any, content: bytes, media_type: str) -> None:
    assert response.status_code == 200
    assert response.content == content
    assert response.headers["content-type"] == media_type
    assert response.headers["content-length"] == str(len(content))
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "filename*=UTF-8''" in disposition
    assert "\r" not in disposition and "\n" not in disposition
    assert "application/json" not in response.headers["content-type"]


@pytest.mark.asyncio
async def test_real_http_owner_admin_mimes_headers_and_all_parse_states(
    http_download_postgres: HTTPDownloadHarness,
) -> None:
    harness = http_download_postgres
    pending_response: Any | None = None
    for name in ("pending", "processing", "parsed", "failed"):
        response = await harness.client.get(
            f"/api/v1/resumes/{harness.resumes[name]}/download",
            headers=harness.headers("candidate"),
        )
        assert_binary_response(response, PDF_BYTES, PDF_MIME_TYPE)
        if name == "pending":
            pending_response = response

    assert pending_response is not None
    disposition = pending_response.headers["content-disposition"]
    assert 'filename="' in disposition
    assert "%" in disposition
    assert disposition.count(";") == 2
    assert disposition.count('"') == 2
    assert "\\" not in disposition
    assert "%0D" not in disposition and "%0A" not in disposition

    hr_response = await harness.client.get(
        f"/api/v1/resumes/{harness.resumes['hr_docx']}/download",
        headers=harness.headers("hr"),
    )
    assert_binary_response(hr_response, harness.sources["hr_docx"], DOCX_MIME_TYPE)

    admin_response = await harness.client.get(
        f"/api/v1/resumes/{harness.resumes['other']}/download",
        headers=harness.headers("admin"),
    )
    assert_binary_response(admin_response, PDF_BYTES, PDF_MIME_TYPE)


@pytest.mark.asyncio
async def test_real_http_auth_visibility_and_match_do_not_grant_download(
    http_download_postgres: HTTPDownloadHarness,
) -> None:
    harness = http_download_postgres
    candidate_resume = harness.resumes["parsed"]
    cases = (
        ({}, 401, "AUTHENTICATION_REQUIRED"),
        ({"Authorization": "Bearer invalid-token"}, 401, "INVALID_ACCESS_TOKEN"),
        (harness.headers("inactive"), 403, "ACCOUNT_INACTIVE"),
        (harness.headers("other"), 404, "RESUME_NOT_FOUND"),
        (harness.headers("hr"), 404, "RESUME_NOT_FOUND"),
    )
    for headers, status_code, code in cases:
        before = len(harness.storage.read_calls)
        response = await harness.client.get(
            f"/api/v1/resumes/{candidate_resume}/download",
            headers=headers,
        )
        assert response.status_code == status_code
        assert response.json()["error"]["code"] == code
        assert len(harness.storage.read_calls) == before

    for resume_id in (harness.resumes["deleted"], uuid.uuid4()):
        before = len(harness.storage.read_calls)
        response = await harness.client.get(
            f"/api/v1/resumes/{resume_id}/download",
            headers=harness.headers("candidate"),
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "RESUME_NOT_FOUND"
        assert len(harness.storage.read_calls) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "status_code", "code"),
    [
        ("corrupt", 404, "RESUME_NOT_FOUND"),
        ("missing", 404, "RESUME_NOT_FOUND"),
        ("io_error", 500, "INTERNAL_SERVER_ERROR"),
    ],
)
async def test_real_http_storage_failures_are_bounded_json_errors(
    http_download_postgres: HTTPDownloadHarness,
    name: str,
    status_code: int,
    code: str,
) -> None:
    harness = http_download_postgres
    response = await harness.client.get(
        f"/api/v1/resumes/{harness.resumes[name]}/download",
        headers=harness.headers("candidate"),
    )
    assert response.status_code == status_code
    assert response.headers["content-type"].startswith("application/json")
    payload = response.json()
    assert payload["error"]["code"] == code
    serialized = response.text
    assert "private-root" not in serialized
    assert "resumes/" not in serialized
    assert "secret-resume" not in serialized


def freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(sorted((key, freeze(item)) for key, item in value.items()))
    if isinstance(value, list):
        return tuple(freeze(item) for item in value)
    return value


def model_snapshot(value: Any) -> tuple[Any, ...]:
    return tuple(freeze(getattr(value, column.name)) for column in value.__table__.columns)


async def business_snapshot(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    resume_id: uuid.UUID,
    job_id: uuid.UUID,
    skill_id: int,
) -> tuple[Any, ...]:
    async with session_factory() as session:
        resume_row = await session.get(Resume, resume_id)
        job_row = await session.get(JobDescription, job_id)
        skill_row = await session.get(Skill, skill_id)
        assert resume_row is not None and job_row is not None and skill_row is not None

        async def rows(model: Any, predicate: Any, order: Any) -> tuple[Any, ...]:
            values = (await session.scalars(select(model).where(predicate).order_by(order))).all()
            return tuple(model_snapshot(value) for value in values)

        return (
            model_snapshot(resume_row),
            await rows(
                CandidateProfile,
                CandidateProfile.resume_id == resume_id,
                CandidateProfile.id,
            ),
            await rows(ResumeSkill, ResumeSkill.resume_id == resume_id, ResumeSkill.id),
            await rows(
                ResumeExperience,
                ResumeExperience.resume_id == resume_id,
                ResumeExperience.id,
            ),
            await rows(ResumeEducation, ResumeEducation.resume_id == resume_id, ResumeEducation.id),
            await rows(MatchResult, MatchResult.resume_id == resume_id, MatchResult.id),
            model_snapshot(job_row),
            await rows(JobSkill, JobSkill.job_id == job_id, JobSkill.id),
            model_snapshot(skill_row),
        )


@pytest.mark.asyncio
async def test_real_http_success_repeat_and_failure_leave_complete_business_snapshot_unchanged(
    http_download_postgres: HTTPDownloadHarness,
) -> None:
    harness = http_download_postgres
    resume_id = harness.resumes["parsed"]
    before = await business_snapshot(
        harness.session_factory,
        resume_id=resume_id,
        job_id=harness.job_id,
        skill_id=harness.skill_id,
    )
    source_before = await harness.storage.read_bytes(f"resumes/{resume_id}/source")

    for _ in range(2):
        response = await harness.client.get(
            f"/api/v1/resumes/{resume_id}/download",
            headers=harness.headers("candidate"),
        )
        assert_binary_response(response, PDF_BYTES, PDF_MIME_TYPE)

    source_path = harness.storage.root.joinpath("resumes", str(resume_id), "source")
    corrupt_source = b"X" * len(source_before)
    source_path.write_bytes(corrupt_source)
    try:
        failure = await harness.client.get(
            f"/api/v1/resumes/{resume_id}/download",
            headers=harness.headers("candidate"),
        )
        assert failure.status_code == 404
        assert source_path.read_bytes() == corrupt_source
    finally:
        source_path.write_bytes(source_before)

    after = await business_snapshot(
        harness.session_factory,
        resume_id=resume_id,
        job_id=harness.job_id,
        skill_id=harness.skill_id,
    )
    source_after = await harness.storage.read_bytes(f"resumes/{resume_id}/source")
    assert after == before
    assert source_after == source_before == PDF_BYTES
