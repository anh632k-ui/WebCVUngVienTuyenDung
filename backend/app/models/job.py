from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class JobDescription(Base):
    __tablename__ = "job_descriptions"
    __table_args__ = (
        CheckConstraint(
            "create_request_fingerprint ~ '^[0-9a-f]{64}$'",
            name="job_descriptions_create_request_fingerprint_check",
        ),
        CheckConstraint("revision >= 1", name="job_descriptions_revision_check"),
        CheckConstraint(
            "min_experience_years >= 0", name="job_descriptions_min_experience_years_check"
        ),
        CheckConstraint(
            "parsing_status IN ('PENDING','PROCESSING','PARSED','FAILED')",
            name="job_descriptions_parsing_status_check",
        ),
        CheckConstraint("w_skill BETWEEN 0 AND 1", name="job_descriptions_w_skill_check"),
        CheckConstraint("w_semantic BETWEEN 0 AND 1", name="job_descriptions_w_semantic_check"),
        CheckConstraint("w_experience BETWEEN 0 AND 1", name="job_descriptions_w_experience_check"),
        CheckConstraint(
            "status IN ('DRAFT','ACTIVE','CLOSED')", name="job_descriptions_status_check"
        ),
        CheckConstraint("(w_skill+w_semantic+w_experience)=1.000", name="chk_job_weights"),
        CheckConstraint(
            "parsing_status <> 'PARSED' OR (job_embedding IS NOT NULL "
            "AND embedding_model IS NOT NULL AND embedding_preprocessing_version IS NOT NULL "
            "AND parsed_at IS NOT NULL)",
            name="chk_job_parsed_payload",
        ),
        CheckConstraint(
            "(parsing_status='FAILED' AND parsing_error_message IS NOT NULL) OR "
            "(parsing_status<>'FAILED' AND parsing_error_message IS NULL)",
            name="chk_job_error_state",
        ),
        CheckConstraint(
            "status <> 'ACTIVE' OR (parsing_status='PARSED' AND is_criteria_verified=TRUE)",
            name="chk_active_job_ready",
        ),
        CheckConstraint("is_deleted=FALSE OR deleted_at IS NOT NULL", name="chk_job_deleted_at"),
        Index(
            "idx_jobs_recruiter_active",
            "recruiter_id",
            text("created_at DESC"),
            postgresql_where=text("is_deleted=FALSE"),
        ),
        Index(
            "idx_jobs_public_active",
            "status",
            text("created_at DESC"),
            postgresql_where=text("is_deleted=FALSE"),
        ),
        Index(
            "idx_jobs_embedding_hnsw",
            "job_embedding",
            postgresql_using="hnsw",
            postgresql_ops={"job_embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    recruiter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    job_level: Mapped[str] = mapped_column(String(50), nullable=False)
    location: Mapped[str | None] = mapped_column(String(150))
    raw_content: Mapped[str] = mapped_column(Text, nullable=False)
    create_request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"))
    min_experience_years: Mapped[Decimal] = mapped_column(
        Numeric(4, 1), nullable=False, server_default=text("0.0")
    )
    education_requirement: Mapped[str | None] = mapped_column(String(255))
    job_embedding: Mapped[list[float] | None] = mapped_column(Vector(1024))
    embedding_model: Mapped[str | None] = mapped_column(String(100))
    embedding_preprocessing_version: Mapped[str | None] = mapped_column(String(50))
    parsing_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'PENDING'")
    )
    parsing_error_message: Mapped[str | None] = mapped_column(Text)
    is_criteria_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    w_skill: Mapped[Decimal] = mapped_column(
        Numeric(4, 3), nullable=False, server_default=text("0.500")
    )
    w_semantic: Mapped[Decimal] = mapped_column(
        Numeric(4, 3), nullable=False, server_default=text("0.300")
    )
    w_experience: Mapped[Decimal] = mapped_column(
        Numeric(4, 3), nullable=False, server_default=text("0.200")
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'DRAFT'"))
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("FALSE"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
