from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db_base import Base


class Resume(Base):
    __tablename__ = "resumes"
    __table_args__ = (
        CheckConstraint("file_size > 0 AND file_size <= 5242880", name="resumes_file_size_check"),
        CheckConstraint(
            "create_request_fingerprint ~ '^[0-9a-f]{64}$'",
            name="resumes_create_request_fingerprint_check",
        ),
        CheckConstraint("revision >= 1", name="resumes_revision_check"),
        CheckConstraint(
            "parsing_status IN ('PENDING','PROCESSING','PARSED','FAILED')",
            name="resumes_parsing_status_check",
        ),
        CheckConstraint(
            "parsing_status <> 'PARSED' OR (raw_text IS NOT NULL AND resume_embedding IS NOT NULL "
            "AND embedding_model IS NOT NULL AND embedding_preprocessing_version IS NOT NULL "
            "AND parsed_at IS NOT NULL)",
            name="chk_resume_parsed_payload",
        ),
        CheckConstraint(
            "(parsing_status = 'FAILED' AND error_message IS NOT NULL) OR "
            "(parsing_status <> 'FAILED' AND error_message IS NULL)",
            name="chk_resume_error_state",
        ),
        CheckConstraint(
            "is_deleted = FALSE OR deleted_at IS NOT NULL", name="chk_resume_deleted_at"
        ),
        Index(
            "idx_resumes_owner_active",
            "owner_user_id",
            text("created_at DESC"),
            postgresql_where=text("is_deleted=FALSE"),
        ),
        Index(
            "idx_resumes_parsing_status",
            "parsing_status",
            postgresql_where=text("is_deleted=FALSE"),
        ),
        Index(
            "idx_resumes_embedding_hnsw",
            "resume_embedding",
            postgresql_using="hnsw",
            postgresql_ops={"resume_embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    create_request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"))
    parsing_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'PENDING'")
    )
    raw_text: Mapped[str | None] = mapped_column(Text)
    resume_embedding: Mapped[list[float] | None] = mapped_column(Vector(1024))
    embedding_model: Mapped[str | None] = mapped_column(String(100))
    embedding_preprocessing_version: Mapped[str | None] = mapped_column(String(50))
    error_message: Mapped[str | None] = mapped_column(Text)
    is_manually_edited: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("FALSE"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CandidateProfile(Base):
    __tablename__ = "candidate_profiles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    resume_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("resumes.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    full_name: Mapped[str | None] = mapped_column(String(150))
    email: Mapped[str | None] = mapped_column(String(255))
    phone_number: Mapped[str | None] = mapped_column(String(30))
    current_title: Mapped[str | None] = mapped_column(String(150))
    location: Mapped[str | None] = mapped_column(String(150))
    linkedin_url: Mapped[str | None] = mapped_column(String(500))
    github_url: Mapped[str | None] = mapped_column(String(500))
    professional_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class ResumeExperience(Base):
    __tablename__ = "resume_experiences"
    __table_args__ = (
        CheckConstraint(
            "end_date IS NULL OR start_date IS NULL OR end_date >= start_date",
            name="chk_experience_dates",
        ),
        CheckConstraint("is_current=FALSE OR end_date IS NULL", name="chk_current_no_end_date"),
        Index("idx_resume_experiences_resume", "resume_id", text("start_date DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    resume_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resumes.id", ondelete="CASCADE"), nullable=False
    )
    company_name: Mapped[str] = mapped_column(String(150), nullable=False)
    job_title: Mapped[str] = mapped_column(String(150), nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("FALSE"))
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class ResumeEducation(Base):
    __tablename__ = "resume_educations"
    __table_args__ = (
        CheckConstraint(
            "graduation_year IS NULL OR start_year IS NULL OR graduation_year >= start_year",
            name="chk_education_years",
        ),
        Index("idx_resume_educations_resume", "resume_id", text("graduation_year DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    resume_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resumes.id", ondelete="CASCADE"), nullable=False
    )
    institution_name: Mapped[str] = mapped_column(String(150), nullable=False)
    degree: Mapped[str | None] = mapped_column(String(100))
    field_of_study: Mapped[str | None] = mapped_column(String(150))
    start_year: Mapped[int | None] = mapped_column(SmallInteger)
    graduation_year: Mapped[int | None] = mapped_column(SmallInteger)
    gpa: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
