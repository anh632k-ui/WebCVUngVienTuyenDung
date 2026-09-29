-- =============================================================
-- WebCVUngVienTuyenDung - Canonical Physical Schema
-- Target: PostgreSQL 18 + pgvector
-- Generated from PTTK, not from the previous experimental database.
-- =============================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;

-- =============================================================
-- 1. USERS
-- =============================================================
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    full_name VARCHAR(100) NOT NULL,
    phone_number VARCHAR(20),
    role VARCHAR(20) NOT NULL
        CHECK (role IN ('CANDIDATE', 'HR', 'ADMIN')),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Application layer MUST normalize email to lowercase before persistence.

-- =============================================================
-- 2. SKILLS - Skill Taxonomy
-- =============================================================
CREATE TABLE skills (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    normalized_name VARCHAR(100) NOT NULL UNIQUE,
    skill_kind VARCHAR(10) NOT NULL
        CHECK (skill_kind IN ('HARD', 'SOFT')),
    category VARCHAR(50) NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_skills_kind_category
    ON skills(skill_kind, category);

-- =============================================================
-- 3. RESUMES
-- owner_user_id = Candidate self CV OR HR talent-pool owner.
-- =============================================================
CREATE TABLE resumes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    owner_user_id UUID NOT NULL
        REFERENCES users(id) ON DELETE CASCADE,
    file_name VARCHAR(255) NOT NULL,
    storage_key VARCHAR(500) NOT NULL UNIQUE,
    file_size INTEGER NOT NULL
        CHECK (file_size > 0 AND file_size <= 5242880),
    mime_type VARCHAR(100) NOT NULL,
    parsing_status VARCHAR(20) NOT NULL DEFAULT 'PENDING'
        CHECK (parsing_status IN ('PENDING', 'PROCESSING', 'PARSED', 'FAILED')),
    raw_text TEXT,
    resume_embedding vector(1024),
    embedding_model VARCHAR(100),
    error_message TEXT,
    is_manually_edited BOOLEAN NOT NULL DEFAULT FALSE,
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    parsed_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ,
    CONSTRAINT chk_resume_parsed_payload CHECK (
        parsing_status <> 'PARSED'
        OR (raw_text IS NOT NULL AND parsed_at IS NOT NULL)
    )
);

CREATE INDEX idx_resumes_owner_active
    ON resumes(owner_user_id, created_at DESC)
    WHERE is_deleted = FALSE;

CREATE INDEX idx_resumes_parsing_status
    ON resumes(parsing_status)
    WHERE is_deleted = FALSE;

CREATE INDEX idx_resumes_embedding_hnsw
    ON resumes USING hnsw (resume_embedding vector_cosine_ops);

-- =============================================================
-- 4. CANDIDATE_PROFILES
-- Snapshot of professional/contact information extracted from ONE CV.
-- This is deliberately separate from users account profile.
-- =============================================================
CREATE TABLE candidate_profiles (
    id BIGSERIAL PRIMARY KEY,
    resume_id UUID NOT NULL UNIQUE
        REFERENCES resumes(id) ON DELETE CASCADE,
    full_name VARCHAR(150),
    email VARCHAR(255),
    phone_number VARCHAR(30),
    current_title VARCHAR(150),
    location VARCHAR(150),
    linkedin_url VARCHAR(500),
    github_url VARCHAR(500),
    professional_summary TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================
-- 5. RESUME_SKILLS
-- =============================================================
CREATE TABLE resume_skills (
    id BIGSERIAL PRIMARY KEY,
    resume_id UUID NOT NULL
        REFERENCES resumes(id) ON DELETE CASCADE,
    skill_id INTEGER NOT NULL
        REFERENCES skills(id) ON DELETE RESTRICT,
    years_of_experience NUMERIC(4,1),
    proficiency_level VARCHAR(30),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_resume_skill UNIQUE (resume_id, skill_id),
    CONSTRAINT chk_resume_skill_years CHECK (
        years_of_experience IS NULL OR years_of_experience >= 0
    )
);

CREATE INDEX idx_resume_skills_skill
    ON resume_skills(skill_id, resume_id);

-- =============================================================
-- 6. RESUME_EXPERIENCES
-- =============================================================
CREATE TABLE resume_experiences (
    id BIGSERIAL PRIMARY KEY,
    resume_id UUID NOT NULL
        REFERENCES resumes(id) ON DELETE CASCADE,
    company_name VARCHAR(150) NOT NULL,
    job_title VARCHAR(150) NOT NULL,
    start_date DATE,
    end_date DATE,
    is_current BOOLEAN NOT NULL DEFAULT FALSE,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_experience_dates CHECK (
        end_date IS NULL OR start_date IS NULL OR end_date >= start_date
    ),
    CONSTRAINT chk_current_no_end_date CHECK (
        is_current = FALSE OR end_date IS NULL
    )
);

CREATE INDEX idx_resume_experiences_resume
    ON resume_experiences(resume_id, start_date DESC);

-- =============================================================
-- 7. RESUME_EDUCATIONS
-- =============================================================
CREATE TABLE resume_educations (
    id BIGSERIAL PRIMARY KEY,
    resume_id UUID NOT NULL
        REFERENCES resumes(id) ON DELETE CASCADE,
    institution_name VARCHAR(150) NOT NULL,
    degree VARCHAR(100),
    field_of_study VARCHAR(150),
    start_year SMALLINT,
    graduation_year SMALLINT,
    gpa NUMERIC(4,2),
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_education_years CHECK (
        graduation_year IS NULL OR start_year IS NULL OR graduation_year >= start_year
    )
);

CREATE INDEX idx_resume_educations_resume
    ON resume_educations(resume_id, graduation_year DESC);

-- =============================================================
-- 8. JOB_DESCRIPTIONS
-- business status and NLP parsing status are deliberately separate.
-- =============================================================
CREATE TABLE job_descriptions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    recruiter_id UUID NOT NULL
        REFERENCES users(id) ON DELETE CASCADE,
    title VARCHAR(200) NOT NULL,
    job_level VARCHAR(50) NOT NULL,
    location VARCHAR(150),
    raw_content TEXT NOT NULL,
    min_experience_years NUMERIC(4,1) NOT NULL DEFAULT 0.0
        CHECK (min_experience_years >= 0),
    education_requirement VARCHAR(255),
    job_embedding vector(1024),
    embedding_model VARCHAR(100),
    parsing_status VARCHAR(20) NOT NULL DEFAULT 'PENDING'
        CHECK (parsing_status IN ('PENDING', 'PROCESSING', 'PARSED', 'FAILED')),
    parsing_error_message TEXT,
    is_criteria_verified BOOLEAN NOT NULL DEFAULT FALSE,
    w_skill NUMERIC(4,3) NOT NULL DEFAULT 0.500
        CHECK (w_skill BETWEEN 0 AND 1),
    w_semantic NUMERIC(4,3) NOT NULL DEFAULT 0.300
        CHECK (w_semantic BETWEEN 0 AND 1),
    w_experience NUMERIC(4,3) NOT NULL DEFAULT 0.200
        CHECK (w_experience BETWEEN 0 AND 1),
    status VARCHAR(20) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'ACTIVE', 'CLOSED')),
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    parsed_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ,
    CONSTRAINT chk_job_weights CHECK (
        (w_skill + w_semantic + w_experience) = 1.000
    ),
    CONSTRAINT chk_active_job_is_parsed CHECK (
        status <> 'ACTIVE' OR parsing_status = 'PARSED'
    ),
    CONSTRAINT chk_job_parsed_timestamp CHECK (
        parsing_status <> 'PARSED' OR parsed_at IS NOT NULL
    )
);

CREATE INDEX idx_jobs_recruiter_active
    ON job_descriptions(recruiter_id, created_at DESC)
    WHERE is_deleted = FALSE;

CREATE INDEX idx_jobs_public_active
    ON job_descriptions(status, created_at DESC)
    WHERE is_deleted = FALSE;

CREATE INDEX idx_jobs_embedding_hnsw
    ON job_descriptions USING hnsw (job_embedding vector_cosine_ops);

-- =============================================================
-- 9. JOB_SKILLS
-- importance is different from skills.skill_kind.
-- =============================================================
CREATE TABLE job_skills (
    id BIGSERIAL PRIMARY KEY,
    job_id UUID NOT NULL
        REFERENCES job_descriptions(id) ON DELETE CASCADE,
    skill_id INTEGER NOT NULL
        REFERENCES skills(id) ON DELETE RESTRICT,
    importance VARCHAR(20) NOT NULL
        CHECK (importance IN ('MANDATORY', 'OPTIONAL')),
    min_years_required NUMERIC(4,1) NOT NULL DEFAULT 0.0
        CHECK (min_years_required >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_job_skill UNIQUE (job_id, skill_id)
);

CREATE INDEX idx_job_skills_skill
    ON job_skills(skill_id, job_id);

-- =============================================================
-- 10. MATCH_RESULTS
-- Scores are NULL while a task is pending/processing/failed.
-- =============================================================
CREATE TABLE match_results (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    job_id UUID NOT NULL
        REFERENCES job_descriptions(id) ON DELETE CASCADE,
    resume_id UUID NOT NULL
        REFERENCES resumes(id) ON DELETE CASCADE,
    overall_score NUMERIC(5,2),
    skill_score NUMERIC(5,2),
    semantic_score NUMERIC(5,2),
    experience_score NUMERIC(5,2),
    matched_skills JSONB NOT NULL DEFAULT '[]'::jsonb,
    missing_skills JSONB NOT NULL DEFAULT '[]'::jsonb,
    gap_analysis_summary TEXT,
    algorithm_version VARCHAR(50) NOT NULL DEFAULT 'hybrid-v1',
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'PROCESSING', 'COMPLETED', 'FAILED')),
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    calculated_at TIMESTAMPTZ,
    CONSTRAINT uq_job_resume_match UNIQUE (job_id, resume_id),
    CONSTRAINT chk_match_scores_range CHECK (
        (overall_score IS NULL OR overall_score BETWEEN 0 AND 100)
        AND (skill_score IS NULL OR skill_score BETWEEN 0 AND 100)
        AND (semantic_score IS NULL OR semantic_score BETWEEN 0 AND 100)
        AND (experience_score IS NULL OR experience_score BETWEEN 0 AND 100)
    ),
    CONSTRAINT chk_completed_match_payload CHECK (
        status <> 'COMPLETED'
        OR (
            overall_score IS NOT NULL
            AND skill_score IS NOT NULL
            AND semantic_score IS NOT NULL
            AND experience_score IS NOT NULL
            AND calculated_at IS NOT NULL
        )
    )
);

CREATE INDEX idx_match_job_leaderboard
    ON match_results(job_id, overall_score DESC)
    WHERE status = 'COMPLETED';

CREATE INDEX idx_match_resume
    ON match_results(resume_id, created_at DESC);

-- =============================================================
-- Notes
-- =============================================================
-- 1. updated_at is updated by application/service layer in MVP.
-- 2. Soft-deleted Resume/JD is filtered by service queries.
-- 3. Redis session data is intentionally NOT modeled as a PostgreSQL table.
-- 4. LLM/XAI output is not persisted as a required table in MVP; it may be
--    generated on demand from deterministic matching evidence.
