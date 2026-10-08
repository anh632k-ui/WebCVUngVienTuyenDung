from __future__ import annotations

import hashlib
import os
import uuid
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import pytest

from app.core.exceptions import APIError
from app.models.user import User
from app.services.resume_service import (
    DOCX_MIME_TYPE,
    MAX_RESUME_FILE_SIZE,
    PDF_MIME_TYPE,
    build_download_content_disposition,
    download_resume_source,
)
from app.storage.resume_storage import LocalResumeStorage


class Result:
    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self.row = row

    def one_or_none(self) -> tuple[Any, ...] | None:
        return self.row


class DownloadSession:
    def __init__(self, metadata: tuple[Any, ...] | None, *, still_visible: bool = True) -> None:
        self.metadata = metadata
        self.still_visible = still_visible
        self.execute_calls = 0
        self.rollback_calls = 0
        self.no_autoflush = nullcontext()

    async def execute(self, statement: Any) -> Result:
        del statement
        self.execute_calls += 1
        return Result(self.metadata)

    async def scalar(self, statement: Any) -> uuid.UUID | None:
        del statement
        return uuid.uuid4() if self.still_visible else None

    async def rollback(self) -> None:
        self.rollback_calls += 1


class RecordingStorage:
    def __init__(self, content: bytes | BaseException) -> None:
        self.content = content
        self.calls: list[tuple[str, int, int]] = []

    async def read_bytes_bounded(
        self,
        key: str,
        *,
        expected_size: int,
        maximum_size: int,
    ) -> bytes:
        self.calls.append((key, expected_size, maximum_size))
        if isinstance(self.content, BaseException):
            raise self.content
        return self.content


def make_actor(role: str = "CANDIDATE") -> User:
    return User(id=uuid.uuid4(), role=role)


def metadata_for(resume_id: uuid.UUID, content: bytes) -> tuple[Any, ...]:
    return (
        'Ứng viên "senior";\r\n.pdf',
        f"resumes/{resume_id}/source",
        len(content),
        PDF_MIME_TYPE,
        hashlib.sha256(content).hexdigest(),
    )


@pytest.mark.asyncio
async def test_download_service_returns_exact_verified_bytes_and_ends_db_transactions() -> None:
    resume_id = uuid.uuid4()
    content = b"%PDF-1.7\noriginal bytes\x00\xff"
    session = DownloadSession(metadata_for(resume_id, content))
    storage = RecordingStorage(content)

    result = await download_resume_source(
        session,  # type: ignore[arg-type]
        current_user=make_actor(),
        resume_id=resume_id,
        storage=storage,  # type: ignore[arg-type]
    )

    assert result.content == content
    assert result.media_type == PDF_MIME_TYPE
    assert storage.calls == [(f"resumes/{resume_id}/source", len(content), MAX_RESUME_FILE_SIZE)]
    assert session.rollback_calls == 2
    assert "\r" not in result.content_disposition
    assert "\n" not in result.content_disposition
    assert 'filename="' in result.content_disposition
    assert "filename*=UTF-8''" in result.content_disposition


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata_change",
    [
        lambda row: (*row[:1], "../outside", *row[2:]),
        lambda row: (*row[:2], MAX_RESUME_FILE_SIZE + 1, *row[3:]),
        lambda row: (*row[:3], "text/plain", *row[4:]),
    ],
)
async def test_invalid_database_metadata_is_generic_not_found_without_storage_read(
    metadata_change: Any,
) -> None:
    resume_id = uuid.uuid4()
    content = b"%PDF-test"
    session = DownloadSession(metadata_change(metadata_for(resume_id, content)))
    storage = RecordingStorage(content)

    with pytest.raises(APIError) as captured:
        await download_resume_source(
            session,  # type: ignore[arg-type]
            current_user=make_actor(),
            resume_id=resume_id,
            storage=storage,  # type: ignore[arg-type]
        )

    assert (captured.value.status_code, captured.value.code) == (404, "RESUME_NOT_FOUND")
    assert storage.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stored",
    [b"truncated", b"%PDF-same-size-wrong!!", FileNotFoundError("private storage path")],
)
async def test_missing_or_corrupt_object_is_generic_not_found(
    stored: bytes | BaseException,
) -> None:
    resume_id = uuid.uuid4()
    content = b"%PDF-canonical-content"
    session = DownloadSession(metadata_for(resume_id, content))
    storage = RecordingStorage(stored)

    with pytest.raises(APIError) as captured:
        await download_resume_source(
            session,  # type: ignore[arg-type]
            current_user=make_actor(),
            resume_id=resume_id,
            storage=storage,  # type: ignore[arg-type]
        )

    assert (captured.value.status_code, captured.value.code) == (404, "RESUME_NOT_FOUND")
    assert "private storage path" not in captured.value.message


@pytest.mark.asyncio
async def test_final_visibility_guard_rejects_soft_delete_during_storage_read() -> None:
    resume_id = uuid.uuid4()
    content = b"%PDF-race"
    session = DownloadSession(metadata_for(resume_id, content), still_visible=False)

    with pytest.raises(APIError) as captured:
        await download_resume_source(
            session,  # type: ignore[arg-type]
            current_user=make_actor("ADMIN"),
            resume_id=resume_id,
            storage=RecordingStorage(content),  # type: ignore[arg-type]
        )

    assert (captured.value.status_code, captured.value.code) == (404, "RESUME_NOT_FOUND")
    assert session.rollback_calls == 2


@pytest.mark.asyncio
async def test_unexpected_storage_io_failure_is_generic_internal_error() -> None:
    resume_id = uuid.uuid4()
    content = b"%PDF-storage-error"
    session = DownloadSession(metadata_for(resume_id, content))

    with pytest.raises(APIError) as captured:
        await download_resume_source(
            session,  # type: ignore[arg-type]
            current_user=make_actor(),
            resume_id=resume_id,
            storage=RecordingStorage(PermissionError("secret path")),  # type: ignore[arg-type]
        )

    assert (captured.value.status_code, captured.value.code) == (
        500,
        "INTERNAL_SERVER_ERROR",
    )
    assert "secret path" not in captured.value.message


@pytest.mark.parametrize(
    "filename",
    ["résumé ứng viên.pdf", 'quote"slash\\semi;line\r\n\x85\u202e.pdf', "..."],
)
def test_content_disposition_is_injection_safe_and_has_utf8_fallback(filename: str) -> None:
    value = build_download_content_disposition(
        filename,
        resume_id=uuid.UUID(int=1),
        media_type=DOCX_MIME_TYPE,
    )
    assert value.startswith('attachment; filename="')
    assert "filename*=UTF-8''" in value
    assert "\r" not in value
    assert "\n" not in value
    assert "\x85" not in value
    assert "\u202e" not in value
    assert "\\" not in value
    assert ";line" not in value


@pytest.mark.asyncio
async def test_local_storage_bounded_read_does_not_create_missing_directories(
    tmp_path: Path,
) -> None:
    root = tmp_path / "storage"
    storage = LocalResumeStorage(root)
    missing_parent = root / "resumes" / str(uuid.uuid4())

    with pytest.raises(FileNotFoundError):
        await storage.read_bytes_bounded(
            f"resumes/{missing_parent.name}/source",
            expected_size=10,
            maximum_size=MAX_RESUME_FILE_SIZE,
        )
    assert not missing_parent.exists()


@pytest.mark.asyncio
async def test_local_storage_rejects_unsafe_nonregular_and_symlink_reads(tmp_path: Path) -> None:
    storage = LocalResumeStorage(tmp_path / "storage")
    for key in ("../source", "/absolute", "C:/source", "nested\\source", "bad\x00key"):
        with pytest.raises(ValueError):
            await storage.read_bytes_bounded(key, expected_size=1, maximum_size=10)

    directory_object = tmp_path / "storage" / "directory-object"
    directory_object.mkdir()
    with pytest.raises(ValueError):
        await storage.read_bytes_bounded("directory-object", expected_size=1, maximum_size=10)

    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"private")
    link = tmp_path / "storage" / "linked.pdf"
    try:
        os.symlink(outside, link)
    except OSError as error:
        pytest.fail(f"Filesystem symlink test unavailable: {type(error).__name__}", pytrace=False)
    with pytest.raises(ValueError):
        await storage.read_bytes_bounded("linked.pdf", expected_size=7, maximum_size=10)

    outside_directory = tmp_path / "outside-directory"
    outside_directory.mkdir()
    (outside_directory / "source").write_bytes(b"private")
    linked_parent = tmp_path / "storage" / "linked-parent"
    try:
        os.symlink(outside_directory, linked_parent, target_is_directory=True)
    except OSError as error:
        pytest.fail(
            f"Filesystem directory-symlink test unavailable: {type(error).__name__}",
            pytrace=False,
        )
    with pytest.raises(ValueError):
        await storage.read_bytes_bounded("linked-parent/source", expected_size=7, maximum_size=10)


@pytest.mark.asyncio
async def test_local_storage_reads_at_most_expected_size_plus_one(tmp_path: Path) -> None:
    storage = LocalResumeStorage(tmp_path / "storage")
    target = tmp_path / "storage" / "resumes" / "large" / "source"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x" * 100)

    content = await storage.read_bytes_bounded(
        "resumes/large/source",
        expected_size=10,
        maximum_size=MAX_RESUME_FILE_SIZE,
    )
    assert content == b"x" * 11


@pytest.mark.asyncio
async def test_local_storage_round_trip_is_exact_and_never_mutates_source(tmp_path: Path) -> None:
    storage = LocalResumeStorage(tmp_path / "storage")
    resume_id = uuid.uuid4()
    key = f"resumes/{resume_id}/source"
    original = b"%PDF-1.7\nexact\x00source\xff"
    stored = await storage.put_if_absent(key, original)

    downloaded = await storage.read_bytes_bounded(
        key,
        expected_size=len(original),
        maximum_size=MAX_RESUME_FILE_SIZE,
    )
    assert stored.created
    assert stored.fingerprint == hashlib.sha256(original).hexdigest()
    assert downloaded == original
    assert await storage.read_bytes(key) == original


@pytest.mark.asyncio
async def test_local_storage_oversized_object_is_bounded_at_absolute_limit_plus_one(
    tmp_path: Path,
) -> None:
    storage = LocalResumeStorage(tmp_path / "storage")
    target = tmp_path / "storage" / "resumes" / "oversized" / "source"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x" * (MAX_RESUME_FILE_SIZE + 2))

    content = await storage.read_bytes_bounded(
        "resumes/oversized/source",
        expected_size=MAX_RESUME_FILE_SIZE,
        maximum_size=MAX_RESUME_FILE_SIZE,
    )
    assert len(content) == MAX_RESUME_FILE_SIZE + 1
