"""Canonical ORM mappings.

Importing this package registers all ten existing database tables with Base.metadata.
It never creates, migrates, or modifies the database schema.
"""

from app.models.job import JobDescription
from app.models.match_result import MatchResult
from app.models.resume import CandidateProfile, Resume, ResumeEducation, ResumeExperience
from app.models.skill import JobSkill, ResumeSkill, Skill
from app.models.user import User

__all__ = [
    "CandidateProfile",
    "JobDescription",
    "JobSkill",
    "MatchResult",
    "Resume",
    "ResumeEducation",
    "ResumeExperience",
    "ResumeSkill",
    "Skill",
    "User",
]
