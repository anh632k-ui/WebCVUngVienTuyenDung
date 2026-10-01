from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class MatchResult(Base):
    __tablename__ = "match_results"
    __table_args__ = (
        CheckConstraint("generation >= 1", name="match_results_generation_check"),
        CheckConstraint("resume_revision >= 1", name="match_results_resume_revision_check"),
        CheckConstraint("job_revision >= 1", name="match_results_job_revision_check"),
        CheckConstraint(
            "status IN ('PENDING','PROCESSING','COMPLETED','FAILED')",
            name="match_results_status_check",
        ),
        UniqueConstraint("job_id", "resume_id", name="uq_job_resume_match"),
        CheckConstraint(
            "(overall_score IS NULL OR overall_score BETWEEN 0 AND 100) AND "
            "(skill_score IS NULL OR skill_score BETWEEN 0 AND 100) AND "
            "(semantic_score IS NULL OR semantic_score BETWEEN 0 AND 100) AND "
            "(experience_score IS NULL OR experience_score BETWEEN 0 AND 100)",
            name="chk_match_scores_range",
        ),
        CheckConstraint(
            "status <> 'COMPLETED' OR (overall_score IS NOT NULL AND skill_score IS NOT NULL "
            "AND semantic_score IS NOT NULL AND experience_score IS NOT NULL "
            "AND embedding_model IS NOT NULL AND embedding_preprocessing_version IS NOT NULL "
            "AND calculated_at IS NOT NULL)",
            name="chk_completed_match_payload",
        ),
        CheckConstraint(
            "status='COMPLETED' OR (overall_score IS NULL AND skill_score IS NULL "
            "AND semantic_score IS NULL AND experience_score IS NULL "
            "AND matched_skills='[]'::jsonb AND missing_skills='[]'::jsonb "
            "AND gap_analysis_summary IS NULL AND embedding_model IS NULL "
            "AND embedding_preprocessing_version IS NULL AND calculated_at IS NULL)",
            name="chk_noncompleted_match_cleared",
        ),
        CheckConstraint(
            "(status='FAILED' AND error_message IS NOT NULL) OR "
            "(status<>'FAILED' AND error_message IS NULL)",
            name="chk_match_error_state",
        ),
        Index(
            "idx_match_job_leaderboard",
            "job_id",
            text("overall_score DESC"),
            postgresql_where=text("status='COMPLETED'"),
        ),
        Index("idx_match_resume", "resume_id", text("created_at DESC")),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("uuid_generate_v4()")
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_descriptions.id", ondelete="CASCADE"), nullable=False
    )
    resume_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resumes.id", ondelete="CASCADE"), nullable=False
    )
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"))
    resume_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    job_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    overall_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    skill_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    semantic_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    experience_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    matched_skills: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    missing_skills: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    gap_analysis_summary: Mapped[str | None] = mapped_column(Text)
    algorithm_version: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default=text("'hybrid-v1'")
    )
    embedding_model: Mapped[str | None] = mapped_column(String(100))
    embedding_preprocessing_version: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'PENDING'")
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    calculated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
