-- 1. Kích hoạt Extension pgvector và pgcrypto
-- CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
-- CREATE EXTENSION IF NOT EXISTS "vector";

-- 2. Bảng Users
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    full_name VARCHAR(100) NOT NULL,
    phone_number VARCHAR(20),
    role VARCHAR(20) NOT NULL CHECK (role IN ('CANDIDATE', 'HR', 'ADMIN')),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX idx_users_email ON users(email);

-- 3. Bảng Skills (Từ điển kỹ năng)
CREATE TABLE skills (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) UNIQUE NOT NULL,
    normalized_name VARCHAR(100) UNIQUE NOT NULL,
    category VARCHAR(50) NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_skills_normalized ON skills(normalized_name);

-- 4. Bảng Resumes
CREATE TABLE resumes (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    file_name VARCHAR(255) NOT NULL,
    file_path VARCHAR(500) NOT NULL,
    file_size INTEGER NOT NULL,
    mime_type VARCHAR(100) NOT NULL,
    parsing_status VARCHAR(20) NOT NULL DEFAULT 'PENDING' 
        CHECK (parsing_status IN ('PENDING', 'PROCESSING', 'PARSED', 'FAILED')),
    raw_text TEXT,
    resume_embedding vector(1024),
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    parsed_at TIMESTAMPTZ
);

CREATE INDEX idx_resumes_user_id ON resumes(user_id);
-- Chỉ mục HNSW cho vector embedding của CV
CREATE INDEX idx_resumes_embedding ON resumes USING hnsw (resume_embedding vector_cosine_ops);

-- 5. Bảng Resume Skills
CREATE TABLE resume_skills (
    id BIGSERIAL PRIMARY KEY,
    resume_id UUID NOT NULL REFERENCES resumes(id) ON DELETE CASCADE,
    skill_id INTEGER NOT NULL REFERENCES skills(id) ON DELETE RESTRICT,
    years_of_experience NUMERIC(3,1),
    proficiency_level VARCHAR(20),
    CONSTRAINT uq_resume_skill UNIQUE (resume_id, skill_id)
);

-- 6. Bảng Resume Experiences
CREATE TABLE resume_experiences (
    id BIGSERIAL PRIMARY KEY,
    resume_id UUID NOT NULL REFERENCES resumes(id) ON DELETE CASCADE,
    company_name VARCHAR(150) NOT NULL,
    job_title VARCHAR(100) NOT NULL,
    start_date DATE,
    end_date DATE,
    is_current BOOLEAN DEFAULT FALSE,
    description TEXT
);

-- 7. Bảng Resume Educations
CREATE TABLE resume_educations (
    id BIGSERIAL PRIMARY KEY,
    resume_id UUID NOT NULL REFERENCES resumes(id) ON DELETE CASCADE,
    institution_name VARCHAR(150) NOT NULL,
    degree VARCHAR(100),
    field_of_study VARCHAR(100),
    graduation_year SMALLINT,
    gpa NUMERIC(3,2)
);

-- 8. Bảng Job Descriptions
CREATE TABLE job_descriptions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    recruiter_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title VARCHAR(200) NOT NULL,
    job_level VARCHAR(50) NOT NULL,
    location VARCHAR(150),
    raw_content TEXT NOT NULL,
    min_experience_years NUMERIC(3,1) DEFAULT 0.0,
    job_embedding vector(1024),
    w_skill NUMERIC(3,2) NOT NULL DEFAULT 0.50 CHECK (w_skill >= 0),
    w_semantic NUMERIC(3,2) NOT NULL DEFAULT 0.30 CHECK (w_semantic >= 0),
    w_experience NUMERIC(3,2) NOT NULL DEFAULT 0.20 CHECK (w_experience >= 0),
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('DRAFT', 'ACTIVE', 'CLOSED')),
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_total_weights CHECK ((w_skill + w_semantic + w_experience) = 1.00)
);

CREATE INDEX idx_jobs_recruiter ON job_descriptions(recruiter_id);
-- Chỉ mục HNSW cho vector embedding của JD
CREATE INDEX idx_jobs_embedding ON job_descriptions USING hnsw (job_embedding vector_cosine_ops);

-- 9. Bảng Job Skills
CREATE TABLE job_skills (
    id BIGSERIAL PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES job_descriptions(id) ON DELETE CASCADE,
    skill_id INTEGER NOT NULL REFERENCES skills(id) ON DELETE RESTRICT,
    skill_type VARCHAR(20) NOT NULL CHECK (skill_type IN ('MANDATORY', 'OPTIONAL')),
    min_years_required NUMERIC(3,1) DEFAULT 0.0,
    CONSTRAINT uq_job_skill UNIQUE (job_id, skill_id)
);

-- 10. Bảng Match Results
CREATE TABLE match_results (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    job_id UUID NOT NULL REFERENCES job_descriptions(id) ON DELETE CASCADE,
    resume_id UUID NOT NULL REFERENCES resumes(id) ON DELETE CASCADE,
    overall_score NUMERIC(5,2) NOT NULL CHECK (overall_score BETWEEN 0 AND 100),
    skill_score NUMERIC(5,2) NOT NULL CHECK (skill_score BETWEEN 0 AND 100),
    semantic_score NUMERIC(5,2) NOT NULL CHECK (semantic_score BETWEEN 0 AND 100),
    experience_score NUMERIC(5,2) NOT NULL CHECK (experience_score BETWEEN 0 AND 100),
    matched_skills JSONB DEFAULT '[]'::jsonb,
    missing_skills JSONB DEFAULT '[]'::jsonb,
    gap_analysis_summary TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'COMPLETED' 
        CHECK (status IN ('PENDING', 'PROCESSING', 'COMPLETED', 'FAILED')),
    calculated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_job_resume_match UNIQUE (job_id, resume_id)
);

-- Tối ưu hóa truy vấn Leaderboard xếp hạng ứng viên theo JD
CREATE INDEX idx_match_job_score ON match_results(job_id, overall_score DESC);