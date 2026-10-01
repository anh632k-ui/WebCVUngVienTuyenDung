from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Numeric

import app.models  # noqa: F401 - registers every canonical mapping
from app.core.database import Base

EXPECTED_COLUMNS = {
    "users": {
        "id",
        "email",
        "password_hash",
        "full_name",
        "phone_number",
        "role",
        "is_active",
        "created_at",
        "updated_at",
    },
    "skills": {
        "id",
        "name",
        "normalized_name",
        "skill_kind",
        "category",
        "description",
        "created_at",
        "updated_at",
    },
    "resumes": {
        "id",
        "owner_user_id",
        "file_name",
        "storage_key",
        "file_size",
        "mime_type",
        "create_request_fingerprint",
        "revision",
        "parsing_status",
        "raw_text",
        "resume_embedding",
        "embedding_model",
        "embedding_preprocessing_version",
        "error_message",
        "is_manually_edited",
        "is_deleted",
        "created_at",
        "updated_at",
        "parsed_at",
        "deleted_at",
    },
    "candidate_profiles": {
        "id",
        "resume_id",
        "full_name",
        "email",
        "phone_number",
        "current_title",
        "location",
        "linkedin_url",
        "github_url",
        "professional_summary",
        "created_at",
        "updated_at",
    },
    "resume_skills": {
        "id",
        "resume_id",
        "skill_id",
        "years_of_experience",
        "proficiency_level",
        "created_at",
        "updated_at",
    },
    "resume_experiences": {
        "id",
        "resume_id",
        "company_name",
        "job_title",
        "start_date",
        "end_date",
        "is_current",
        "description",
        "created_at",
        "updated_at",
    },
    "resume_educations": {
        "id",
        "resume_id",
        "institution_name",
        "degree",
        "field_of_study",
        "start_year",
        "graduation_year",
        "gpa",
        "description",
        "created_at",
        "updated_at",
    },
    "job_descriptions": {
        "id",
        "recruiter_id",
        "title",
        "job_level",
        "location",
        "raw_content",
        "create_request_fingerprint",
        "revision",
        "min_experience_years",
        "education_requirement",
        "job_embedding",
        "embedding_model",
        "embedding_preprocessing_version",
        "parsing_status",
        "parsing_error_message",
        "is_criteria_verified",
        "w_skill",
        "w_semantic",
        "w_experience",
        "status",
        "is_deleted",
        "created_at",
        "updated_at",
        "parsed_at",
        "deleted_at",
    },
    "job_skills": {
        "id",
        "job_id",
        "skill_id",
        "importance",
        "min_years_required",
        "created_at",
        "updated_at",
    },
    "match_results": {
        "id",
        "job_id",
        "resume_id",
        "generation",
        "resume_revision",
        "job_revision",
        "overall_score",
        "skill_score",
        "semantic_score",
        "experience_score",
        "matched_skills",
        "missing_skills",
        "gap_analysis_summary",
        "algorithm_version",
        "embedding_model",
        "embedding_preprocessing_version",
        "status",
        "error_message",
        "created_at",
        "updated_at",
        "calculated_at",
    },
}


def test_metadata_maps_exactly_the_ten_canonical_tables_and_columns() -> None:
    assert set(Base.metadata.tables) == set(EXPECTED_COLUMNS)
    for table_name, expected_columns in EXPECTED_COLUMNS.items():
        assert set(Base.metadata.tables[table_name].columns.keys()) == expected_columns


def test_revision_generation_and_vector_types_match_schema() -> None:
    resumes = Base.metadata.tables["resumes"]
    jobs = Base.metadata.tables["job_descriptions"]
    matches = Base.metadata.tables["match_results"]

    assert isinstance(resumes.c.revision.type, BigInteger)
    assert isinstance(jobs.c.revision.type, BigInteger)
    assert isinstance(matches.c.generation.type, BigInteger)
    assert isinstance(resumes.c.resume_embedding.type, Vector)
    assert resumes.c.resume_embedding.type.dim == 1024
    assert isinstance(jobs.c.job_embedding.type, Vector)
    assert jobs.c.job_embedding.type.dim == 1024


def test_canonical_numeric_precision_and_foreign_key_delete_rules() -> None:
    jobs = Base.metadata.tables["job_descriptions"]
    matches = Base.metadata.tables["match_results"]

    assert isinstance(jobs.c.w_skill.type, Numeric)
    assert (jobs.c.w_skill.type.precision, jobs.c.w_skill.type.scale) == (4, 3)
    assert (matches.c.overall_score.type.precision, matches.c.overall_score.type.scale) == (5, 2)

    for table in Base.metadata.tables.values():
        for foreign_key in table.foreign_keys:
            expected = "RESTRICT" if foreign_key.column.table.name == "skills" else "CASCADE"
            assert foreign_key.ondelete == expected


def test_foundation_never_calls_create_all() -> None:
    assert not hasattr(Base, "create_all")
