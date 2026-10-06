from __future__ import annotations

import hashlib
import io
import logging
import uuid
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from xml.etree import ElementTree

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.exceptions import APIError
from app.core.idempotency import RESUME_UPLOAD_ROUTE, derive_idempotent_resource_id
from app.models.resume import CandidateProfile, Resume, ResumeEducation, ResumeExperience
from app.models.skill import ResumeSkill
from app.models.user import User
from app.schemas.auth_schema import UserRole
from app.schemas.resume_schema import ParsingStatus
from app.services.resume_dispatcher import ResumeParseDispatcher
from app.storage.resume_storage import ResumeStorage

logger = logging.getLogger(__name__)

MAX_RESUME_FILE_SIZE = 5_242_880
PDF_MIME_TYPE = "application/pdf"
DOCX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
DOCX_REQUIRED_ENTRIES = {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}
DOCX_DOCUMENT_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
)
DOCX_OFFICE_DOCUMENT_RELATIONSHIP = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
)
DOCX_STRICT_OFFICE_DOCUMENT_RELATIONSHIP = (
    "http://purl.oclc.org/ooxml/officeDocument/relationships/officeDocument"
)
DOCX_WORDPROCESSINGML_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
DOCX_STRICT_WORDPROCESSINGML_NAMESPACE = "http://purl.oclc.org/ooxml/wordprocessingml/main"
DOCX_RELATIONSHIP_NAMESPACES = {
    DOCX_OFFICE_DOCUMENT_RELATIONSHIP: DOCX_WORDPROCESSINGML_NAMESPACE,
    DOCX_STRICT_OFFICE_DOCUMENT_RELATIONSHIP: DOCX_STRICT_WORDPROCESSINGML_NAMESPACE,
}
DOCX_METADATA_MAX_SIZE = 64 * 1024
# Resume DOCX XML is normally far smaller; this generous ceiling prevents
# highly-compressed document parts from expanding without bound during validation.
DOCX_DOCUMENT_XML_MAX_SIZE = 16 * 1024 * 1024
DOCX_XML_VALIDATION_CHUNK_SIZE = 64 * 1024


@dataclass
class ResumeAggregate:
    resume: Resume
    candidate_profile: CandidateProfile | None
    skills: list[ResumeSkill]
    experiences: list[ResumeExperience]
    educations: list[ResumeEducation]


def _validate_filename(filename: str | None) -> str:
    if filename is None or not filename.strip() or len(filename) > 255:
        raise APIError(422, "INVALID_FILE", "A valid upload filename is required")
    if "\x00" in filename or "/" in filename or "\\" in filename:
        raise APIError(422, "INVALID_FILE", "Upload filename must not contain path components")
    return filename


def _word_document_root_namespace(
    archive: zipfile.ZipFile,
    document: zipfile.ZipInfo,
) -> str | None:
    parser = ElementTree.XMLPullParser(events=("start", "end"))
    bytes_read = 0
    root_namespace: str | None = None
    root_seen = False
    with archive.open(document) as source:
        while True:
            remaining = DOCX_DOCUMENT_XML_MAX_SIZE - bytes_read
            chunk = source.read(min(DOCX_XML_VALIDATION_CHUNK_SIZE, remaining + 1))
            if not chunk:
                break
            bytes_read += len(chunk)
            if bytes_read > DOCX_DOCUMENT_XML_MAX_SIZE:
                return None
            parser.feed(chunk)
            events = cast(Iterator[tuple[str, Any]], parser.read_events())
            for event, element in events:
                if not root_seen:
                    if event != "start" or not isinstance(element.tag, str):
                        return None
                    if not element.tag.startswith("{"):
                        return None
                    namespace, separator, local_name = element.tag[1:].partition("}")
                    if separator != "}" or local_name != "document":
                        return None
                    root_namespace = namespace
                    root_seen = True
                if event == "end":
                    element.clear()
    parser.close()
    return root_namespace if root_seen else None


def _is_docx(data: bytes) -> bool:
    stream = io.BytesIO(data)
    if not zipfile.is_zipfile(stream):
        return False
    try:
        with zipfile.ZipFile(stream) as archive:
            names = set(archive.namelist())
            if not DOCX_REQUIRED_ENTRIES.issubset(names):
                return False
            content_types = archive.getinfo("[Content_Types].xml")
            relationships = archive.getinfo("_rels/.rels")
            document = archive.getinfo("word/document.xml")
            if (
                content_types.file_size > DOCX_METADATA_MAX_SIZE
                or relationships.file_size > DOCX_METADATA_MAX_SIZE
                or content_types.flag_bits & 0x1
                or relationships.flag_bits & 0x1
                or document.flag_bits & 0x1
                or document.is_dir()
                or document.file_size > DOCX_DOCUMENT_XML_MAX_SIZE
            ):
                return False
            content_root = ElementTree.fromstring(archive.read(content_types))
            relationship_root = ElementTree.fromstring(archive.read(relationships))
            has_document_content_type = any(
                element.tag.rsplit("}", 1)[-1] == "Override"
                and element.attrib.get("PartName") == "/word/document.xml"
                and element.attrib.get("ContentType") == DOCX_DOCUMENT_CONTENT_TYPE
                for element in content_root
            )
            document_relationships = [
                element
                for element in relationship_root
                if element.tag.rsplit("}", 1)[-1] == "Relationship"
                and element.attrib.get("Target", "").lstrip("/") == "word/document.xml"
            ]
            if len(document_relationships) != 1:
                return False
            document_relationship = document_relationships[0]
            if document_relationship.attrib.get("TargetMode", "Internal") != "Internal":
                return False
            relationship_type = document_relationship.attrib.get("Type", "")
            expected_document_namespace = DOCX_RELATIONSHIP_NAMESPACES.get(relationship_type)
            if expected_document_namespace is None:
                return False
            document_namespace = _word_document_root_namespace(archive, document)
            return has_document_content_type and document_namespace == expected_document_namespace
    except (
        ElementTree.ParseError,
        KeyError,
        NotImplementedError,
        OSError,
        ValueError,
        zipfile.BadZipFile,
        RuntimeError,
    ):
        return False


def validate_resume_upload(filename: str | None, data: bytes) -> tuple[str, str]:
    validated_filename = _validate_filename(filename)
    if not data:
        raise APIError(422, "INVALID_FILE", "Resume file must not be empty")
    if len(data) > MAX_RESUME_FILE_SIZE:
        raise APIError(413, "FILE_TOO_LARGE", "Resume file exceeds the 5 MiB limit")
    if data.startswith(b"%PDF-"):
        return validated_filename, PDF_MIME_TYPE
    if _is_docx(data):
        return validated_filename, DOCX_MIME_TYPE
    raise APIError(415, "UNSUPPORTED_FILE_FORMAT", "Resume must be a valid PDF or DOCX file")


def _idempotency_conflict() -> APIError:
    return APIError(
        409,
        "IDEMPOTENCY_KEY_REUSED",
        "Idempotency-Key was reused with different resume content",
    )


async def _dispatch_if_pending(
    dispatcher: ResumeParseDispatcher,
    resume: Resume,
) -> None:
    if resume.parsing_status != ParsingStatus.PENDING.value:
        return
    try:
        await dispatcher.dispatch(resume.id, resume.revision)
    except Exception:  # noqa: BLE001 - persistence succeeds independently of best-effort dispatch
        logger.error(
            "resume_parse_dispatch_failed resume_id=%s revision=%s",
            resume.id,
            resume.revision,
        )


async def upload_resume(
    session: AsyncSession,
    *,
    current_user: User,
    idempotency_key: uuid.UUID,
    filename: str | None,
    data: bytes,
    storage: ResumeStorage,
    dispatcher: ResumeParseDispatcher,
) -> Resume:
    validated_filename, mime_type = validate_resume_upload(filename, data)
    fingerprint = hashlib.sha256(data).hexdigest()
    resume_id = derive_idempotent_resource_id(
        current_user.id,
        RESUME_UPLOAD_ROUTE,
        idempotency_key,
    )

    existing = await session.get(Resume, resume_id)
    if existing is not None:
        if existing.create_request_fingerprint != fingerprint:
            raise _idempotency_conflict()
        await _dispatch_if_pending(dispatcher, existing)
        return existing

    storage_key = f"resumes/{resume_id}/source"
    stored = await storage.put_if_absent(storage_key, data)
    if stored.fingerprint != fingerprint:
        raise _idempotency_conflict()

    resume = Resume(
        id=resume_id,
        owner_user_id=current_user.id,
        file_name=validated_filename,
        storage_key=storage_key,
        file_size=len(data),
        mime_type=mime_type,
        create_request_fingerprint=fingerprint,
        revision=1,
        parsing_status=ParsingStatus.PENDING.value,
    )
    session.add(resume)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        winner = await session.get(Resume, resume_id, populate_existing=True)
        if winner is None:
            raise
        if winner.create_request_fingerprint != fingerprint:
            raise _idempotency_conflict() from None
        await _dispatch_if_pending(dispatcher, winner)
        return winner
    except SQLAlchemyError:
        await session.rollback()
        raise

    await _dispatch_if_pending(dispatcher, resume)
    return resume


def _visibility_filters(current_user: User) -> list[ColumnElement[bool]]:
    filters: list[ColumnElement[bool]] = [Resume.is_deleted.is_(False)]
    if current_user.role != UserRole.ADMIN.value:
        filters.append(Resume.owner_user_id == current_user.id)
    return filters


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def list_resumes(
    session: AsyncSession,
    *,
    current_user: User,
    keyword: str | None,
    parsing_status: ParsingStatus | None,
    page: int,
    limit: int,
) -> tuple[list[Resume], int]:
    filters = _visibility_filters(current_user)
    if keyword:
        filters.append(Resume.file_name.ilike(f"%{_escape_like(keyword)}%", escape="\\"))
    if parsing_status is not None:
        filters.append(Resume.parsing_status == parsing_status.value)

    total_items = (
        await session.scalar(select(func.count()).select_from(Resume).where(*filters)) or 0
    )
    statement = (
        select(Resume)
        .where(*filters)
        .order_by(Resume.created_at.desc(), Resume.id.desc())
        .offset((page - 1) * limit)
        .limit(limit)
    )
    return list((await session.scalars(statement)).all()), total_items


async def get_resume(
    session: AsyncSession,
    *,
    current_user: User,
    resume_id: uuid.UUID,
) -> Resume:
    resume = await session.scalar(
        select(Resume).where(Resume.id == resume_id, *_visibility_filters(current_user))
    )
    if resume is None:
        raise APIError(404, "RESUME_NOT_FOUND", "Resume not found")
    return resume


async def get_resume_aggregate(
    session: AsyncSession,
    *,
    current_user: User,
    resume_id: uuid.UUID,
) -> ResumeAggregate:
    resume = await get_resume(session, current_user=current_user, resume_id=resume_id)
    profile = await session.scalar(
        select(CandidateProfile).where(CandidateProfile.resume_id == resume.id)
    )
    skills = list(
        (
            await session.scalars(
                select(ResumeSkill)
                .where(ResumeSkill.resume_id == resume.id)
                .order_by(ResumeSkill.id.asc())
            )
        ).all()
    )
    experiences = list(
        (
            await session.scalars(
                select(ResumeExperience)
                .where(ResumeExperience.resume_id == resume.id)
                .order_by(ResumeExperience.id.asc())
            )
        ).all()
    )
    educations = list(
        (
            await session.scalars(
                select(ResumeEducation)
                .where(ResumeEducation.resume_id == resume.id)
                .order_by(ResumeEducation.id.asc())
            )
        ).all()
    )
    return ResumeAggregate(resume, profile, skills, experiences, educations)


async def soft_delete_resume(
    session: AsyncSession,
    *,
    current_user: User,
    resume_id: uuid.UUID,
) -> None:
    resume = await session.scalar(
        select(Resume)
        .where(Resume.id == resume_id, *_visibility_filters(current_user))
        .with_for_update()
    )
    if resume is None:
        raise APIError(404, "RESUME_NOT_FOUND", "Resume not found")

    now = datetime.now(UTC)
    resume.is_deleted = True
    resume.deleted_at = now
    resume.updated_at = now
    await session.commit()
