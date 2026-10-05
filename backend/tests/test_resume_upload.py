from __future__ import annotations

import hashlib
import io
import uuid
import zipfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.api.v1.endpoints import resumes as resume_endpoint
from app.core.config import Settings, get_settings
from app.core.database import get_db_session
from app.core.exceptions import APIError
from app.core.idempotency import (
    APP_IDEMPOTENCY_NAMESPACE,
    RESUME_UPLOAD_ROUTE,
    derive_idempotent_resource_id,
)
from app.core.security import create_access_token
from app.models.resume import Resume
from app.models.user import User
from app.services.resume_dispatcher import get_resume_parse_dispatcher
from app.services.resume_service import MAX_RESUME_FILE_SIZE, upload_resume, validate_resume_upload
from app.storage.resume_storage import LocalResumeStorage, PutIfAbsentResult
from main import app

TEST_SECRET = "resume-upload-unit-test-jwt-secret"
PDF_BYTES = b"%PDF-1.7\n% resume upload test\n%%EOF\n"


def make_docx() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            "<Types><Override PartName='/word/document.xml' "
            "ContentType='application/vnd.openxmlformats-officedocument.wordprocessingml."
            "document.main+xml'/></Types>",
        )
        archive.writestr(
            "_rels/.rels",
            "<Relationships><Relationship "
            "Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
            "officeDocument' Target='word/document.xml'/></Relationships>",
        )
        archive.writestr("word/document.xml", "<w:document/>")
    return output.getvalue()


def make_user(role: str = "CANDIDATE", *, label: str = "actor") -> User:
    now = datetime.now(UTC)
    return User(
        id=uuid.uuid5(uuid.NAMESPACE_URL, f"resume-upload-{label}"),
        email=f"{label}@example.com",
        password_hash="not-used",
        full_name="Upload Actor",
        phone_number=None,
        role=role,
        is_active=True,
        created_at=now,
        updated_at=now,
    )


class FakeResult:
    def __init__(self, actor: User) -> None:
        self.actor = actor

    def scalar_one_or_none(self) -> User:
        return self.actor


class MemorySession:
    def __init__(self, actor: User) -> None:
        self.actor = actor
        self.rows: dict[uuid.UUID, Resume] = {}
        self.pending: Resume | None = None
        self.events: list[str] = []
        self.commit_error: Exception | None = None
        self.race_winner: Resume | None = None

    async def execute(self, _: Any) -> FakeResult:
        return FakeResult(self.actor)

    async def get(self, _: type[Resume], resume_id: uuid.UUID, **__: Any) -> Resume | None:
        return self.rows.get(resume_id)

    def add(self, resume: Resume) -> None:
        self.pending = resume
        self.events.append("add")

    async def commit(self) -> None:
        self.events.append("commit")
        if self.commit_error is not None:
            error = self.commit_error
            self.commit_error = None
            raise error
        assert self.pending is not None
        self.rows[self.pending.id] = self.pending
        self.pending = None

    async def rollback(self) -> None:
        self.events.append("rollback")
        self.pending = None
        if self.race_winner is not None:
            self.rows[self.race_winner.id] = self.race_winner


class RecordingDispatcher:
    def __init__(self, session: MemorySession, *, fail: bool = False) -> None:
        self.session = session
        self.fail = fail
        self.calls: list[tuple[uuid.UUID, int]] = []

    async def dispatch(self, resume_id: uuid.UUID, revision: int) -> None:
        assert "commit" in self.session.events
        self.calls.append((resume_id, revision))
        self.session.events.append("dispatch")
        if self.fail:
            raise RuntimeError("dispatcher unavailable")


class FailingStorage:
    async def put_if_absent(self, key: str, data: bytes) -> PutIfAbsentResult:
        del key, data
        raise OSError("storage unavailable")

    async def read_bytes(self, key: str) -> bytes:
        raise AssertionError(key)


@dataclass
class UploadContext:
    client: AsyncClient
    actor: User
    session: MemorySession
    storage: LocalResumeStorage
    dispatcher: RecordingDispatcher
    settings: Settings
    storage_root: Path

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {create_access_token(self.actor.id, self.settings)}",
            "Idempotency-Key": str(uuid.uuid5(uuid.NAMESPACE_URL, "default-upload-key")),
        }


@pytest_asyncio.fixture
async def upload_api(tmp_path: Path) -> AsyncIterator[UploadContext]:
    actor = make_user()
    session = MemorySession(actor)
    storage_root = tmp_path / "storage"
    storage = LocalResumeStorage(storage_root)
    dispatcher = RecordingDispatcher(session)
    settings = Settings(
        _env_file=None,
        app_env="test",
        jwt_secret_key=TEST_SECRET,
        resume_storage_root=storage_root,
    )

    async def override_session() -> AsyncIterator[MemorySession]:
        yield session

    def override_settings() -> Settings:
        return settings

    def override_storage() -> LocalResumeStorage:
        return storage

    def override_dispatcher() -> RecordingDispatcher:
        return dispatcher

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_settings] = override_settings
    app.dependency_overrides[resume_endpoint.get_resume_storage] = override_storage
    app.dependency_overrides[get_resume_parse_dispatcher] = override_dispatcher
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield UploadContext(client, actor, session, storage, dispatcher, settings, storage_root)
    app.dependency_overrides.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("role", "filename", "payload"),
    [
        ("CANDIDATE", "candidate.pdf", PDF_BYTES),
        ("HR", "hr.pdf", PDF_BYTES),
        ("CANDIDATE", "candidate.docx", make_docx()),
        ("HR", "hr.docx", make_docx()),
    ],
)
async def test_candidate_and_hr_upload_real_pdf_and_docx_bytes(
    upload_api: UploadContext, role: str, filename: str, payload: bytes
) -> None:
    upload_api.actor.role = role
    response = await upload_api.client.post(
        "/api/v1/resumes/upload",
        headers=upload_api.headers,
        files={"file": (filename, payload, "application/octet-stream")},
    )
    assert response.status_code == 202
    resume_id = uuid.UUID(response.json()["data"]["resume_id"])
    stored = await upload_api.storage.read_bytes(f"resumes/{resume_id}/source")
    assert stored == payload


@pytest.mark.asyncio
async def test_upload_requires_auth_and_denies_admin(upload_api: UploadContext) -> None:
    unauthenticated = await upload_api.client.post(
        "/api/v1/resumes/upload",
        headers={"Idempotency-Key": str(uuid.uuid4())},
        files={"file": ("resume.pdf", PDF_BYTES)},
    )
    upload_api.actor.role = "ADMIN"
    forbidden = await upload_api.client.post(
        "/api/v1/resumes/upload", headers=upload_api.headers, files={"file": ("a.pdf", PDF_BYTES)}
    )
    assert unauthenticated.status_code == 401
    assert forbidden.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("header", [None, "not-a-uuid"])
async def test_upload_rejects_missing_or_invalid_idempotency_key(
    upload_api: UploadContext, header: str | None
) -> None:
    headers = {"Authorization": upload_api.headers["Authorization"]}
    if header is not None:
        headers["Idempotency-Key"] = header
    response = await upload_api.client.post(
        "/api/v1/resumes/upload", headers=headers, files={"file": ("a.pdf", PDF_BYTES)}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "payload", "expected_status"),
    [
        ("empty.pdf", b"", 422),
        ("large.pdf", b"%PDF-" + b"x" * (MAX_RESUME_FILE_SIZE - 4), 413),
        ("fake.pdf", b"not a pdf", 415),
        ("fake.docx", b"not a zip", 415),
    ],
    ids=["empty", "over-five-mib", "fake-pdf", "fake-docx"],
)
async def test_upload_rejects_invalid_size_and_actual_format(
    upload_api: UploadContext, filename: str, payload: bytes, expected_status: int
) -> None:
    response = await upload_api.client.post(
        "/api/v1/resumes/upload",
        headers=upload_api.headers,
        files={"file": (filename, payload)},
    )
    assert response.status_code == expected_status
    assert upload_api.session.rows == {}


@pytest.mark.asyncio
async def test_upload_rejects_arbitrary_non_word_zip(upload_api: UploadContext) -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("notes.txt", "not a Word document")
    response = await upload_api.client.post(
        "/api/v1/resumes/upload",
        headers=upload_api.headers,
        files={"file": ("fake.docx", output.getvalue())},
    )
    assert response.status_code == 415


def test_exact_five_mib_file_is_allowed_but_one_extra_byte_is_rejected() -> None:
    exact = b"%PDF-" + b"x" * (MAX_RESUME_FILE_SIZE - 5)
    assert len(exact) == MAX_RESUME_FILE_SIZE
    assert validate_resume_upload("limit.pdf", exact)[1] == "application/pdf"
    with pytest.raises(APIError) as raised:
        validate_resume_upload("large.pdf", exact + b"x")
    assert raised.value.status_code == 413


@pytest.mark.asyncio
async def test_upload_persists_exact_fingerprint_deterministic_id_and_initial_state(
    upload_api: UploadContext,
) -> None:
    key = uuid.UUID("D055AECB-52F8-4F3D-AB55-8DC972B20887")
    headers = upload_api.headers | {"Idempotency-Key": str(key).upper()}
    response = await upload_api.client.post(
        "/api/v1/resumes/upload",
        headers=headers,
        files={"file": ("original.pdf", PDF_BYTES, "text/plain")},
    )
    expected_id = uuid.uuid5(
        APP_IDEMPOTENCY_NAMESPACE,
        f"{upload_api.actor.id}\n{RESUME_UPLOAD_ROUTE}\n{str(key).lower()}",
    )
    assert (
        derive_idempotent_resource_id(upload_api.actor.id, RESUME_UPLOAD_ROUTE, key) == expected_id
    )
    assert response.status_code == 202
    assert response.json() == {
        "success": True,
        "data": {
            "resume_id": str(expected_id),
            "revision": 1,
            "file_name": "original.pdf",
            "file_size": len(PDF_BYTES),
            "parsing_status": "PENDING",
        },
    }
    row = upload_api.session.rows[expected_id]
    assert row.create_request_fingerprint == hashlib.sha256(PDF_BYTES).hexdigest()
    assert row.storage_key == f"resumes/{expected_id}/source"
    assert row.mime_type == "application/pdf"
    assert row.owner_user_id == upload_api.actor.id
    assert upload_api.session.events == ["add", "commit", "dispatch"]


@pytest.mark.asyncio
async def test_same_key_same_file_is_idempotent_without_overwrite_or_revision_reset(
    upload_api: UploadContext,
) -> None:
    first = await upload_api.client.post(
        "/api/v1/resumes/upload",
        headers=upload_api.headers,
        files={"file": ("first.pdf", PDF_BYTES)},
    )
    resume_id = uuid.UUID(first.json()["data"]["resume_id"])
    row = upload_api.session.rows[resume_id]
    row.revision = 7
    row.parsing_status = "PARSED"
    before = (upload_api.storage_root / "resumes" / str(resume_id) / "source").stat().st_mtime_ns
    second = await upload_api.client.post(
        "/api/v1/resumes/upload",
        headers=upload_api.headers,
        files={"file": ("ignored.pdf", PDF_BYTES)},
    )
    after = (upload_api.storage_root / "resumes" / str(resume_id) / "source").stat().st_mtime_ns
    assert second.status_code == 202
    assert second.json()["data"]["revision"] == 7
    assert second.json()["data"]["parsing_status"] == "PARSED"
    assert len(upload_api.session.rows) == 1
    assert before == after
    assert upload_api.dispatcher.calls == [(resume_id, 1)]


@pytest.mark.asyncio
async def test_same_key_different_file_returns_canonical_conflict_without_mutation(
    upload_api: UploadContext,
) -> None:
    await upload_api.client.post(
        "/api/v1/resumes/upload", headers=upload_api.headers, files={"file": ("a.pdf", PDF_BYTES)}
    )
    response = await upload_api.client.post(
        "/api/v1/resumes/upload",
        headers=upload_api.headers,
        files={"file": ("b.pdf", PDF_BYTES + b"different")},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert len(upload_api.session.rows) == 1


@pytest.mark.asyncio
async def test_same_key_for_different_users_creates_different_ids(tmp_path: Path) -> None:
    key = uuid.uuid4()
    storage = LocalResumeStorage(tmp_path)
    first_user = make_user(label="one")
    second_user = make_user(label="two")
    first_session = MemorySession(first_user)
    second_session = MemorySession(second_user)
    first = await upload_resume(
        first_session,
        current_user=first_user,
        idempotency_key=key,
        filename="a.pdf",
        data=PDF_BYTES,
        storage=storage,
        dispatcher=RecordingDispatcher(first_session),
    )
    second = await upload_resume(
        second_session,
        current_user=second_user,
        idempotency_key=key,
        filename="a.pdf",
        data=PDF_BYTES,
        storage=storage,
        dispatcher=RecordingDispatcher(second_session),
    )
    assert first.id != second.id


@pytest.mark.asyncio
async def test_orphan_same_hash_is_reused_and_different_hash_is_preserved(tmp_path: Path) -> None:
    user = make_user()
    key = uuid.uuid4()
    resume_id = derive_idempotent_resource_id(user.id, RESUME_UPLOAD_ROUTE, key)
    storage = LocalResumeStorage(tmp_path)
    storage_key = f"resumes/{resume_id}/source"
    await storage.put_if_absent(storage_key, PDF_BYTES)
    session = MemorySession(user)
    resume = await upload_resume(
        session,
        current_user=user,
        idempotency_key=key,
        filename="a.pdf",
        data=PDF_BYTES,
        storage=storage,
        dispatcher=RecordingDispatcher(session),
    )
    assert resume.id == resume_id

    conflicting_user = make_user(label="conflict")
    conflicting_key = uuid.uuid4()
    conflicting_id = derive_idempotent_resource_id(
        conflicting_user.id, RESUME_UPLOAD_ROUTE, conflicting_key
    )
    conflicting_key_path = f"resumes/{conflicting_id}/source"
    await storage.put_if_absent(conflicting_key_path, PDF_BYTES)
    with pytest.raises(APIError) as raised:
        await upload_resume(
            MemorySession(conflicting_user),
            current_user=conflicting_user,
            idempotency_key=conflicting_key,
            filename="b.pdf",
            data=PDF_BYTES + b"new",
            storage=storage,
            dispatcher=RecordingDispatcher(MemorySession(conflicting_user)),
        )
    assert getattr(raised.value, "code", None) == "IDEMPOTENCY_KEY_REUSED"
    assert await storage.read_bytes(conflicting_key_path) == PDF_BYTES


@pytest.mark.asyncio
async def test_storage_failure_creates_no_row_and_db_failure_may_leave_orphan(
    tmp_path: Path,
) -> None:
    user = make_user()
    session = MemorySession(user)
    with pytest.raises(OSError):
        await upload_resume(
            session,
            current_user=user,
            idempotency_key=uuid.uuid4(),
            filename="a.pdf",
            data=PDF_BYTES,
            storage=FailingStorage(),
            dispatcher=RecordingDispatcher(session),
        )
    assert session.rows == {} and session.pending is None

    key = uuid.uuid4()
    storage = LocalResumeStorage(tmp_path)
    failed_session = MemorySession(user)
    failed_session.commit_error = SQLAlchemyError("database unavailable")
    with pytest.raises(SQLAlchemyError):
        await upload_resume(
            failed_session,
            current_user=user,
            idempotency_key=key,
            filename="a.pdf",
            data=PDF_BYTES,
            storage=storage,
            dispatcher=RecordingDispatcher(failed_session),
        )
    resume_id = derive_idempotent_resource_id(user.id, RESUME_UPLOAD_ROUTE, key)
    assert await storage.read_bytes(f"resumes/{resume_id}/source") == PDF_BYTES
    assert failed_session.rows == {}


@pytest.mark.asyncio
async def test_dispatch_failure_keeps_committed_pending_and_pending_retry_redispatches(
    tmp_path: Path,
) -> None:
    user = make_user()
    session = MemorySession(user)
    dispatcher = RecordingDispatcher(session, fail=True)
    key = uuid.uuid4()
    first = await upload_resume(
        session,
        current_user=user,
        idempotency_key=key,
        filename="a.pdf",
        data=PDF_BYTES,
        storage=LocalResumeStorage(tmp_path),
        dispatcher=dispatcher,
    )
    assert first.id in session.rows and first.parsing_status == "PENDING"
    second = await upload_resume(
        session,
        current_user=user,
        idempotency_key=key,
        filename="a.pdf",
        data=PDF_BYTES,
        storage=LocalResumeStorage(tmp_path),
        dispatcher=dispatcher,
    )
    assert second is first
    assert dispatcher.calls == [(first.id, 1), (first.id, 1)]


@pytest.mark.asyncio
async def test_dispatch_failure_still_returns_202_from_api(upload_api: UploadContext) -> None:
    upload_api.dispatcher.fail = True
    response = await upload_api.client.post(
        "/api/v1/resumes/upload",
        headers=upload_api.headers,
        files={"file": ("resume.pdf", PDF_BYTES)},
    )
    assert response.status_code == 202
    resume_id = uuid.UUID(response.json()["data"]["resume_id"])
    assert upload_api.session.rows[resume_id].parsing_status == "PENDING"


@pytest.mark.asyncio
async def test_deterministic_primary_key_race_rolls_back_and_returns_winner(
    tmp_path: Path,
) -> None:
    user = make_user()
    key = uuid.uuid4()
    storage = LocalResumeStorage(tmp_path)
    winning_session = MemorySession(user)
    winner = await upload_resume(
        winning_session,
        current_user=user,
        idempotency_key=key,
        filename="winner.pdf",
        data=PDF_BYTES,
        storage=storage,
        dispatcher=RecordingDispatcher(winning_session),
    )

    losing_session = MemorySession(user)
    losing_session.commit_error = IntegrityError("INSERT", {}, RuntimeError("duplicate key"))
    losing_session.race_winner = winner
    dispatcher = RecordingDispatcher(losing_session)
    recovered = await upload_resume(
        losing_session,
        current_user=user,
        idempotency_key=key,
        filename="loser.pdf",
        data=PDF_BYTES,
        storage=storage,
        dispatcher=dispatcher,
    )
    assert recovered is winner
    assert losing_session.events == ["add", "commit", "rollback", "dispatch"]
    assert await losing_session.get(Resume, winner.id) is winner


@pytest.mark.asyncio
async def test_filename_cannot_escape_storage_root(upload_api: UploadContext) -> None:
    response = await upload_api.client.post(
        "/api/v1/resumes/upload",
        headers=upload_api.headers,
        files={"file": ("../escape.pdf", PDF_BYTES)},
    )
    assert response.status_code == 422
    assert not (upload_api.storage_root.parent / "escape.pdf").exists()


def test_upload_openapi_and_existing_resume_routes_are_registered_once() -> None:
    schema = app.openapi()
    upload = schema["paths"]["/api/v1/resumes/upload"]["post"]
    assert "multipart/form-data" in upload["requestBody"]["content"]
    assert "202" in upload["responses"]
    assert set(schema["paths"]["/api/v1/resumes"]) == {"get"}
    assert set(schema["paths"]["/api/v1/resumes/{id}"]) == {"get", "delete"}
    assert set(schema["paths"]["/api/v1/resumes/{id}/status"]) == {"get"}
