from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db_base import Base


class Skill(Base):
    __tablename__ = "skills"
    __table_args__ = (
        CheckConstraint("skill_kind IN ('HARD','SOFT')", name="skills_skill_kind_check"),
        Index("idx_skills_kind_category", "skill_kind", "category"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    normalized_name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    skill_kind: Mapped[str] = mapped_column(String(10), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class ResumeSkill(Base):
    __tablename__ = "resume_skills"
    __table_args__ = (
        UniqueConstraint("resume_id", "skill_id", name="uq_resume_skill"),
        CheckConstraint(
            "years_of_experience IS NULL OR years_of_experience >= 0",
            name="chk_resume_skill_years",
        ),
        Index("idx_resume_skills_skill", "skill_id", "resume_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    resume_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resumes.id", ondelete="CASCADE"), nullable=False
    )
    skill_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("skills.id", ondelete="RESTRICT"), nullable=False
    )
    years_of_experience: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    proficiency_level: Mapped[str | None] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class JobSkill(Base):
    __tablename__ = "job_skills"
    __table_args__ = (
        CheckConstraint(
            "importance IN ('MANDATORY','OPTIONAL')", name="job_skills_importance_check"
        ),
        CheckConstraint("min_years_required >= 0", name="job_skills_min_years_required_check"),
        UniqueConstraint("job_id", "skill_id", name="uq_job_skill"),
        Index("idx_job_skills_skill", "skill_id", "job_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_descriptions.id", ondelete="CASCADE"), nullable=False
    )
    skill_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("skills.id", ondelete="RESTRICT"), nullable=False
    )
    importance: Mapped[str] = mapped_column(String(20), nullable=False)
    min_years_required: Mapped[Decimal] = mapped_column(
        Numeric(4, 1), nullable=False, server_default=text("0.0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
